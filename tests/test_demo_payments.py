from concurrent.futures import ThreadPoolExecutor
import app as module
from test_recommendations import rec


def paid(rec, price=100, capacity=10):
    c,p,login=rec
    with module.app.app_context():
        module.db().execute('UPDATE events SET price=?,capacity=? WHERE id=100',(price,capacity))
        module.db().commit()
    return c,p,login


def test_payment_requires_organizer_before_ticket_and_rewards(rec):
    c,p,login=paid(rec)
    p('recommendation-settings',{'enabled':True})
    payload=dict(event=100,qty=2,points=0,request_id='payment-test-001',method='demo_upi',amount=1)
    r=p('book',payload).json
    assert r['status']=='pending' and r['amount']==200
    assert c.get('/api/dashboard').json['bookings']==[]
    assert c.get('/api/dashboard').json['user']['points']==0
    assert p('book',payload).json['replayed']
    assert p('book',dict(payload,qty=1)).status_code==400
    assert p('organizer/payments/review',{'id':r['payment_id'],'decision':'approved'}).status_code==403
    assert c.get('/api/organizer/payments').status_code==403
    login(11);assert c.get('/api/payments').json==[]
    login(2)
    assert any(n['reference']==r['reference'] for n in c.get('/api/organizer/payments').json)
    assert any(r['reference'] in n['text'] for n in c.get('/api/dashboard').json['notices'])
    result=p('organizer/payments/review',{'id':r['payment_id'],'decision':'approved'}).json
    assert result['status']=='approved'
    assert p('organizer/payments/review',{'id':r['payment_id'],'decision':'approved'}).json['replayed']
    assert p('organizer/payments/review',{'id':r['payment_id'],'decision':'rejected','reason':'No'}).status_code==400
    login(10);d=c.get('/api/dashboard').json
    assert len(d['bookings'])==1 and d['bookings'][0]['total']==200 and d['user']['points']==20
    assert c.get('/api/qr/'+d['bookings'][0]['token']).status_code==200
    with module.app.app_context():
        assert module.one("SELECT COUNT(*) n FROM interactions WHERE user=10 AND kind='booking'")['n']==1


def test_rejection_expiry_and_live_mode(rec,monkeypatch):
    c,p,login=paid(rec)
    first=p('book',dict(event=100,request_id='reject-payment-001')).json['payment_id']
    second=p('book',dict(event=100,request_id='expire-payment-001')).json['payment_id']
    login(2)
    assert p('organizer/payments/review',dict(id=first,decision='rejected')).status_code==400
    assert p('organizer/payments/review',dict(id=first,decision='rejected',reason='Test rejection')).json['status']=='rejected'
    with module.app.app_context():
        module.db().execute("UPDATE demo_payments SET expires='2020-01-01' WHERE id=?",(second,));module.db().commit()
    assert p('organizer/payments/review',dict(id=second,decision='approved')).json['status']=='expired'
    login(10);assert c.get('/api/dashboard').json['bookings']==[]
    monkeypatch.setattr(module,'DEMO',False)
    assert p('book',dict(event=100,request_id='live-blocked-001')).status_code==400
    login(2);assert p('organizer/payments/review',dict(id=second,decision='approved')).status_code==400


def test_concurrent_approvals_cannot_oversell_or_duplicate(rec):
    c,p,login=paid(rec,capacity=2)
    ids=[p('book',dict(event=100,request_id='concurrent-payment-'+str(i))).json['payment_id'] for i in range(2)]
    def approve(pid):
        client=module.app.test_client()
        with client.session_transaction() as s:s.update(uid=2,version=1,csrf='parallel')
        return client.post('/api/organizer/payments/review',json=dict(id=pid,decision='approved'),headers={'X-CSRF-Token':'parallel'}).json
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(approve,ids*2))
    with module.app.app_context():
        assert module.one('SELECT COUNT(*) n FROM bookings WHERE event=100')['n']==2
        assert module.one("SELECT COUNT(*) n FROM demo_payments WHERE status='approved'")['n']==2
        assert module.one('SELECT points FROM users WHERE id=10')['points']==20


def test_price_and_rewards_rechecked_at_approval(rec):
    c,p,login=paid(rec)
    with module.app.app_context():module.db().execute('UPDATE users SET points=20 WHERE id=10');module.db().commit()
    first=p('book',dict(event=100,points=20,request_id='balance-recheck-1')).json['payment_id']
    with module.app.app_context():module.db().execute('UPDATE users SET points=0 WHERE id=10');module.db().commit()
    login(2);assert p('organizer/payments/review',dict(id=first,decision='approved')).json['status']=='rejected'
    login(10);second=p('book',dict(event=100,request_id='price-recheck-001')).json['payment_id']
    with module.app.app_context():module.db().execute('UPDATE events SET price=200 WHERE id=100');module.db().commit()
    login(2);assert p('organizer/payments/review',dict(id=second,decision='approved')).json['status']=='rejected'
    login(10);assert c.get('/api/dashboard').json['bookings']==[]
