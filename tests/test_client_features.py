from concurrent.futures import ThreadPoolExecutor
import app as module
from test_recommendations import rec
from test_razorpay import rp


def test_seats_drop_at_request_and_release_on_rejection(rec):
    c,p,login=rec
    initial=c.get('/api/inventory/100').json['available']
    payment=p('book',dict(event=100,qty=3,request_id='free-request-001')).json
    assert payment['status']=='pending'
    assert c.get('/api/inventory/100').json['available']==initial-3
    assert c.get('/api/dashboard').json['bookings']==[]
    login(3)
    assert p('organizer/payments/review',dict(id=payment['payment_id'],decision='approved')).status_code==403
    assert c.get('/api/organizer/payments').status_code==403
    login(2)
    assert p('organizer/payments/review',dict(id=payment['payment_id'],decision='rejected',reason='Schedule issue')).status_code==200
    assert c.get('/api/inventory/100').json['available']==initial


def test_only_owning_organizer_can_approve(rec):
    c,p,login=rec
    pid=p('book',dict(event=100,request_id='ownership-book-1')).json['payment_id']
    with module.app.app_context():module.db().execute("UPDATE users SET role='organizer' WHERE id=11");module.db().commit()
    login(11)
    assert c.get('/api/organizer/payments').json==[]
    assert p('organizer/payments/review',dict(id=pid,decision='approved')).status_code==403
    login(2);assert p('organizer/payments/review',dict(id=pid,decision='approved')).json['status']=='approved'
    inv=c.get('/api/inventory/100').json
    assert inv['confirmed']==1 and inv['held']==0 and inv['available']==99


def test_concurrent_requests_reserve_last_seat_only_once(rec):
    with module.app.app_context():module.db().execute('UPDATE events SET capacity=1 WHERE id=100');module.db().commit()
    def request_seat(i):
        c=module.app.test_client()
        with c.session_transaction() as s:s.update(uid=10,version=1,csrf='parallel')
        return c.post('/api/book',json=dict(event=100,request_id='unique-seat-'+str(i)),headers={'X-CSRF-Token':'parallel'})
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(request_seat,range(4)))
    assert sum(r.status_code==200 for r in results)==1
    c,p,login=rec;assert c.get('/api/inventory/100').json['available']==0


def test_hold_expiry_restores_availability_and_excludes_full_recommendations(rec):
    c,p,login=rec
    with module.app.app_context():module.db().execute('UPDATE events SET capacity=1 WHERE id=100');module.db().commit()
    pid=p('book',dict(event=100,request_id='hold-expiration-1')).json['payment_id']
    assert all(e['id']!=100 for e in c.get('/api/recommendations').json['events'])
    with module.app.app_context():
        module.db().execute("UPDATE seat_holds SET expires='2020-01-01' WHERE payment=?",(pid,));module.db().execute("UPDATE demo_payments SET expires='2020-01-01' WHERE id=?",(pid,));module.db().commit()
    assert c.get('/api/inventory/100').json['available']==1
    assert c.get('/api/payments').json[0]['status']=='expired'


def test_legacy_pending_migration_rebuilds_holds_safely(rec):
    c,p,login=rec
    p('book',dict(event=100,request_id='legacy-pending-001'))
    p('book',dict(event=100,request_id='legacy-pending-002'))
    with module.app.app_context():
        module.db().execute('DELETE FROM seat_holds')
        module.db().execute('UPDATE events SET capacity=1 WHERE id=100');module.db().commit()
        module.app.extensions['eventai_migrate_holds']()
        module.app.extensions['eventai_migrate_holds']()
        assert module.one('SELECT held FROM event_inventory WHERE id=100')['held']==1
        assert module.one("SELECT COUNT(*) n FROM demo_payments WHERE status='rejected'")['n']==1


def test_razorpay_holds_before_payment_and_refunds_late_capture(rp):
    c,p,login,state,create,payment,webhook=rp
    with module.app.app_context():module.db().execute('UPDATE events SET capacity=1 WHERE id=100');module.db().commit()
    order=create().json
    assert c.get('/api/inventory/100').json['available']==0
    assert create(key='another-checkout-1').status_code==400
    with module.app.app_context():
        module.db().execute("UPDATE seat_holds SET expires='2020-01-01'");module.db().commit()
    other=create(key='another-checkout-2').json
    assert p('razorpay/verify',payment(order)).json['status']=='refund_required'
    assert c.get('/api/inventory/100').json['held']==1
    login(3);assert p('organizer/razorpay/review',dict(id=other['id'],decision='approved')).status_code==403


def test_event_notifications_reviews_history_and_analytics(rec):
    c,p,login=rec
    pid=p('book',dict(event=100,request_id='review-booking-1')).json['payment_id']
    login(2)
    event=next(e for e in c.get('/api/organizer').json['events'] if e['id']==100)
    event.update(venue='Updated hall')
    assert p('organizer',event).status_code==200
    login(10);assert any('details updated' in n['text'] for n in c.get('/api/dashboard').json['notices'])
    assert p('reviews/100',dict(rating=5,comment='Wonderful event and venue.')).status_code==400
    login(3);p('admin/review',dict(type='event',id=100,status='approved'))
    login(2);p('organizer/payments/review',dict(id=pid,decision='approved'))
    with module.app.app_context():module.db().execute("UPDATE events SET date='2020-01-01' WHERE id=100");module.db().commit()
    login(10)
    assert all(e['id']!=100 for e in c.get('/api/events').json)
    assert c.get('/api/dashboard').json['bookings'][0]['lifecycle']=='completed'
    assert p('reviews/100',dict(rating=5,comment='Wonderful event and venue.')).status_code==200
    assert p('reviews/100',dict(rating=4,comment='Updated review after the event.')).status_code==200
    reviews=c.get('/api/reviews/100').json
    assert reviews['summary']=={'count':1,'average':4.0}
    login(11);assert p('reviews/100',dict(rating=1,comment='I did not have a booking.')).status_code==400
    assert c.get('/api/analytics').status_code==403
    login(2);data=c.get('/api/analytics').json
    assert any(r['label']=='Music' and r['value']==1 for r in data['categories'])
    assert data['ratings']==[{'label':'4 stars','value':1}]
    assert c.get('/api/community').status_code==200
    login(3);assert c.get('/api/community').status_code==200
    assert c.get('/api/analytics').json['scope']=='Platform'
