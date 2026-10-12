from test_recommendations import rec
import app as module
from assistant_features import predict

def test_mood_model():
    assert predict('stressed tired unwind')=='relaxed'
    assert predict('dance party excited')=='energetic'
    assert predict('learn coding skills')=='learning'
    assert predict('quantumxyz') is None

def test_assistant_filters(rec):
    c,p,login=rec
    with module.app.app_context():
        module.db().execute("UPDATE events SET price=300 WHERE id=100")
        module.db().execute("UPDATE events SET price=700 WHERE id=101")
        module.db().commit()
    r=p('event-assistant',{'message':'relaxed events in Davangere under 500'}).json
    assert [e['id'] for e in r['events']]==[100]
    assert p('event-assistant',{'message':'events in Mumbai under 500'}).json['events']==[]
    assert p('event-assistant',{'message':'events tomorrow'}).json['events']==[]
    assert p('event-assistant',{'message':'what is the price of Acoustic guitar night'}).json['answer']=='Acoustic guitar night: ₹300 per seat (from).'
    assert p('event-assistant',{'message':'write python code'}).json['events']==[]
    assert p('event-assistant',{'message':'events','filters':{'budget':-1}}).status_code==400
    with module.app.app_context():
        module.db().execute("UPDATE events SET capacity=0 WHERE id=100");module.db().commit()
    assert p('event-assistant',{'message':'relaxed events under 500'}).json['events']==[]

def test_notice_read_is_user_scoped(rec):
    c,p,login=rec
    with module.app.app_context():
        module.notice(10,'Ticket approved');module.notice(11,'Private notice');module.db().commit()
    d=c.get('/api/notice-status').json
    assert d['count']>0
    assert p('notice-status',{'through':d['latest']}).json['count']==0
    login(11);assert c.get('/api/notice-status').json['count']>0

def test_demo_otp_and_qr(rec,monkeypatch):
    c,p,login=rec
    with module.app.app_context():module.db().execute('UPDATE events SET price=100 WHERE id=100');module.db().commit()
    d={'event':100,'qty':1,'request_id':'demo-card-otp-test','method':'demo_card'}
    assert p('book',d).status_code==400
    assert p('book',dict(d,demo_otp='1234')).status_code==400
    r=p('book',dict(d,demo_otp='0000'));assert r.status_code==200,r.json
    assert p('book',dict(d,demo_otp='0000')).json['replayed']
    assert c.get('/api/demo-payment-qr').mimetype=='image/png'
    monkeypatch.setenv('PAYMENT_PROVIDER','razorpay')
    assert c.get('/api/demo-payment-qr').status_code==400
