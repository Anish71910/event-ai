import io,subprocess,shutil
from datetime import timedelta
import pytest
from werkzeug.security import generate_password_hash
import app as module
from client_updates import now
from profile_media import normalize_clip
from test_recommendations import rec

def password_for_user():
    with module.app.app_context():
        module.db().execute('UPDATE users SET password=? WHERE id=10',(generate_password_hash('OriginalPassword123'),));module.db().commit()

def test_profile_password_revokes_other_sessions(rec):
    c,p,login=rec;password_for_user();other=module.app.test_client()
    with other.session_transaction() as s:s.update(uid=10,version=1,csrf='other')
    assert p('profile',{'name':'New Name','role':'admin','city':'Mangaluru'}).json['user']['role']=='user'
    payload=dict(current_password='wrong',new_password='NewPassword123',confirm_new_password='NewPassword123')
    assert p('profile',payload).status_code==400
    payload['current_password']='OriginalPassword123'
    assert p('profile',payload).status_code==200
    assert c.get('/api/profile').json['user']['name']=='New Name'
    assert other.get('/api/profile').status_code==401
    assert p('profile',{'current_password':[]}).status_code==400

def test_email_verification_and_replay(rec):
    c,p,login=rec;password_for_user()
    r=p('profile/email/send',dict(email='profile-change-unique@example.com',current_password='OriginalPassword123'))
    assert r.status_code==200 and r.json['demo_code']
    assert c.get('/api/profile').json['user']['email']=='test10@example.com'
    assert p('profile/email/confirm',dict(code='000000',current_password='OriginalPassword123')).status_code==400
    data=dict(code=r.json['demo_code'],current_password='OriginalPassword123')
    assert p('profile/email/confirm',data).json['user']['email']=='profile-change-unique@example.com'
    assert p('profile/email/confirm',data).status_code==400
    assert p('profile/email/send',dict(email='test11@example.com',current_password='OriginalPassword123')).status_code==400

def test_custom_categories_and_date_bounds(rec):
    c,p,login=rec;login(2);start=(now()+timedelta(days=21)).isoformat(timespec='minutes')
    event=dict(title='Pottery session',category='Pottery & Crafts',city='Mangaluru',venue='Studio',date=start,duration_minutes=90,capacity=10,price=0,lat=12.9,lon=74.8,description='Hands-on creative pottery workshop')
    created=p('organizer',event);assert created.status_code==200
    eid=created.json['id'];login(3)
    assert p('admin/review',dict(kind='event',id=eid,status='approved')).status_code==200
    assert any(x['name']==event['category'] for x in c.get('/api/categories').json)
    assert [x['id'] for x in c.get('/api/events',query_string={'date_from':start[:10],'date_to':start[:10]}).json]==[eid]
    assert c.get('/api/events?date_from=invalid').status_code==400
    assert c.get('/api/events?date_from=2099-02-01&date_to=2099-01-01').status_code==400

@pytest.fixture
def clip(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('FFmpeg unavailable')
    path=tmp_path/'sample.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=purple:s=160x240:r=10','-t','1','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(path)],check=True)
    return path.read_bytes()

def upload(c,kind,eid,raw,csrf='test-csrf'):
    return c.post(f'/api/clips/{kind}/{eid}',data={'clip':(io.BytesIO(raw),'video.mp4')},headers={'X-CSRF-Token':csrf},content_type='multipart/form-data')

def test_event_clip_permissions_normalization_review_and_range(rec,clip):
    c,p,login=rec
    assert upload(c,'event',100,clip).status_code==403
    login(2)
    assert upload(c,'event',100,clip,csrf='wrong').status_code==403
    assert upload(c,'event',100,b'not a video').status_code==400
    assert upload(c,'event',100,clip).status_code==200
    assert c.get('/api/clips/event/100/info').json['clip']['duration']>0
    response=c.get('/api/clips/event/100',headers={'Range':'bytes=0-99'})
    assert response.status_code==206 and len(response.data)==100 and response.mimetype=='video/mp4'
    with module.app.app_context():assert module.one('SELECT status FROM events WHERE id=100')['status']=='pending'
    login(10);assert c.get('/api/clips/event/100').status_code==404
    login(3);p('admin/review',dict(kind='event',id=100,status='approved'))
    login(10);assert c.get('/api/clips/event/100').status_code==200
    login(2);assert p('clips/event/100',{'remove':1}).status_code==200
    assert c.get('/api/clips/event/100/info').json['clip'] is None

def test_memory_clip_connections_privacy(rec,clip):
    c,p,login=rec
    with module.app.app_context():
        pid=module.db().execute("INSERT INTO posts(user,event,caption,audience) VALUES(10,100,'A good memory','connections')").lastrowid;module.db().commit()
    assert upload(c,'memory',pid,clip).status_code==200
    assert any(x['id']==pid and x['has_clip'] for x in c.get('/api/community').json['posts'])
    login(11);assert c.get(f'/api/clips/memory/{pid}').status_code==404
    assert upload(c,'memory',pid,clip).status_code==403
    with module.app.app_context():
        module.db().execute("INSERT INTO connections(sender,recipient,status) VALUES(10,11,'accepted')");module.db().commit()
    assert c.get(f'/api/clips/memory/{pid}').status_code==200

def test_clip_size_and_missing_encoder(monkeypatch):
    if shutil.which('ffmpeg'):
        with pytest.raises(ValueError,match='20 MB'):normalize_clip(b'x'*(20*1024*1024+1))
    monkeypatch.setattr(shutil,'which',lambda _:None)
    with pytest.raises(ValueError,match='FFmpeg'):normalize_clip(b'video')

def test_long_clip_rejected_before_encoding(tmp_path):
    if not shutil.which('ffmpeg'):pytest.skip('FFmpeg unavailable')
    path=tmp_path/'long.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=purple:s=32x32:r=1','-t','61','-c:v','libx264','-threads','1',str(path)],check=True)
    with pytest.raises(ValueError,match='60 seconds'):normalize_clip(path.read_bytes())

def test_multipart_is_limited_to_clip_routes(rec):
    c,p,login=rec
    assert c.post('/api/profile',data={'name':'Bypass'},content_type='multipart/form-data',headers={'X-CSRF-Token':'test-csrf'}).status_code==400
    login(2)
    assert upload(c,'event',100,b'x'*(21*1024*1024+1)).status_code==413
