"""Shared inventory counts: confirmed tickets plus unexpired checkout holds."""
from flask import jsonify
from client_updates import now
from datetime import datetime,timedelta


def install(app,db,one):
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS seat_holds(
          provider TEXT NOT NULL, payment INTEGER NOT NULL,event INTEGER NOT NULL REFERENCES events(id),
          qty INTEGER NOT NULL,expires TEXT NOT NULL,PRIMARY KEY(provider,payment));
        CREATE INDEX IF NOT EXISTS holds_event ON seat_holds(event,expires);
        CREATE VIEW IF NOT EXISTS event_inventory AS
          SELECT e.id,
            COALESCE((SELECT SUM(qty) FROM bookings WHERE event=e.id AND status='confirmed'),0) confirmed,
            COALESCE((SELECT SUM(qty) FROM seat_holds WHERE event=e.id
              AND datetime(expires)>datetime('now','+330 minutes')),0) held,
            MAX(0,e.capacity-COALESCE((SELECT SUM(qty) FROM bookings WHERE event=e.id AND status='confirmed'),0)
              -COALESCE((SELECT SUM(qty) FROM seat_holds WHERE event=e.id
              AND datetime(expires)>datetime('now','+330 minutes')),0)) available
          FROM events e;
        ''')
        db().commit()

    def hold(provider,payment,event,qty,expires):
        # Caller must hold BEGIN IMMEDIATE, making capacity check + reservation atomic.
        existing=one('SELECT * FROM seat_holds WHERE provider=? AND payment=?',(provider,payment))
        own=existing['qty'] if existing and existing['expires']>now().isoformat() else 0
        inventory=one('SELECT * FROM event_inventory WHERE id=?',(event,))
        if not inventory or inventory['available']+own<qty:return False
        db().execute('INSERT OR REPLACE INTO seat_holds VALUES(?,?,?,?,?)',(provider,payment,event,qty,expires))
        return True
    app.extensions['eventai_hold_seats']=hold

    def migrate_pending():
        """Give legacy pending requests holds without overbooking existing records."""
        db().execute('BEGIN IMMEDIATE')
        requests=[]
        for p in db().execute("SELECT p.*,e.date FROM demo_payments p JOIN events e ON e.id=p.event WHERE p.status='pending'").fetchall():
            requests.append(('demo',dict(p)))
        for p in db().execute("SELECT p.*,e.date FROM razorpay_payments p JOIN events e ON e.id=p.event WHERE p.status IN ('creating','created','authorized','paid_pending')").fetchall():
            requests.append(('razorpay',dict(p)))
        for provider,p in sorted(requests,key=lambda item:item[1]['created']):
            if one('SELECT payment FROM seat_holds WHERE provider=? AND payment=?',(provider,p['id'])):continue
            expires=(p['expires'] if provider=='demo' else p['date'] if p['status']=='paid_pending' else
                min(datetime.fromisoformat(p['created'])+timedelta(minutes=15),datetime.fromisoformat(p['date'])).isoformat())
            if expires<=now().isoformat():continue
            if not hold(provider,p['id'],p['event'],p['qty'],expires):
                status='rejected' if provider=='demo' else 'refund_required' if p['status']=='paid_pending' else 'failed'
                table='demo_payments' if provider=='demo' else 'razorpay_payments'
                db().execute(f'UPDATE {table} SET status=?,reason=? WHERE id=?',(status,'No seats available when migrating the pending request.',p['id']))
                db().execute('INSERT INTO notices(user,text) VALUES(?,?)',(p['user'],'Booking request updated: '+status+'. No seats could be reserved; contact the event organizer.'))
        db().commit()
    app.extensions['eventai_migrate_holds']=migrate_pending

    @app.get('/api/inventory/<int:eid>')
    def inventory(eid):
        if not one("SELECT id FROM events WHERE id=? AND status='approved'",(eid,)):
            return jsonify(error='Event unavailable.'),404
        return jsonify(one('SELECT * FROM event_inventory WHERE id=?',(eid,)))
