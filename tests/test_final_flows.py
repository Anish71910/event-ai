import io,time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import pytest
import app as module
import notifications
from client_updates import now
from evaluation import metrics,evaluate
from test_recommendations import rec
from test_razorpay import rp


def layout(c,p,login,eid=100,price=0):
    login(2)
    r=p('seats/'+str(eid),{'rows':[dict(label='A',section='Classic',count=3,price=price,aisle=1),dict(label='B',section='Premium',count=2,price=price+100,aisle=0)]})
    assert r.status_code==200,r.json
    login(3);assert p('admin/review',dict(id=eid,status='approved')).status_code==200
    login(10);return c.get('/api/seats/'+str(eid)).json['seats']


def confirm(c,p,login,eid=100,seats=None,key='flow-final-request'):
    login(10);d=dict(event=eid,qty=len(seats) if seats else 1,request_id=key)
    if seats:d['seats']=seats
    r=p('book',d);assert r.status_code==200,r.json
    login(2);r=p('organizer/payments/review',dict(id=r.json['payment_id'],decision='approved'));assert r.status_code==200,r.json
    login(10);return c.get('/api/dashboard').json['bookings'][0]


def test_named_seats_pricing_hold_confirmation_and_cancellation(rec):
    c,p,login=rec;seats=layout(c,p,login,price=100);ids=[seats[0]['id'],seats[3]['id']]
    r=p('book',dict(event=100,qty=2,seats=ids,request_id='named-payment-001',amount=1))
    assert r.status_code==200 and r.json['amount']==300
    assert [x['state'] for x in c.get('/api/seats/100').json['seats']].count('held')==2
    assert p('book',dict(event=100,qty=1,seats=[ids[0]],request_id='named-payment-002')).status_code==400
    assert p('book',dict(event=100,qty=2,seats=ids,request_id='named-payment-001')).json['payment_id']==r.json['payment_id']
    assert p('book',dict(event=100,qty=2,seats=[seats[1]['id'],seats[2]['id']],request_id='named-payment-001')).status_code==400
    login(2);assert p('organizer/payments/review',dict(id=r.json['payment_id'],decision='approved')).json['status']=='approved'
    login(10);b=c.get('/api/dashboard').json['bookings'][0]
    assert sum(x['state']=='booked' for x in c.get('/api/seats/100').json['seats'])==2
    assert p(f"bookings/{b['id']}/cancel",{}).status_code==200
    assert all(x['state']=='available' for x in c.get('/api/seats/100').json['seats'])
    assert p(f"bookings/{b['id']}/cancel",{}).status_code==200
    assert c.get('/api/dashboard').json['user']['points']==0


def test_named_seat_race_only_one_hold(rec):
    c,p,login=rec;sid=layout(c,p,login)[0]['id']
    def run(i):
        client=module.app.test_client()
        with client.session_transaction() as s:s.update(uid=10,version=1,csrf='race')
        return client.post('/api/book',json=dict(event=100,qty=1,seats=[sid],request_id='seat-race-'+str(i)),headers={'X-CSRF-Token':'race'}).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:codes=list(pool.map(run,range(4)))
    assert codes.count(200)==1 and codes.count(400)==3


def test_layout_auth_lock_and_invalid_selection(rec):
    c,p,login=rec;seats=layout(c,p,login)
    assert p('seats/100',{'rows':[]}).status_code==403
    for selected in [[],[True],[seats[0]['id'],seats[0]['id']],[999999]]:
        assert p('book',dict(event=100,qty=1,seats=selected,request_id='bad-layout-001')).status_code==400
    p('book',dict(event=100,qty=1,seats=[seats[0]['id']],request_id='lock-layout-001'))
    login(2);assert p('seats/100',{'rows':[dict(label='Z',section='VIP',count=5,price=0)]}).status_code==400


