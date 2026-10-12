"""Named-seat inventory integrated with the existing transactional payment holds."""
import json,re
from flask import request,jsonify
from client_updates import now


def install(app,db,auth,user,one,rows,fail):
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS event_seats(id INTEGER PRIMARY KEY,event INTEGER NOT NULL REFERENCES events(id),
          section TEXT NOT NULL,row_label TEXT NOT NULL,number INTEGER NOT NULL,price INTEGER NOT NULL,
          aisle INTEGER NOT NULL DEFAULT 0,UNIQUE(event,row_label,number));
        CREATE TABLE IF NOT EXISTS seat_claims(seat INTEGER PRIMARY KEY REFERENCES event_seats(id),
          provider TEXT NOT NULL,payment INTEGER NOT NULL,booking INTEGER REFERENCES bookings(id));
        CREATE TRIGGER IF NOT EXISTS release_named_hold AFTER DELETE ON seat_holds BEGIN
          DELETE FROM seat_claims WHERE provider=OLD.provider AND payment=OLD.payment AND booking IS NULL;
        END;
        ''')
        for provider,table in [('demo','demo_payments'),('razorpay','razorpay_payments')]:
            if 'seat_json' not in [r[1] for r in db().execute('PRAGMA table_info('+table+')')]:db().execute("ALTER TABLE "+table+" ADD COLUMN seat_json TEXT NOT NULL DEFAULT '[]'")
            db().execute(f'''CREATE TRIGGER IF NOT EXISTS assign_{provider}_seats AFTER UPDATE OF booking ON {table}
              WHEN NEW.booking IS NOT NULL BEGIN UPDATE seat_claims SET booking=NEW.booking WHERE provider='{provider}' AND payment=NEW.id; END''')
        db().commit()

    def quote(e,d,qty):
        seats=d.get('seats',[])
        if not isinstance(seats,list) or len(seats)>10 or any(type(x)!=int for x in seats) or len(set(seats))!=len(seats):raise ValueError('Invalid seat selection.')
        configured=one('SELECT id FROM event_seats WHERE event=? LIMIT 1',(e['id'],))
        if not configured:
            if seats:raise ValueError('This event uses general admission.')
            return e['price']*qty,[]
        if len(seats)!=qty:raise ValueError('Select one seat for each ticket.')
        selected=rows('SELECT id,price FROM event_seats WHERE event=? AND id IN ('+','.join('?' for _ in seats)+')',(e['id'],*seats))
        if len(selected)!=qty:raise ValueError('Some seats do not belong to this event.')
        return sum(x['price'] for x in selected),sorted(seats)
    app.extensions['eventai_seat_quote']=quote

    base_hold=app.extensions['eventai_hold_seats']
    def hold(provider,payment,event,qty,expires):
        table={'demo':'demo_payments','razorpay':'razorpay_payments'}[provider]
        p=one('SELECT seat_json FROM '+table+' WHERE id=?',(payment,));selected=json.loads(p['seat_json']) if p else []
        db().execute('''DELETE FROM seat_claims WHERE (booking IS NOT NULL AND NOT EXISTS(SELECT 1 FROM bookings b WHERE b.id=seat_claims.booking AND b.status='confirmed'))
          OR (booking IS NULL AND NOT EXISTS(SELECT 1 FROM seat_holds h WHERE h.provider=seat_claims.provider AND h.payment=seat_claims.payment AND datetime(h.expires)>datetime(?)))''',(now().isoformat(),))
        if one('SELECT id FROM event_seats WHERE event=?',(event,)) and len(selected)!=qty:return False
        for sid in selected:
            existing=one('SELECT * FROM seat_claims WHERE seat=?',(sid,))
            if existing and (existing['provider']!=provider or existing['payment']!=payment):return False
        if not base_hold(provider,payment,event,qty,expires):return False
        for sid in selected:db().execute('INSERT OR IGNORE INTO seat_claims(seat,provider,payment) VALUES(?,?,?)',(sid,provider,payment))
        return True
    app.extensions['eventai_hold_seats']=hold

    def default_plan(eid):
        # Serialize conversion with checkout so existing reservations remain occupied.
        db().execute('BEGIN IMMEDIATE')
        try:
            e=one('SELECT * FROM events WHERE id=?',(eid,))
            if one('SELECT id FROM event_seats WHERE event=?',(eid,)):
                db().commit();return
            def label(index):
                result=''
                while True:
                    result=chr(65+index%26)+result;index=index//26-1
                    if index<0:return result
            for n in range(e['capacity']):
                db().execute('INSERT INTO event_seats(event,section,row_label,number,price,aisle) VALUES(?,?,?,?,?,?)',
                    (eid,'Standard',label(n//20),n%20+1,e['price'],10))
            available=[x['id'] for x in rows('SELECT id FROM event_seats WHERE event=? ORDER BY id',(eid,))]
            offset=0
            def assign(provider,payment,booking,qty):
                nonlocal offset
                chosen=available[offset:offset+qty];offset+=qty
                if len(chosen)!=qty:raise ValueError('Existing reservations exceed event capacity. Contact the organizer.')
                for sid in chosen:db().execute('INSERT INTO seat_claims(seat,provider,payment,booking) VALUES(?,?,?,?)',(sid,provider,payment,booking))
                if provider in ('demo','razorpay'):
                    db().execute('UPDATE '+provider+'_payments SET seat_json=? WHERE id=?',(json.dumps(chosen),payment))
            for b in rows("SELECT id,qty FROM bookings WHERE event=? AND status='confirmed' ORDER BY id",(eid,)):
                payment=None
                for provider in ('demo','razorpay'):
                    payment=one('SELECT id FROM '+provider+'_payments WHERE booking=?',(b['id'],))
                    if payment:break
                assign(provider if payment else 'legacy',payment['id'] if payment else b['id'],b['id'],b['qty'])
            for h in rows('SELECT * FROM seat_holds WHERE event=? AND datetime(expires)>datetime(?) ORDER BY payment',(eid,now().isoformat())):
                assign(h['provider'],h['payment'],None,h['qty'])
            db().commit()
        except Exception:
            db().rollback();raise
    app.extensions['eventai_default_plan']=default_plan

    @app.route('/api/seats/<int:eid>',methods=['GET','POST'])
    def seats(eid):
        e=one('SELECT * FROM events WHERE id=?',(eid,));u=user()
        manage=bool(e and u and u['verified'] and (u['role']=='admin' or u['role']=='organizer' and u['approved'] and e['owner']==u['id']))
        if not e or not (e['status']=='approved' or manage):return jsonify(error='Event unavailable.'),404
        if request.method=='POST':
            if not manage:return jsonify(error='Only the approved event owner or admin can edit seats.'),403
            d=request.json;layout=d.get('rows')
            if not isinstance(layout,list) or not 1<=len(layout)<=60:return fail('Provide 1–60 seating rows.')
            parsed=[];labels=set()
            for row in layout:
                if not isinstance(row,dict):return fail('Invalid row.')
                label=row.get('label','');section=row.get('section','');count=row.get('count');price=row.get('price');aisle=row.get('aisle',0)
                if not isinstance(label,str) or not re.fullmatch('[A-Za-z0-9]{1,4}',label) or label.upper() in labels:return fail('Use unique row labels of 1–4 letters/numbers.')
                if not isinstance(section,str) or not 1<=len(section.strip())<=50 or type(count)!=int or not 1<=count<=60 or type(price)!=int or not 0<=price<=100000 or type(aisle)!=int or not 0<=aisle<count:return fail('Check section, row size, aisle and whole-rupee price.')
                labels.add(label.upper());parsed.append((section.strip(),label.upper(),count,price,aisle))
            if sum(x[2] for x in parsed)>2000:return fail('Maximum 2,000 seats per event.')
            db().execute('BEGIN IMMEDIATE')
            e=one('SELECT * FROM events WHERE id=?',(eid,))
            if e['date']<=now().isoformat() or e['status']=='cancelled':db().rollback();return fail('This event can no longer be edited.')
            # A plan is immutable once any request exists, including expired provider orders.
            if one('SELECT id FROM bookings WHERE event=? UNION SELECT id FROM demo_payments WHERE event=? UNION SELECT id FROM razorpay_payments WHERE event=?',(eid,eid,eid)):
                db().rollback();return fail('Seat layout is locked after the first booking request. Create a new event for a different layout.')
            db().execute('DELETE FROM event_seats WHERE event=?',(eid,))
            for section,label,count,price,aisle in parsed:
                db().executemany('INSERT INTO event_seats(event,section,row_label,number,price,aisle) VALUES(?,?,?,?,?,?)',[(eid,section,label,n,price,aisle) for n in range(1,count+1)])
            db().execute('UPDATE events SET capacity=?,price=?,status=? WHERE id=?',(sum(x[2] for x in parsed),min(x[3] for x in parsed),'pending' if u['role']!='admin' else e['status'],eid));db().commit()
            return jsonify(message='Seating plan saved. Organizer changes require admin approval.')
        try:default_plan(eid)
        except ValueError as error:return fail(str(error))
        data=rows('''SELECT s.*,CASE WHEN b.status='confirmed' THEN 'booked'
          WHEN b.id IS NULL AND datetime(h.expires)>datetime(?) THEN 'held' ELSE 'available' END state
          FROM event_seats s LEFT JOIN seat_claims c ON c.seat=s.id LEFT JOIN bookings b ON b.id=c.booking
          LEFT JOIN seat_holds h ON h.provider=c.provider AND h.payment=c.payment WHERE s.event=? ORDER BY s.id''',(now().isoformat(),eid))
        return jsonify(enabled=bool(data),seats=data,capacity=e['capacity'])
