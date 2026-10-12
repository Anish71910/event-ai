import base64
import io
from datetime import timedelta
from PIL import Image
from test_flows import c,post,login
from app import app,db
from client_updates import now

def event_data(**changes):
    data=dict(title='Client update event',category='Music',city='Davangere',venue='Test Hall',lat=14.4,lon=75.9,date=(now()+timedelta(days=10)).isoformat(timespec='minutes'),price=0,capacity=20,description='Test event',duration_minutes=180)
    data.update(changes)
    return data

def test_registration_confirmation():
    post('logout',{})
    data={'name':'Confirmation','email':'confirmation@example.com','password':'LongPassword123'}
    assert post('register',data).status_code==400
    data['confirm_password']='DifferentPassword'
    assert post('register',data).status_code==400
    data['confirm_password']=data['password']
    response=post('register',data)
    assert response.status_code==200
    assert 'successfully created' in response.json['message']

def test_duration_cover_and_admin_edit():
    login('organizer')
    im=io.BytesIO();Image.new('RGB',(30,20),'purple').save(im,'PNG')
    data=event_data(date='2099-01-01T11:00',cover=base64.b64encode(im.getvalue()).decode())
    r=post('organizer',data);assert r.status_code==200
    eid=r.json['id'];event=c.get('/api/organizer').json['events'][0]
    assert event['end_time']=='2099-01-01T14:00'
    assert event['has_image']
    post('logout',{});assert c.get(f'/api/event-image/{eid}').status_code==400
    login('admin');data.update(id=eid,title='Admin edited title')
    assert post('organizer',data).status_code==200
    assert post('admin/review',{'type':'event','id':eid,'status':'approved'}).status_code==200
    assert post('organizer',dict(data,title='Admin edits published event')).status_code==200
    assert next(e for e in c.get('/api/admin').json['events'] if e['id']==eid)['status']=='approved'
    login('user');assert c.get(f'/api/event-image/{eid}').status_code==200
    assert post('organizer',data).status_code==403
    login('organizer');assert post('organizer',data).status_code==200
    assert c.get('/api/organizer').json['events'][0]['status']=='pending'

def test_completion_notification_once_and_expiry():
    login('organizer');r=post('organizer',event_data());eid=r.json['id']
    with app.app_context():
        db().execute("UPDATE events SET status='approved',date=?,duration_minutes=1 WHERE id=?",((now()-timedelta(minutes=5)).isoformat(timespec='minutes'),eid));db().commit()
    for _ in range(2):app.extensions['eventai_complete_events']()
    with app.app_context():
        assert db().execute('SELECT COUNT(*) FROM event_completions WHERE event=?',(eid,)).fetchone()[0]==1
        assert db().execute("SELECT COUNT(*) FROM notices WHERE text LIKE 'Event completed: Client update event%'").fetchone()[0]==1
    assert all(e['id']!=eid for e in c.get('/api/events').json)
    assert post('organizer',event_data(id=eid)).status_code==400
    login('user');assert post('book',{'event':eid}).status_code==400

def test_image_validation_and_booked_edit_safety():
    login('organizer')
    assert post('organizer',event_data(cover='not an image')).status_code==400
    assert post('organizer',event_data(duration_minutes=-1)).status_code==400
    r=post('organizer',event_data());eid=r.json['id']
    login('admin');post('admin/review',{'type':'event','id':eid,'status':'approved'})
    login('user');assert post('book',{'event':eid,'qty':2}).status_code==200
    login('organizer')
    assert post('organizer',event_data(id=eid,capacity=1)).status_code==400
    assert post('organizer',event_data(id=eid,price=100)).status_code==400

def test_ongoing_event_and_cross_midnight():
    login('organizer');r=post('organizer',event_data(date='2099-01-01T23:00',duration_minutes=180));eid=r.json['id']
    event=c.get('/api/organizer').json['events'][0]
    assert event['end_time']=='2099-01-02T02:00'
    with app.app_context():
        db().execute("UPDATE events SET status='approved',date=? WHERE id=?",((now()-timedelta(minutes=15)).isoformat(timespec='minutes'),eid));db().commit()
    public=next(e for e in c.get('/api/events').json if e['id']==eid)
    assert public['lifecycle']=='ongoing'
    login('user');assert post('book',{'event':eid}).status_code==400
