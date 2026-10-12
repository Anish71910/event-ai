import sqlite3
import time
from datetime import timedelta
import pytest
import app as module
from client_updates import now
from recommendation_engine import content_scores,collaborative_scores,rank
from production import validate_environment

@pytest.fixture
def rec(tmp_path,monkeypatch):
    # Clone schema/data to an isolated DB; never touch the user's database.
    path=str(tmp_path/'recommendations.db')
    source=sqlite3.connect(module.DB);dest=sqlite3.connect(path);source.backup(dest);source.close();dest.close()
    monkeypatch.setattr(module,'DB',path)
    with module.app.app_context():
        db=module.db()
        for table in ['seat_holds','razorpay_payments','demo_payments','interactions','event_likes','recommendation_preferences','booking_requests','bookings','event_completions']:
            db.execute('DELETE FROM '+table)
        db.execute("UPDATE events SET status='rejected'")
        for uid in (10,11,12):
            db.execute("INSERT INTO users(id,name,email,password,role,verified,approved,city,interests) VALUES(?,?,?,'unused','user',1,1,'Davangere',?)",(uid,'Test person',f'test{uid}@example.com','Music guitar' if uid==10 else ''))
        for eid,title,cat,desc in [(100,'Acoustic guitar night','Music','Guitar live acoustic performance'),(101,'Acoustic guitar workshop','Music','Learn guitar acoustic music'),(102,'Python data workshop','Technology','Coding software data science'),(103,'Jazz evening','Music','Live jazz saxophone'),(104,'Food festival','Food','Regional food tasting')]:
            db.execute("INSERT INTO events(id,owner,title,category,city,venue,lat,lon,date,price,capacity,description,status,duration_minutes) VALUES(?,2,?,?,'Davangere','Hall',14.4,75.9,?,0,100,?,'approved',180)",(eid,title,cat,(now()+timedelta(days=15)).isoformat(timespec='minutes'),desc))
        db.commit()
    client=module.app.test_client()
    def login(uid=10):
        with client.session_transaction() as s:s.clear();s.update(uid=uid,version=1,csrf='test-csrf')
    def post(path,data):return client.post('/api/'+path,json=data,headers={'X-CSRF-Token':'test-csrf'})
    login()
    return client,post,login

def test_content_and_collaborative_math():
    catalog=[dict(id=1,title='Acoustic guitar',category='Music',city='',venue='',description='live guitar',date='2099-01-01'),
             dict(id=2,title='Guitar workshop',category='Music',city='',venue='',description='acoustic guitar',date='2099-01-02'),
             dict(id=3,title='SQL coding',category='Technology',city='',venue='',description='databases python',date='2099-01-03')]
    scores=content_scores(catalog,catalog,'guitar','',{},[])
    assert scores[1]>scores[3] and scores[2]>scores[3]
    matrix={10:{1:8},11:{1:8,2:8},12:{3:8}}
    cf,n=collaborative_scores(10,matrix,catalog)
    assert n==1 and cf[2]>cf[3] and 0<=cf[2]<=1
    result,meta=rank(catalog,catalog[1:],10,matrix,interests='guitar')
    assert result[0]['id']==2 and meta['mode']=='hybrid'
    assert abs(sum(meta['weights'].values())-1)<1e-8

def test_consent_dedupe_and_spoofing(rec):
    c,p,login=rec
    assert p('interactions',{'kind':'view','event':100}).json['recorded'] is False
    assert p('recommendation-settings',{'enabled':True}).status_code==200
    assert p('interactions',{'kind':'view','event':100}).json['recorded'] is True
    assert p('interactions',{'kind':'view','event':100}).json['recorded'] is False
    assert p('interactions',{'kind':'booking','event':100}).status_code==400
    assert p('interactions',{'kind':'view','event':100,'user':11}).status_code==400
    assert p('interactions',{'kind':'view','event':999999}).status_code==400
    assert p('interactions',{'kind':'search','query':'guitar test@example.com 1234567890'}).status_code==200
    with module.app.app_context():
        saved=module.one("SELECT query FROM interactions WHERE user=10 AND kind='search'")['query']
        assert '@' not in saved and '1234567890' not in saved
    assert p('recommendation-settings',{'enabled':False}).status_code==200
    with module.app.app_context():assert module.one('SELECT COUNT(*) n FROM interactions WHERE user=10')['n']==0

def test_likes_are_idempotent_and_private(rec):
    c,p,login=rec
    for _ in range(3):assert p('likes',{'event':100,'liked':True}).status_code==200
    assert c.get('/api/likes').json['events']==[100]
    login(11);assert c.get('/api/likes').json['events']==[]
    login(10);assert p('likes',{'event':100,'liked':False}).status_code==200
    assert c.get('/api/likes').json['events']==[]
    assert c.post('/api/likes',json={'event':100,'liked':True}).status_code==403

def test_hybrid_uses_real_peer_signals(rec):
    c,p,login=rec
    for uid in (10,11):
        login(uid);p('recommendation-settings',{'enabled':True});p('likes',{'event':100,'liked':True})
    p('likes',{'event':101,'liked':True})
    login(10)
    result=c.get('/api/recommendations').json
    assert result['model']['mode']=='hybrid'
    event=next(e for e in result['events'] if e['id']==101)
    assert event['recommendation']['collaborative']>0
    assert event['recommendation']['content']>0
    assert 'password' not in str(result) and 'test11@example.com' not in str(result)
    login(11);p('recommendation-settings',{'enabled':False})
    login(10);assert c.get('/api/recommendations').json['model']['has_collaborative_signal'] is False