def test_qr_event_scope_roles_duplicate_and_cancelled(rec):
    c,p,login=rec;b=confirm(c,p,login)
    assert p('checkin',dict(event=100,token=b['token'])).status_code==403
    login(2)
    assert p('checkin',dict(token=b['token'])).status_code==400
    assert p('checkin',dict(event=101,token=b['token'])).json['status']=='invalid'
    assert p('checkin',dict(event=100,token=b['token'])).json['status']=='valid'
    assert p('checkin',dict(event=100,token=b['token'])).json['status']=='already_used'
    login(10);assert p(f"bookings/{b['id']}/cancel",{}).status_code==400
    b2=confirm(c,p,login,key='another-ticket-001');p(f"bookings/{b2['id']}/cancel",{})
    login(2);assert p('checkin',dict(event=100,token=b2['token'])).json['status']=='cancelled'


def test_qr_concurrent_scan_once(rec):
    c,p,login=rec;b=confirm(c,p,login)
    def scan(_):
        client=module.app.test_client()
        with client.session_transaction() as s:s.update(uid=2,version=1,csrf='scan')
        return client.post('/api/checkin',json=dict(event=100,token=b['token']),headers={'X-CSRF-Token':'scan'}).json['status']
    with ThreadPoolExecutor(max_workers=3) as pool:statuses=list(pool.map(scan,range(3)))
    assert statuses.count('valid')==1 and statuses.count('already_used')==2


def test_cancel_event_invalidates_tickets_and_pending_holds(rec):
    c,p,login=rec;b=confirm(c,p,login)
    pending=p('book',dict(event=100,request_id='pending-cancel-001')).json
    login(11);assert p('events/100/cancel',{'reason':'Cancelled schedule'}).status_code==403
    login(2);assert p('events/100/cancel',{'reason':'Venue no longer available'}).status_code==200
    assert p('events/100/cancel',{'reason':'Venue no longer available'}).status_code==200
    assert p('checkin',dict(event=100,token=b['token'])).json['status']=='cancelled'
    login(10);assert c.get('/api/dashboard').json['bookings'][0]['status']=='cancelled'
    assert all(x['id']!=100 for x in c.get('/api/events').json)
    with module.app.app_context():assert module.one('SELECT COUNT(*) n FROM seat_holds WHERE event=100')['n']==0
    login(3);assert p('admin/review',dict(id=100,status='approved')).status_code==400


def test_razorpay_named_seat_prices_and_late_cancel_capture(rp):
    c,p,login,state,create,payment,webhook=rp;seats=layout(c,p,login,price=100)
    order=create(seats=[seats[3]['id']]).json;assert order['amount']==20000
    assert p('checkout/cancel',dict(provider='razorpay',id=order['id'])).json['status']=='cancelled'
    data=payment(order);assert p('razorpay/verify',data).json['status']=='refund_required'
    assert c.get('/api/dashboard').json['bookings']==[]
    assert all(x['state']=='available' for x in c.get('/api/seats/100').json['seats'])


def test_razorpay_cancel_confirmed_and_refund_once(rp):
    c,p,login,state,create,payment,webhook=rp;order=create().json;p('razorpay/verify',payment(order))
    login(2);bid=p('organizer/razorpay/review',dict(id=order['id'],decision='approved')).json['booking_id']
    login(10);assert p(f'bookings/{bid}/cancel',{}).status_code==200
    assert c.get('/api/razorpay/payments').json[0]['status']=='refund_required'
    assert c.get('/api/dashboard').json['user']['points']==0
    state['payments']['pay_Test1'].update(status='refunded',amount_refunded=order['amount'])
    assert webhook(event='refund.processed').status_code==200
    assert webhook(event='refund.processed').json['duplicate']
    assert c.get('/api/dashboard').json['user']['points']==0


def test_email_outbox_delivers_ticket_and_deduplicates(rec,monkeypatch):
    c,p,login=rec;sent=[];monkeypatch.setattr(notifications,'send_smtp',lambda message:sent.append(message))
    b=confirm(c,p,login)
    with module.app.app_context():
        r=module.app.extensions['eventai_deliver_emails']();assert r['sent']>=2
        before=len(sent);module.app.extensions['eventai_deliver_emails']();assert len(sent)==before
    matching=[m for m in sent if m['Subject']=='EventAI — Your QR ticket'];assert matching
    assert any(x.get_content_type()=='image/png' for x in matching[-1].iter_attachments())
    p(f"bookings/{b['id']}/cancel",{})
    with module.app.app_context():module.app.extensions['eventai_deliver_emails']()
    assert any(m['Subject']=='EventAI — Booking cancellation' for m in sent)


