"""Event-scoped admission, cancellations, role management and real-data reports."""
import hashlib,json,io
import qrcode
from datetime import datetime,timedelta
from flask import request,jsonify,send_file
from client_updates import now


def install(app,db,auth,user,one,rows,fail,notice,limited):
    with app.app_context():
        columns=[r[1] for r in db().execute('PRAGMA table_info(users)')]
        for name,definition in [('disabled','INTEGER NOT NULL DEFAULT 0'),('created','TEXT')]:
            if name not in columns:db().execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')
        db().executescript('''CREATE TRIGGER IF NOT EXISTS user_timestamp AFTER INSERT ON users BEGIN
          UPDATE users SET created=strftime('%Y-%m-%dT%H:%M:%S','now','+330 minutes') WHERE id=NEW.id; END;
          CREATE TABLE IF NOT EXISTS admission_audit(id INTEGER PRIMARY KEY,actor INTEGER,event INTEGER,result TEXT,token_hash TEXT,created TEXT);
          CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY,actor INTEGER,action TEXT,target INTEGER,created TEXT);
        ''');db().commit()

    @auth(['organizer','admin'])
    def checkin_secure():
        d=request.json;eid=d.get('event');token=d.get('token','');u=user()
        if type(eid)!=int or not isinstance(token,str) or not 1<=len(token)<=200:return fail('Choose an event and supply the QR ticket token.')
        if limited('scan:'+str(u['id']),120,60):return jsonify(error='Scanner rate limit reached.'),429
        db().execute('BEGIN IMMEDIATE')
        e=one('SELECT * FROM events WHERE id=?',(eid,))
        if not e or (u['role']!='admin' and (e['owner']!=u['id'] or not u['approved'])):db().rollback();return jsonify(error='You cannot validate tickets for this event.'),403
        b=one('SELECT * FROM bookings WHERE event=? AND token=?',(eid,token))
        status='invalid';message='Invalid ticket for the selected event.'
        if b:
            if b['status']!='confirmed' or e['status']=='cancelled':status='cancelled';message='Cancelled ticket. Entry denied.'
            elif b['used']:status='already_used';message='Already used. Entry denied.'
            elif e['status']!='approved':message='Event is not published. Entry denied.'
            elif datetime.fromisoformat(e['date'])+timedelta(minutes=e['duration_minutes'])<=now():message='This event has ended. Entry denied.'
            else:
                changed=db().execute("UPDATE bookings SET used=1 WHERE id=? AND used=0 AND status='confirmed'",(b['id'],)).rowcount
                if changed:status='valid';message=f"Valid ticket: admit {b['qty']} guest(s)."
        db().execute('INSERT INTO admission_audit(actor,event,result,token_hash,created) VALUES(?,?,?,?,?)',(u['id'],eid,status,hashlib.sha256(token.encode()).hexdigest(),now().isoformat()));db().commit()
        return jsonify(status=status,message=message,**({'booking':b['id'],'qty':b['qty']} if status=='valid' else {'error':message})),200 if status=='valid' else 400
    app.add_url_rule('/api/checkin',endpoint='checkin',view_func=checkin_secure,methods=['POST'])

    def cancel_booking(b,reason):
        if b['status']!='confirmed':return False
        db().execute("UPDATE bookings SET status='cancelled' WHERE id=?",(b['id'],))
        adjustment=b['redeemed']-b['earned']
        db().execute('UPDATE users SET points=points+? WHERE id=?',(adjustment,b['user']))
        if adjustment:db().execute('INSERT INTO ledger(user,amount,label) VALUES(?,?,?)',(b['user'],adjustment,'Cancellation adjustment #'+str(b['id'])))
        db().execute("DELETE FROM interactions WHERE user=? AND dedupe=?",(b['user'],'booking:'+str(b['id'])))
        db().execute('DELETE FROM seat_claims WHERE booking=?',(b['id'],))
        db().execute("UPDATE razorpay_payments SET status='refund_required',reason=? WHERE booking=? AND status='approved'",(reason,b['id']))
        db().execute("UPDATE demo_payments SET status='cancelled',reason=? WHERE booking=?",(reason,b['id']))
        notice(b['user'],'Booking #'+str(b['id'])+' cancelled. '+reason+' Any captured payment requires a refund; check My orders for status.')
        return True

    @app.post('/api/bookings/<int:bid>/cancel')
    @auth()
    def booking_cancel(bid):
        db().execute('BEGIN IMMEDIATE');b=one('SELECT * FROM bookings WHERE id=? AND user=?',(bid,user()['id']))
        if not b:db().rollback();return jsonify(error='Booking unavailable.'),404
        if b['status']=='cancelled':db().rollback();return jsonify(message='Booking already cancelled.')
        e=one('SELECT * FROM events WHERE id=?',(b['event'],))
        if b['used'] or e['date']<=now().isoformat():db().rollback();return fail('Used tickets and started events cannot be cancelled.')
        cancel_booking(b,'Cancelled by attendee.');notice(e['owner'],'Booking #'+str(bid)+' cancelled by attendee. Seats released.');db().commit()
        return jsonify(message='Booking cancelled and seats released. Any captured payment is marked for refund; no refund is claimed until provider confirmation.')

    @app.post('/api/events/<int:eid>/cancel')
    @auth(['organizer','admin'])
    def event_cancel(eid):
        reason=request.json.get('reason','').strip()
        if not 5<=len(reason)<=500:return fail('Provide a cancellation reason (5–500 characters).')
        db().execute('BEGIN IMMEDIATE');e=one('SELECT * FROM events WHERE id=?',(eid,));u=user()
        if not e or (u['role']!='admin' and (e['owner']!=u['id'] or not u['approved'])):db().rollback();return jsonify(error='Event unavailable for this account.'),403
        if e['status']=='cancelled':db().rollback();return jsonify(message='Event already cancelled.')
        if e['date']<=now().isoformat():db().rollback();return fail('Started events cannot be cancelled here.')
        db().execute("UPDATE events SET status='cancelled',reason=? WHERE id=?",(reason,eid))
        for b in rows("SELECT * FROM bookings WHERE event=? AND status='confirmed'",(eid,)):cancel_booking(b,'Event cancelled: '+reason)
        for table,provider in [('demo_payments','demo'),('razorpay_payments','razorpay')]:
            for p in rows(f"SELECT * FROM {table} WHERE event=? AND status IN ('pending','creating','created','authorized','paid_pending')",(eid,)):
                state='refund_required' if p['status']=='paid_pending' else 'cancelled'
                db().execute(f'UPDATE {table} SET status=?,reason=? WHERE id=?',(state,reason,p['id']));notice(p['user'],'Event cancelled: '+e['title']+'. '+reason)
            db().execute('DELETE FROM seat_holds WHERE provider=? AND event=?',(provider,eid))
        db().commit();return jsonify(message='Event cancelled. Reservations released, tickets invalidated and captured payments flagged for refund.')

    @app.post('/api/checkout/cancel')
    @auth()
    def checkout_cancel():
        d=request.json;provider=d.get('provider');table={'demo':'demo_payments','razorpay':'razorpay_payments'}.get(provider)
        if not table:return fail('Choose a payment provider.')
        db().execute('BEGIN IMMEDIATE');p=one(f'SELECT * FROM {table} WHERE id=? AND user=?',(d.get('id'),user()['id']))
        if not p:db().rollback();return fail('Payment unavailable.')
        if p['status']=='cancelled':db().rollback();return jsonify(message='Checkout already cancelled.')
        if p['status'] not in ('pending','created','authorized','failed','paid_pending'):db().rollback();return fail('This payment requires booking cancellation or payment reconciliation.')
        state='refund_required' if p['status']=='paid_pending' else 'cancelled'
        db().execute(f'UPDATE {table} SET status=?,reason=? WHERE id=?',(state,'Cancelled by attendee.',p['id']))
        db().execute('DELETE FROM seat_holds WHERE provider=? AND payment=?',(provider,p['id']));db().commit()
        return jsonify(message='Reservation cancelled. Seats released. Any captured or late-arriving payment requires a refund.',status=state)

    @app.get('/api/ticket-qr/<int:bid>')
    @auth()
    def ticket_qr(bid):
        b=one("SELECT token FROM bookings WHERE id=? AND user=? AND status='confirmed'",(bid,user()['id']))
        if not b:return jsonify(error='Ticket unavailable or cancelled.'),404
        output=io.BytesIO();qrcode.make(b['token']).save(output,format='PNG');output.seek(0)
        return send_file(output,mimetype='image/png',download_name=f'EventAI-ticket-{bid}.png')

    @app.get('/api/tickets/<int:bid>')
    @auth()
    def ticket_details(bid):
        b=one('SELECT b.*,e.title,e.date,e.duration_minutes,e.venue,e.city FROM bookings b JOIN events e ON e.id=b.event WHERE b.id=? AND b.user=?',(bid,user()['id']))
        if not b:return jsonify(error='Ticket unavailable.'),404
        payment=one("SELECT seat_json FROM demo_payments WHERE booking=? UNION ALL SELECT seat_json FROM razorpay_payments WHERE booking=?",(bid,bid))
        ids=json.loads(payment['seat_json']) if payment else []
        b['seats']=rows('SELECT section,row_label,number,price FROM event_seats WHERE id IN ('+','.join('?' for _ in ids)+')',ids) if ids else []
        if b['status']!='confirmed':b.pop('token',None)
        return jsonify(b)

    @app.get('/api/management/bookings')
    @auth(['organizer','admin'])
    def managed_bookings():
        scope='1=1' if user()['role']=='admin' else 'e.owner=?';args=() if user()['role']=='admin' else (user()['id'],)
        return jsonify(rows(f'''SELECT b.id,b.event,b.qty,b.total,b.status,b.used,b.created,e.title,u.name,u.email,
          (SELECT GROUP_CONCAT(s.row_label||s.number,', ') FROM seat_claims c JOIN event_seats s ON s.id=c.seat WHERE c.booking=b.id) seats
          FROM bookings b JOIN events e ON e.id=b.event JOIN users u ON u.id=b.user WHERE {scope} ORDER BY b.id DESC LIMIT 1000''',args))

    @app.route('/api/admin/users',methods=['GET','POST'])
    @auth(['admin'])
    def manage_users():
        if request.method=='GET':return jsonify(rows('SELECT id,name,email,role,approved,verified,disabled,created FROM users ORDER BY id DESC LIMIT 1000'))
        d=request.json;uid=d.get('id');action=d.get('action')
        if type(uid)!=int or uid==user()['id'] or action not in ('disable','enable','approve','reject'):return fail('Choose a different account and valid action.')
        db().execute('BEGIN IMMEDIATE');u=one('SELECT * FROM users WHERE id=?',(uid,))
        if not u or u['role']=='admin':db().rollback();return fail('Administrator accounts cannot be changed here.')
        if action in ('approve','reject'):
            if u['role']!='organizer':db().rollback();return fail('Select an organizer.')
            db().execute('UPDATE users SET approved=?,version=version+1 WHERE id=?',(int(action=='approve'),uid))
            if action=='reject':db().execute("UPDATE events SET status='pending',reason='Organizer approval revoked.' WHERE owner=? AND status='approved' AND date>?",(uid,now().isoformat()))
        else:db().execute('UPDATE users SET disabled=?,version=version+1 WHERE id=?',(int(action=='disable'),uid))
        notice(uid,'Administrator updated your account: '+action+'.')
        db().execute('INSERT INTO admin_audit(actor,action,target,created) VALUES(?,?,?,?)',(user()['id'],action,uid,now().isoformat()));db().commit()
        return jsonify(message='Account updated. Existing sessions revoked.')

    @app.get('/api/analytics/extended')
    @auth(['organizer','admin'])
    def extended():
        admin=user()['role']=='admin';scope='1=1' if admin else 'e.owner=?';args=() if admin else (user()['id'],)
        monthly=rows(f"SELECT substr(b.created,1,7) label,COUNT(*) bookings,COALESCE(SUM(b.total),0) revenue FROM bookings b JOIN events e ON e.id=b.event WHERE b.status='confirmed' AND {scope} GROUP BY label ORDER BY label DESC LIMIT 24",args)[::-1]
        performance=rows(f"SELECT e.id,e.title label,COUNT(b.id) bookings,COALESCE(SUM(b.qty),0) tickets,COALESCE(SUM(b.total),0) revenue FROM events e LEFT JOIN bookings b ON b.event=e.id AND b.status='confirmed' WHERE {scope} GROUP BY e.id ORDER BY tickets DESC LIMIT 20",args)
        totals=one(f"SELECT COUNT(*) events FROM events e WHERE {scope}",args)
        totals.update(one(f"SELECT COUNT(*) bookings,COALESCE(SUM(b.total),0) revenue FROM bookings b JOIN events e ON e.id=b.event WHERE b.status='confirmed' AND {scope}",args))
        growth=[];organizers=[]
        if admin:
            totals.update(one("SELECT COUNT(*) users,SUM(role='organizer') organizers,SUM(created IS NULL) users_without_signup_date FROM users"))
            growth=rows("SELECT substr(created,1,7) label,COUNT(*) value FROM users WHERE created IS NOT NULL GROUP BY label ORDER BY label DESC LIMIT 24")[::-1]
            organizers=rows("SELECT u.name label,COUNT(DISTINCT e.id) events,COUNT(b.id) bookings,COALESCE(SUM(b.total),0) revenue FROM users u LEFT JOIN events e ON e.owner=u.id LEFT JOIN bookings b ON b.event=e.id AND b.status='confirmed' WHERE u.role='organizer' GROUP BY u.id ORDER BY revenue DESC LIMIT 30")
        return jsonify(totals=totals,monthly=monthly,performance=performance,growth=growth,organizers=organizers)

    @app.get('/api/admin/payments')
    @auth(['admin'])
    def all_payments():
        return jsonify(demo=rows('SELECT p.*,e.title FROM demo_payments p JOIN events e ON e.id=p.event ORDER BY p.id DESC LIMIT 500'),razorpay=rows('SELECT p.*,e.title FROM razorpay_payments p JOIN events e ON e.id=p.event ORDER BY p.id DESC LIMIT 500'))
