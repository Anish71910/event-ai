"""Provider calls are stubbed. No real keys, network traffic, or charges."""
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
import app as module
import razorpay_gateway as gateway
from test_recommendations import rec


@pytest.fixture
def rp(rec,monkeypatch):
    c,p,login=rec
    monkeypatch.setenv('PAYMENT_PROVIDER','razorpay')
    monkeypatch.setenv('RAZORPAY_KEY_ID','rzp_test_fixture')
    monkeypatch.setenv('RAZORPAY_KEY_SECRET','fixture-api-secret')
    monkeypatch.setenv('RAZORPAY_WEBHOOK_SECRET','fixture-webhook-secret')
    with module.app.app_context():
        module.db().execute('UPDATE events SET price=100 WHERE id=100');module.db().commit()
    state={'orders':{},'payments':{},'creates':0}
    def remote(method,path,data=None):
        if method=='POST' and path=='orders':
            state['creates']+=1;oid='order_Test'+str(state['creates'])
            state['orders'][oid]=dict(data,id=oid,status='created');return state['orders'][oid].copy()
        if path.startswith('payments/'):
            return state['payments'][path.split('/')[1]].copy()
        if path.startswith('orders/'):
            oid=path.split('/')[1]
            if path.endswith('/payments'):return {'items':[x.copy() for x in state['payments'].values() if x['order_id']==oid]}
            return state['orders'][oid].copy()
        raise AssertionError('Unexpected gateway request '+method+' '+path)
    monkeypatch.setattr(gateway,'gateway_api',remote)
    def create(key='checkout-test-0001',**extra):
        return p('razorpay/order',dict(event=100,qty=1,points=0,request_id=key,**extra))
    def payment(order,pid='pay_Test1',status='captured',**extra):
        entity=dict(id=pid,order_id=order['order_id'],amount=order['amount'],currency='INR',status=status,
                    captured=status=='captured',amount_refunded=0,method='upi');entity.update(extra)
        state['payments'][pid]=entity
        return dict(id=order['id'],razorpay_order_id=order['order_id'],razorpay_payment_id=pid,
                    razorpay_signature=hmac.new(b'fixture-api-secret',(order['order_id']+'|'+pid).encode(),hashlib.sha256).hexdigest())
    def webhook(pid='pay_Test1',event='payment.captured',signature=None):
        entity=state['payments'][pid]
        body=json.dumps({'event':event,'payload':{'payment':{'entity':entity},'refund':{'entity':{'payment_id':pid}}}}).encode()
        sig=signature or hmac.new(b'fixture-webhook-secret',body,hashlib.sha256).hexdigest()
        return c.post('/api/razorpay/webhook',data=body,content_type='application/json',headers={'X-Razorpay-Signature':sig})
    return c,p,login,state,create,payment,webhook


def test_real_checkout_capture_organizer_gate_and_idempotency(rp):
    c,p,login,state,create,payment,webhook=rp
    p('recommendation-settings',{'enabled':True})
    order=create(amount=1).json
    assert order['amount']==10000 and order['currency']=='INR'
    assert create().json['order_id']==order['order_id'] and state['creates']==1
    assert p('razorpay/order',dict(event=100,qty=2,request_id='checkout-test-0001')).status_code==400
    assert p('book',dict(event=100,request_id='bypass-demo-001')).status_code==400
    assert p('razorpay/verify',payment(order)).json['status']=='paid_pending'
    assert c.get('/api/dashboard').json['bookings']==[]
    assert p('organizer/razorpay/review',dict(id=order['id'],decision='approved')).status_code==403
    login(2)
    assert any('pay_Test1' in x['text'] for x in c.get('/api/dashboard').json['notices'])
    result=p('organizer/razorpay/review',dict(id=order['id'],decision='approved')).json
    assert result['status']=='approved'
    assert p('organizer/razorpay/review',dict(id=order['id'],decision='approved')).json['booking_id']==result['booking_id']
    login(10);d=c.get('/api/dashboard').json
    assert len(d['bookings'])==1 and d['user']['points']==10
    assert c.get('/api/qr/'+d['bookings'][0]['token']).status_code==200
    with module.app.app_context():assert module.one("SELECT COUNT(*) n FROM interactions WHERE user=10 AND kind='booking'")['n']==1


def test_signature_ownership_amount_currency_and_order_are_verified(rp):
    c,p,login,state,create,payment,webhook=rp
    order=create().json;data=payment(order)
    assert p('razorpay/verify',dict(data,razorpay_signature='0'*64)).status_code==400
    assert p('razorpay/verify',dict(data,razorpay_order_id='order_forged')).status_code==400
    login(11);assert p('razorpay/verify',data).status_code==400
    assert c.get('/api/razorpay/payments').json==[]
    login(10)
    for field,value in [('amount',1),('currency','USD'),('order_id','order_wrong')]:
        original=state['payments']['pay_Test1'][field];state['payments']['pay_Test1'][field]=value
        assert p('razorpay/verify',data).status_code==502
        state['payments']['pay_Test1'][field]=original
    assert c.get('/api/dashboard').json['bookings']==[]


def test_webhook_recovers_lost_browser_callback_and_deduplicates(rp):
    c,p,login,state,create,payment,webhook=rp
    order=create().json;payment(order)
    assert webhook(signature='a'*64).status_code==400
    assert webhook().status_code==200
    assert webhook().json['duplicate']
    assert c.get('/api/razorpay/payments').json[0]['status']=='paid_pending'
    assert c.get('/api/dashboard').json['bookings']==[]
    assert c.post('/api/razorpay/order',json={}).status_code==403
    login(2);assert p('organizer/razorpay/review',dict(id=order['id'],decision='approved')).status_code==200
    # Re-delivery must not issue another ticket or return the row to pending.
    assert webhook(event='order.paid').status_code==200
    login(10);assert len(c.get('/api/dashboard').json['bookings'])==1


