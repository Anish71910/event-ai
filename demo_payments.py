"""Local payment simulator. Never contacts Razorpay or collects payment credentials."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from flask import request, jsonify
from client_updates import now


def install(app, db, auth, user, one, rows, fail, notice, limited, demo_enabled):
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS demo_payments(
          id INTEGER PRIMARY KEY, user INTEGER NOT NULL REFERENCES users(id),
          event INTEGER NOT NULL REFERENCES events(id), request_id TEXT NOT NULL,
          payload TEXT NOT NULL, reference TEXT NOT NULL UNIQUE,
          qty INTEGER NOT NULL, unit_price INTEGER NOT NULL, redeemed INTEGER NOT NULL,
          amount INTEGER NOT NULL, method TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL, expires TEXT NOT NULL,
          reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT, reason TEXT DEFAULT '',
          booking INTEGER REFERENCES bookings(id), UNIQUE(user,request_id));
        CREATE INDEX IF NOT EXISTS demo_payment_queue ON demo_payments(status,created);
        ''')
        db().commit()

    def result(p, replay=False):
        return jsonify(payment_id=p['id'],reference=p['reference'],status=p['status'],
                       amount=p['amount'],booking_id=p['booking'],replayed=replay,
                       message=('Ticket confirmed.' if p['status']=='approved' else
                                'Booking request submitted. Seats held pending organizer approval.'
                                if p['status']=='pending' else 'Payment '+p['status']+'.'))


    @auth()
    def submit():
        d=request.json;uid=user()['id']
        e=one('SELECT * FROM events WHERE id=?',(d.get('event'),))
        if not e:return fail('Event unavailable.')
        if e['price'] and not demo_enabled():return fail('Use the configured Razorpay checkout for paid bookings.')
        if limited('payment:'+str(uid),20,60):return jsonify(error='Please wait before trying again.'),429
        qty=int(d.get('qty',1));redeem=int(d.get('points',0));key=d.get('request_id','')
        method=d.get('method','demo_upi') if e['price'] else 'free'
        if not e['price'] and not key:key=secrets.token_hex(16)
        if not isinstance(key,str) or not 8<=len(key)<=128:return fail('A valid payment request ID is required.')
        if method not in ['demo_upi','demo_card','demo_netbanking','free']:return fail('Select a demo payment method.')
        if not 1<=qty<=10 or redeem<0:return fail('Choose 1–10 tickets and valid points.')
        gross,selected=app.extensions['eventai_seat_quote'](e,d,qty)
        if gross and not demo_enabled():return fail('Use Razorpay for paid seats.')
        method=d.get('method','demo_upi') if gross else 'free'
        if method not in ['demo_upi','demo_card','demo_netbanking','free']:return fail('Select a supported payment method.')
        if gross and method=='free':return fail('Paid seats require payment.')
        if gross and (method=='demo_card' or d.get('demo_flow')) and d.get('demo_otp')!='0000':return fail('Enter demo OTP 0000. No real SMS is sent.')
        payload=hashlib.sha256(json.dumps([e['id'],qty,redeem,method,selected]).encode()).hexdigest()
        db().execute('BEGIN IMMEDIATE')
        previous=one('SELECT * FROM demo_payments WHERE user=? AND request_id=?',(uid,key))
        if previous:
            db().rollback()
            if previous['payload']!=payload:return fail('Request ID already used for another payment.')
            return result(previous,True)
        e=one('SELECT * FROM events WHERE id=?',(e['id'],));u=user();gross,selected=app.extensions['eventai_seat_quote'](e,d,qty)
        available=one('SELECT available FROM event_inventory WHERE id=?',(e['id'],))['available']
        if e['status']!='approved' or e['date']<=now().isoformat() or qty>available:
            db().rollback();return fail('Event unavailable or not enough seats.')
        if redeem>min(max(0,u['points']),gross//5):db().rollback();return fail('Not enough points. Maximum redemption is 20%.')
        stamp=now();ref=('booking_' if method=='free' else 'demo_pay_')+secrets.token_hex(10)
        pid=db().execute('''INSERT INTO demo_payments(user,event,request_id,payload,reference,qty,
          unit_price,redeemed,amount,method,created,expires) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
          (uid,e['id'],key,payload,ref,qty,e['price'],redeem,gross-redeem,method,
           stamp.isoformat(),min(stamp+timedelta(hours=24),datetime.fromisoformat(e['date'])).isoformat())).lastrowid
        db().execute('UPDATE demo_payments SET seat_json=? WHERE id=?',(json.dumps(selected),pid))
        expiry=one('SELECT expires FROM demo_payments WHERE id=?',(pid,))['expires']
        if not app.extensions['eventai_hold_seats']('demo',pid,e['id'],qty,expiry):
            db().rollback();return fail('Not enough seats remain.')
        notice(e['owner'],f'Booking request {ref}: {u["name"]}, ₹{gross-redeem}, {qty} ticket(s) for {e["title"]}. Awaiting organizer approval.')
        notice(uid,'Seats reserved for '+e['title']+'. Awaiting organizer approval; no QR ticket issued yet.')
        db().commit();return result(one('SELECT * FROM demo_payments WHERE id=?',(pid,)))

    app.add_url_rule('/api/book',endpoint='book',view_func=submit,methods=['POST'])

    def expire_pending():
        db().execute('BEGIN IMMEDIATE')
        expired=rows("SELECT id,user,reference FROM demo_payments WHERE status='pending' AND expires<=?",(now().isoformat(),))
        for p in expired:
            db().execute("UPDATE demo_payments SET status='expired',reason='Approval window expired.' WHERE id=?",(p['id'],))
            db().execute("DELETE FROM seat_holds WHERE provider='demo' AND payment=?",(p['id'],))
            notice(p['user'],'Demo payment '+p['reference']+' expired before approval. No ticket was issued or money charged.')
        db().commit()

    @app.get('/api/payments')
    @auth()
    def payments():
        expire_pending()
        return jsonify(rows('''SELECT p.*,e.title FROM demo_payments p JOIN events e ON e.id=p.event
          WHERE p.user=? ORDER BY p.id DESC LIMIT 200''',(user()['id'],)))

    @app.get('/api/organizer/payments')
    @auth(['organizer'])
    def queue():
        expire_pending()
        return jsonify(rows('''SELECT p.*,e.title,u.name,u.email FROM demo_payments p
          JOIN events e ON e.id=p.event JOIN users u ON u.id=p.user WHERE e.owner=?
          ORDER BY (p.status='pending') DESC,p.id DESC LIMIT 500''',(user()['id'],)))

    @app.post('/api/organizer/payments/review')
    @auth(['organizer'])
    def review_demo_payment():
        d=request.json;decision=d.get('decision');reason=str(d.get('reason','')).strip()[:500]
        if decision not in ['approved','rejected']:return fail('Choose approve or reject.')
        if decision=='rejected' and not reason:return fail('A rejection reason is required.')
        db().execute('BEGIN IMMEDIATE')
        p=one('SELECT * FROM demo_payments WHERE id=?',(d.get('id'),))
        if not p:db().rollback();return fail('Payment not found.')
        if not one('SELECT id FROM events WHERE id=? AND owner=?',(p['event'],user()['id'])):
            db().rollback();return jsonify(error='Only this event organizer can review its tickets.'),403
        if p['method']!='free' and not demo_enabled():db().rollback();return fail('Demo payment approvals are disabled.')
        if p['status']!='pending':
            db().rollback()
            if p['status']==decision:return result(p,True)
            return fail('This payment has already been reviewed.')
        e=one('SELECT * FROM events WHERE id=?',(p['event'],))
        customer=one('SELECT * FROM users WHERE id=?',(p['user'],))
        status=decision;bid=None
        if decision=='approved':
            sold=one("SELECT COALESCE(SUM(qty),0) n FROM bookings WHERE event=? AND status='confirmed'",(p['event'],))['n']
            if p['expires']<=now().isoformat() or e['date']<=now().isoformat():status='expired';reason='Payment approval window expired.'
            elif e['status']!='approved':status='rejected';reason='Event is no longer available.'
            elif e['price']!=p['unit_price']:status='rejected';reason='Ticket price changed. Please submit a new payment.'
            elif not app.extensions['eventai_hold_seats']('demo',p['id'],p['event'],p['qty'],p['expires']):status='rejected';reason='There are no longer enough seats.'
            elif customer['points']<p['redeemed']:status='rejected';reason='Reward balance changed. Please submit again.'
            elif not customer['verified'] or customer['disabled']:status='rejected';reason='Account is not verified.'
            else:
                earned=p['amount']//10
                bid=db().execute('INSERT INTO bookings(user,event,qty,total,redeemed,earned,token) VALUES(?,?,?,?,?,?,?)',
                    (p['user'],p['event'],p['qty'],p['amount'],p['redeemed'],earned,secrets.token_urlsafe(24))).lastrowid
                db().execute('UPDATE users SET points=points+? WHERE id=?',(earned-p['redeemed'],p['user']))
                for value,label in [(-p['redeemed'],'Redeemed: '),(earned,'Purchase: ')]:
                    if value:db().execute('INSERT INTO ledger(user,amount,label) VALUES(?,?,?)',(p['user'],value,label+e['title']))
                app.extensions['eventai_record_booking'](p['user'],p['event'],bid)
        db().execute('UPDATE demo_payments SET status=?,reviewed_by=?,reviewed_at=?,reason=?,booking=? WHERE id=?',
            (status,user()['id'],now().isoformat(),reason,bid,p['id']))
        db().execute("DELETE FROM seat_holds WHERE provider='demo' AND payment=?",(p['id'],))
        notice(p['user'],('Booking confirmed: '+e['title']+'. Your QR ticket is ready.' if bid else
               'Demo payment '+status+' for '+e['title']+'. '+reason))
        db().commit();return result(one('SELECT * FROM demo_payments WHERE id=?',(p['id'],)))