def test_reminders_24h_2h_no_duplicates_and_cancellation(rec,monkeypatch):
    c,p,login=rec;b=confirm(c,p,login);sent=[];monkeypatch.setattr(notifications,'send_smtp',lambda m:sent.append(m))
    start=now()+timedelta(hours=23)
    with module.app.app_context():
        module.db().execute('UPDATE events SET date=? WHERE id=100',(start.isoformat(),))
        module.db().execute("UPDATE bookings SET created=datetime('now','-2 days') WHERE id=?",(b['id'],));module.db().execute('DELETE FROM email_outbox');module.db().commit()
        module.app.extensions['eventai_notifications_tick']();module.app.extensions['eventai_notifications_tick']()
        assert len(sent)==1 and '24 hours' in sent[0]['Subject']
        monkeypatch.setattr(notifications,'now',lambda:start-timedelta(hours=1))
        module.app.extensions['eventai_notifications_tick']();module.app.extensions['eventai_notifications_tick']()
        assert len(sent)==2 and '2 hours' in sent[1]['Subject']
        assert module.one('SELECT COUNT(*) n FROM email_outbox')['n']==2


def test_email_failure_retry_and_cancelled_reminder_suppression(rec,monkeypatch):
    c,p,login=rec;b=confirm(c,p,login)
    def broken(_):raise OSError('SMTP down')
    monkeypatch.setattr(notifications,'send_smtp',broken)
    with module.app.app_context():
        r=module.app.extensions['eventai_deliver_emails']();assert r['failed']>=2
        assert module.one("SELECT COUNT(*) n FROM email_outbox WHERE status='sent'")['n']==0
        assert module.one("SELECT COUNT(*) n FROM email_outbox WHERE next_try>0")['n']>=2
        module.db().execute("UPDATE email_outbox SET next_try=0");module.db().commit()
    sent=[];monkeypatch.setattr(notifications,'send_smtp',lambda m:sent.append(m))
    p(f"bookings/{b['id']}/cancel",{})
    with module.app.app_context():module.app.extensions['eventai_deliver_emails']()
    assert not any(m['Subject']=='EventAI — Your QR ticket' for m in sent)
    assert any('cancellation' in m['Subject'] for m in sent)


def test_admin_controls_scoped_reports_and_real_metrics(rec):
    c,p,login=rec;b=confirm(c,p,login)
    assert c.get('/api/analytics/extended').status_code==403
    assert c.get('/api/admin/users').status_code==403
    login(2);assert any(x['id']==b['id'] for x in c.get('/api/management/bookings').json)
    login(3);d=c.get('/api/analytics/extended').json;assert d['totals']['bookings']==1
    assert p('admin/users',dict(id=10,action='disable')).status_code==200
    login(10);assert c.get('/api/dashboard').status_code==401


def test_evaluation_math_empty_and_temporal_holdout(rec):
    assert metrics({}, {}, 5)['precision'] is None
    assert metrics({1:[2,3],2:[4,5]},{1:{2},2:{7}},2)==dict(precision=.25,recall=.5,hit_rate=.5,users=2)
    catalog=[dict(id=i,title='Music',category='Music',description='',city='Davangere',venue='',date='2099-01-01') for i in (1,2,3)]
    signals=[dict(user=10,event=1,kind='booking',weight=8,created=10),dict(user=10,event=2,kind='booking',weight=8,created=20)]
    result=evaluate(catalog,[dict(id=10,interests='Music',city='Davangere')],signals,2)
    assert result['users']==1 and result['recall']==1 and result['precision']==.5
    c,p,login=rec;assert c.get('/api/admin/recommendation-evaluation').status_code==403
    login(3);assert c.get('/api/admin/recommendation-evaluation').json['precision'] is None


def test_price_filters_and_security_input_validation(rec):
    c,p,login=rec
    with module.app.app_context():module.db().execute('UPDATE events SET price=500 WHERE id=100');module.db().commit()
    assert [e['id'] for e in c.get('/api/events?min_price=400&max_price=600').json]==[100]
    assert c.get('/api/events?max_price=nan').status_code==400
    assert p('checkout/cancel',dict(provider=[],id=1)).status_code==400
    assert p('events/100/cancel',dict(reason=[])).status_code==400