def test_authorization_only_and_failed_attempt_retry(rp):
    c,p,login,state,create,payment,webhook=rp
    order=create().json
    assert p('razorpay/verify',payment(order,status='authorized')).json['status']=='authorized'
    login(2);assert p('organizer/razorpay/review',dict(id=order['id'],decision='approved')).status_code==400
    login(10);state['payments']['pay_Test1'].update(status='failed',captured=False)
    assert p('razorpay/refresh',dict(id=order['id'])).json['status']=='failed'
    assert p('razorpay/verify',payment(order,pid='pay_Test2')).json['status']=='paid_pending'
    assert webhook(pid='pay_Test1',event='payment.failed').status_code==200
    assert c.get('/api/razorpay/payments').json[0]['payment_id']=='pay_Test2'


def test_paid_rejection_requires_refund_and_never_issues_ticket(rp):
    c,p,login,state,create,payment,webhook=rp
    order=create().json;p('razorpay/verify',payment(order));login(2)
    assert p('organizer/razorpay/review',dict(id=order['id'],decision='rejected')).status_code==400
    assert p('organizer/razorpay/review',dict(id=order['id'],decision='rejected',reason='Event cannot proceed')).json['status']=='refund_required'
    state['payments']['pay_Test1'].update(status='refunded',captured=True,amount_refunded=10000)
    assert webhook(event='refund.processed').status_code==200
    login(10);assert c.get('/api/razorpay/payments').json[0]['status']=='refunded'
    assert c.get('/api/dashboard').json['bookings']==[]


def test_refund_revokes_existing_ticket_and_rewards_once(rp):
    c,p,login,state,create,payment,webhook=rp
    order=create().json;p('razorpay/verify',payment(order));login(2)
    p('organizer/razorpay/review',dict(id=order['id'],decision='approved'))
    login(10);token=c.get('/api/dashboard').json['bookings'][0]['token']
    state['payments']['pay_Test1'].update(status='refunded',captured=True,amount_refunded=10000)
    assert webhook(event='refund.processed').status_code==200
    assert p('razorpay/refresh',dict(id=order['id'])).json['status']=='refunded'
    d=c.get('/api/dashboard').json
    assert d['user']['points']==0 and d['bookings'][0]['status']=='cancelled'
    assert c.get('/api/qr/'+token).status_code==400
    login(2);assert p('checkin',{'token':token}).status_code==400


def test_concurrent_organizer_approvals_cannot_oversell(rp):
    c,p,login,state,create,payment,webhook=rp
    with module.app.app_context():module.db().execute('UPDATE events SET capacity=2 WHERE id=100');module.db().commit()
    ids=[]
    for i in range(2):
        order=create(key='parallel-order-'+str(i)).json
        p('razorpay/verify',payment(order,pid='pay_Parallel'+str(i)));ids.append(order['id'])
    def approve(pid):
        client=module.app.test_client()
        with client.session_transaction() as s:s.update(uid=2,version=1,csrf='parallel')
        return client.post('/api/organizer/razorpay/review',json=dict(id=pid,decision='approved'),headers={'X-CSRF-Token':'parallel'})
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(approve,ids*2))
    with module.app.app_context():
        assert module.one('SELECT COUNT(*) n FROM bookings WHERE event=100')['n']==2
        assert module.one("SELECT COUNT(*) n FROM razorpay_payments WHERE status='refund_required'")['n']==0


def test_order_timeout_is_not_retried_and_can_be_recovered(rp,monkeypatch):
    c,p,login,state,create,payment,webhook=rp
    remote=gateway.gateway_api
    def timeout(method,path,data=None):
        result=remote(method,path,data)
        if method=='POST':raise gateway.GatewayError('Network timeout')
        return result
    monkeypatch.setattr(gateway,'gateway_api',timeout)
    assert create().status_code==502
    assert create().json['status']=='creating' and state['creates']==1
    pid=c.get('/api/razorpay/payments').json[0]['id']
    login(3)
    result=p('admin/razorpay/recover-order',dict(id=pid,order_id='order_Test1'))
    assert result.status_code==200 and result.json['status']=='created'


def test_configuration_secret_exposure_and_live_guard(rp,monkeypatch):
    c,p,login,state,create,payment,webhook=rp
    config=c.get('/api/payment-config')
    assert config.json['enabled'] and config.json['mode']=='test'
    assert 'fixture-api-secret' not in config.text and 'fixture-webhook-secret' not in config.text
    assert 'checkout.razorpay.com' in c.get('/').headers['Content-Security-Policy']
    monkeypatch.setenv('RAZORPAY_KEY_ID','rzp_live_not_allowed_in_demo')
    assert not c.get('/api/payment-config').json['enabled']
    assert create().status_code==400


def test_late_capture_and_partial_refund_require_action(rp):
    c,p,login,state,create,payment,webhook=rp
    order=create().json
    with module.app.app_context():module.db().execute("UPDATE events SET date='2020-01-01' WHERE id=100");module.db().commit()
    assert p('razorpay/verify',payment(order)).json['status']=='refund_required'
    state['payments']['pay_Test1']['amount_refunded']=100
    assert webhook(event='refund.processed').status_code==200
    assert c.get('/api/razorpay/payments').json[0]['status']=='refund_review'
    assert c.get('/api/dashboard').json['bookings']==[]