def test_cold_start_and_candidate_safety(rec):
    c,p,login=rec
    with module.app.app_context():
        db=module.db()
        db.execute("UPDATE events SET status='pending' WHERE id=100")
        db.execute("UPDATE events SET date='2020-01-01T11:00' WHERE id=101")
        db.execute('UPDATE events SET capacity=0 WHERE id=102');db.commit()
    result=c.get('/api/recommendations').json
    assert result['model']['mode']=='discovery'
    assert result['model']['personalized'] is False
    assert {e['id'] for e in result['events']}=={103,104}
    assert c.get('/api/recommendations?city=Mysuru').json['events']==[]

def test_booking_signal_and_retry_protection(rec):
    c,p,login=rec
    p('recommendation-settings',{'enabled':True})
    data={'event':100,'qty':1,'points':0,'request_id':'test-booking-unique'}
    assert p('book',data).status_code==200
    assert p('book',data).json['replayed'] is True
    assert p('book',dict(data,qty=2)).status_code==400
    pid=p('book',data).json['payment_id']
    login(2);assert p('organizer/payments/review',{'id':pid,'decision':'approved'}).status_code==200
    login(10)
    with module.app.app_context():
        assert module.one('SELECT COUNT(*) n FROM bookings WHERE user=10')['n']==1
        assert module.one("SELECT COUNT(*) n FROM interactions WHERE user=10 AND kind='booking'")['n']==1
    assert all(e['id']!=100 for e in c.get('/api/recommendations').json['events'])

def test_clear_history_excludes_earlier_bookings(rec):
    c,p,login=rec
    p('recommendation-settings',{'enabled':True});pid=p('book',{'event':100,'qty':1}).json['payment_id']
    login(2);p('organizer/payments/review',{'id':pid,'decision':'approved'});login(10)
    p('likes',{'event':101,'liked':True});p('recommendation-history/clear',{})
    with module.app.app_context():
        assert module.one('SELECT COUNT(*) n FROM bookings WHERE user=10')['n']==1
        assert module.one('SELECT COUNT(*) n FROM interactions WHERE user=10')['n']==0
        assert module.one('SELECT COUNT(*) n FROM event_likes WHERE user=10')['n']==0
        assert module.one('SELECT booking_after_id FROM recommendation_preferences WHERE user=10')['booking_after_id']>0
    assert c.get('/api/recommendations').json['model']['cold_start'] is True

def test_unauthorized_stats_bad_payload_and_rate_limit(rec):
    c,p,login=rec
    assert c.get('/api/admin/recommendation-stats').status_code==403
    assert c.post('/api/interactions',json=[],headers={'X-CSRF-Token':'test-csrf'}).status_code==400
    p('recommendation-settings',{'enabled':True})
    with module.app.app_context():
        module.db().execute('INSERT OR REPLACE INTO throttle VALUES(?,?,?)',('interaction:10',60,time.time()+60));module.db().commit()
    assert p('interactions',{'kind':'view','event':100}).status_code==429
    with c.session_transaction() as s:s.clear()
    assert c.get('/api/recommendations').status_code==401

def test_retention_cleanup(rec):
    c,p,login=rec
    p('recommendation-settings',{'enabled':True});p('interactions',{'kind':'view','event':100})
    with module.app.app_context():
        module.db().execute('UPDATE interactions SET created=?',(time.time()-100*86400,));module.db().commit()
        module.app.extensions['eventai_prune_interactions']()
        assert module.one('SELECT COUNT(*) n FROM interactions')['n']==0

def test_production_configuration_fail_closed():
    assert validate_environment({'APP_ENV':'production'})
    valid=dict(APP_ENV='production',DEMO_MODE='0',SECRET_KEY='abc123DEF456ghi789JKL012mno345PQR678',HTTPS_ONLY='1',TRUSTED_HOSTS='events.example.com',DATABASE='/var/lib/eventai/events.db',SMTP_USER='test@example.com',SMTP_PASSWORD='test-only')
    assert validate_environment(valid)==[]
    assert validate_environment(dict(valid,DEMO_MODE='1'))
    assert validate_environment(dict(valid,SECRET_KEY='short'))
    assert validate_environment(dict(valid,TRUSTED_HOSTS='*'))

def test_concurrent_booking_retries_are_atomic(rec):
    from concurrent.futures import ThreadPoolExecutor
    def book_once(_):
        c=module.app.test_client()
        with c.session_transaction() as s:s.update(uid=10,version=1,csrf='parallel')
        return c.post('/api/book',json={'event':100,'qty':1,'request_id':'same-concurrent-key'},headers={'X-CSRF-Token':'parallel'})
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(book_once,range(4)))
    assert all(r.status_code==200 for r in responses)
    assert sum(bool(r.json.get('replayed')) for r in responses)==3
    c,p,login=rec;login(2)
    assert p('organizer/payments/review',{'id':responses[0].json['payment_id'],'decision':'approved'}).status_code==200
    with module.app.app_context():assert module.one('SELECT COUNT(*) n FROM bookings WHERE user=10')['n']==1

def test_session_revocation_and_security_headers(rec):
    c,p,login=rec
    r=c.get('/healthz');assert r.status_code==200
    assert r.headers['X-Frame-Options']=='DENY'
    assert "object-src 'none'" in r.headers['Content-Security-Policy']
    assert r.headers['X-Request-ID']
    with module.app.app_context():
        module.db().execute('UPDATE users SET version=version+1 WHERE id=10');module.db().commit()
    assert c.get('/api/session').json['user'] is None
    assert c.get('/api/recommendations').status_code==401
