import os, tempfile, sys
os.environ['DEMO_MODE']='1'
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
from app import app
c=app.test_client()
def post(path,data):
    token=c.get('/api/session').json['csrf']
    return c.post('/api/'+path,json=data,headers={'X-CSRF-Token':token})
def login(role):
    post('logout',{})
    assert post('login',{'email':role+'@smartevents.demo','password':'Smart@2026'}).status_code==200

def test_booking_points_and_qr():
    login('user')
    before=len(c.get('/api/dashboard').json['bookings'])
    payment=post('book',{'event':1,'qty':1,'points':0,'request_id':'flow-booking-001'})
    assert payment.status_code==200 and payment.json['status']=='pending'
    assert len(c.get('/api/dashboard').json['bookings'])==before
    login('organizer')
    assert post('organizer/payments/review',{'id':payment.json['payment_id'],'decision':'approved'}).json['status']=='approved'
    login('user')
    d=c.get('/api/dashboard').json;b=d['bookings'][0]
    assert d['user']['points']==79
    assert c.get('/api/qr/'+b['token']).mimetype=='image/png'
    assert post('book',{'event':1,'qty':1,'points':10000}).status_code==400
    assert c.get('/api/admin').status_code==403
    login('organizer')
    assert c.get('/api/qr/'+b['token']).status_code==400
    assert post('checkin',{'token':b['token'],'event':b['event']}).status_code==200
    assert post('checkin',{'token':b['token'],'event':b['event']}).status_code==400

def test_approval_and_capacity():
    login('organizer')
    d={'title':'Approval test','category':'Music','city':'Davangere','venue':'Test Hall','lat':14.4,'lon':75.9,'date':'2099-01-01T18:00','price':0,'capacity':1,'description':'Test'}
    assert post('organizer',d).status_code==200
    eid=c.get('/api/organizer').json['events'][0]['id']
    assert all(e['id']!=eid for e in c.get('/api/events').json)
    login('user');assert post('book',{'event':eid,'qty':1}).status_code==400
    login('admin');assert post('admin/review',{'type':'event','id':eid,'status':'approved'}).status_code==200
    login('user');assert post('book',{'event':eid,'qty':1}).status_code==200
    assert post('book',{'event':eid,'qty':1}).status_code==400

def test_otp_and_csrf():
    post('logout',{})
    assert c.post('/api/register',json={}).status_code==403
    assert post('register',{'email':'new@example.com','name':'New','password':'LongPassword123','confirm_password':'LongPassword123','role':'user'}).status_code==200
    assert post('login',{'email':'new@example.com','password':'LongPassword123'}).status_code==400
    code=post('otp/send',{'email':'new@example.com','purpose':'verify'}).json['demo_code']
    assert post('otp/verify',{'email':'new@example.com','purpose':'verify','code':code}).status_code==200
    assert post('otp/verify',{'email':'new@example.com','purpose':'verify','code':code}).status_code==400
    assert post('login',{'email':'new@example.com','password':'LongPassword123'}).status_code==200
    code=post('otp/send',{'email':'new@example.com','purpose':'reset'}).json['demo_code']
    assert post('otp/verify',{'email':'new@example.com','purpose':'reset','code':code,'password':'ChangedPass123'}).status_code==200
    assert c.get('/api/dashboard').status_code==401
    assert post('login',{'email':'new@example.com','password':'ChangedPass123'}).status_code==200

def test_connections_and_location():
    login('user');assert post('connect',{'id':2}).status_code==200
    login('organizer');assert post('connect',{'id':1,'action':'accepted'}).status_code==200
    assert any(x['status']=='accepted' for x in c.get('/api/community').json['connections'])
    e=c.get('/api/events?lat=14.464&lon=75.921&radius=20').json
    assert e and all(x['city']=='Davangere' for x in e)
    assert post('community',{'caption':'Invalid future memory','event':1,'audience':'community'}).status_code==400

def test_private_memories_and_photos():
    from app import db
    import base64,io
    from PIL import Image
    with app.app_context():
        db().execute("UPDATE events SET date='2020-01-01T12:00' WHERE id=1")
        db().commit()
    login('user')
    image=io.BytesIO();Image.new('RGB',(20,20),'green').save(image,format='PNG')
    assert post('community',{'event':1,'caption':'Private memory','audience':'connections','photo':base64.b64encode(image.getvalue()).decode()}).status_code==200
    pid=c.get('/api/community').json['posts'][0]['id']
    assert c.get('/api/photo/'+str(pid)).status_code==200
    login('organizer');assert any(p['id']==pid for p in c.get('/api/community').json['posts'])
    login('admin');assert all(p['id']!=pid for p in c.get('/api/community').json['posts'])
    assert c.get('/api/photo/'+str(pid)).status_code==400

def test_live_paid_checkout_is_disabled():
    import app as module
    login('user');old=module.DEMO;module.DEMO=False
    try:assert post('book',{'event':2,'qty':1}).status_code==400
    finally:module.DEMO=old