def test_optional_two_hour_reminder_and_failed_retry_limit(rec,monkeypatch):
    c,p,login=rec;b=confirm(c,p,login);monkeypatch.setenv('REMINDER_2H','0')
    with module.app.app_context():
        module.db().execute('UPDATE events SET date=? WHERE id=100',((now()+timedelta(hours=1)).isoformat(),))
        module.db().execute("UPDATE bookings SET created=datetime('now','-2 days') WHERE id=?",(b['id'],))
        module.db().execute('DELETE FROM email_outbox');module.db().commit()
        module.app.extensions['eventai_enqueue_reminders']();assert module.one('SELECT COUNT(*) n FROM email_outbox')['n']==0
        monkeypatch.setenv('REMINDER_2H','1');module.app.extensions['eventai_enqueue_reminders']()
        monkeypatch.setattr(notifications,'send_smtp',lambda _:(_ for _ in ()).throw(OSError('offline')))
        for _ in range(5):
            module.db().execute('UPDATE email_outbox SET next_try=0');module.db().commit();module.app.extensions['eventai_deliver_emails']()
        assert module.one('SELECT status,attempts FROM email_outbox')==dict(status='failed',attempts=5)
    login(3);jobs=c.get('/api/admin/email-queue').json['items'];assert p('admin/email-queue',dict(id=jobs[0]['id'])).status_code==200
    with module.app.app_context():assert module.one('SELECT status,attempts FROM email_outbox')==dict(status='pending',attempts=0)


def test_ticket_details_ownership_and_preserved_cancelled_seat_labels(rec):
    c,p,login=rec;sid=layout(c,p,login)[0]['id'];b=confirm(c,p,login,seats=[sid])
    assert c.get(f"/api/tickets/{b['id']}").json['seats'][0]['row_label']=='A'
    login(11);assert c.get(f"/api/tickets/{b['id']}").status_code==404
    login(10);p(f"bookings/{b['id']}/cancel",{})
    detail=c.get(f"/api/tickets/{b['id']}").json
    assert 'token' not in detail and detail['seats'][0]['row_label']=='A'


def test_webhook_requires_signature_but_accepts_provider_event_string(rec):
    c,p,login=rec
    assert c.post('/api/razorpay/webhook',json={'event':'payment.captured'}).status_code==400
    assert p('book',dict(event=100,qty=1.5,request_id='fractional-001')).status_code==400
    assert p('book',dict(event=100,qty=1e100,request_id='overflow-001')).status_code==400

def test_email_delivery_concurrent_workers_claim_once(rec,monkeypatch):
    c,p,login=rec;b=confirm(c,p,login);sent=[]
    monkeypatch.setattr(notifications,'send_smtp',lambda m:sent.append(m['Message-ID']))
    def deliver(_):
        with module.app.app_context():return module.app.extensions['eventai_deliver_emails'](1)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(deliver,range(4)))
    assert len(sent)>=2 and len(sent)==len(set(sent))
    assert c.get(f"/api/ticket-qr/{b['id']}").mimetype=='image/png'
    login(11);assert c.get(f"/api/ticket-qr/{b['id']}").status_code==404


def test_default_map_preserves_existing_bookings_and_holds(rec):
    c,p,login=rec
    booking=confirm(c,p,login,key='legacy-confirmed-map')
    login(10)
    pending=p('book',dict(event=100,qty=2,request_id='legacy-held-map'))
    assert pending.status_code==200
    data=c.get('/api/seats/100').json
    assert data['enabled'] and len(data['seats'])==100
    assert sum(s['state']=='booked' for s in data['seats'])==1
    assert sum(s['state']=='held' for s in data['seats'])==2
    available=[s for s in data['seats'] if s['state']=='available']
    assert p('book',dict(event=100,qty=1,request_id='missing-seat-map')).status_code==400
    result=p('book',dict(event=100,qty=1,seats=[available[0]['id']],request_id='default-seat-map'))
    assert result.status_code==200,result.json
    again=c.get('/api/seats/100').json
    assert len(again['seats'])==100 and sum(s['state']=='held' for s in again['seats'])==3
    assert p('book',dict(event=100,qty=1,seats=[available[0]['id']],request_id='default-seat-taken')).status_code==400
