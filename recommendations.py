"""Consent-aware interaction storage and bounded recommendation APIs."""
import hashlib
import re
import time
from collections import defaultdict
from flask import request, jsonify
from client_updates import now
from recommendation_engine import rank

RETENTION=90*86400
MAX_CATALOG=2000
MAX_SIGNALS=50000

def clean_query(value):
    # Avoid storing obvious emails, long numeric identifiers and URL parameters.
    value=re.sub(r'https?://\S+|\S+@\S+|\b\d{6,}\b',' ',value)
    return ' '.join(value.split())[:120]

def install(app,db,auth,user,one,rows,fail,limited):
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS recommendation_preferences(
            user INTEGER PRIMARY KEY REFERENCES users(id),enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN(0,1)),since REAL NOT NULL,updated REAL NOT NULL,booking_after_id INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS interactions(
            id INTEGER PRIMARY KEY,user INTEGER NOT NULL REFERENCES users(id),event INTEGER REFERENCES events(id),
            kind TEXT NOT NULL CHECK(kind IN('view','search','like','unlike','booking')),query TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL,dedupe TEXT NOT NULL UNIQUE);
        CREATE TABLE IF NOT EXISTS event_likes(
            user INTEGER NOT NULL REFERENCES users(id),event INTEGER NOT NULL REFERENCES events(id),created REAL NOT NULL,
            PRIMARY KEY(user,event));
        CREATE INDEX IF NOT EXISTS ix_interactions_user_time ON interactions(user,created);
        CREATE INDEX IF NOT EXISTS ix_interactions_time ON interactions(created);
        CREATE INDEX IF NOT EXISTS ix_interactions_event_kind ON interactions(event,kind,created);
        CREATE INDEX IF NOT EXISTS ix_likes_event ON event_likes(event);
        CREATE INDEX IF NOT EXISTS ix_bookings_user_event ON bookings(user,event,status);
        CREATE INDEX IF NOT EXISTS ix_bookings_event_status ON bookings(event,status);
        CREATE INDEX IF NOT EXISTS ix_events_status_date ON events(status,date);
        CREATE INDEX IF NOT EXISTS ix_notices_user_id ON notices(user,id);
        ''')
        if 'booking_after_id' not in {r[1] for r in db().execute('PRAGMA table_info(recommendation_preferences)')}:
            db().execute('ALTER TABLE recommendation_preferences ADD COLUMN booking_after_id INTEGER NOT NULL DEFAULT 0')
        db().commit()

    def preference(uid):
        return one('SELECT * FROM recommendation_preferences WHERE user=?',(uid,)) or {'enabled':0,'since':time.time(),'booking_after_id':0}

    def insert_signal(uid,kind,eid=None,query='',key=None):
        if not preference(uid)['enabled']:return False
        timestamp=time.time()
        if key is None:
            key=hashlib.sha256(f'{uid}:{kind}:{eid}:{query}:{int(timestamp//86400)}'.encode()).hexdigest()
        return db().execute('INSERT OR IGNORE INTO interactions(user,event,kind,query,created,dedupe) VALUES(?,?,?,?,?,?)',
                            (uid,eid,kind,query,timestamp,key)).rowcount==1

    def booking_signal(uid,eid,bid):
        # Called inside the booking transaction. A client cannot create this signal.
        insert_signal(uid,'booking',eid,key=f'booking:{bid}')
    app.extensions['eventai_record_booking']=booking_signal

    def prune():
        db().execute('DELETE FROM interactions WHERE created<?',(time.time()-RETENTION,))
        db().execute('DELETE FROM otps WHERE expires<?',(time.time(),))
        db().execute('DELETE FROM throttle WHERE until<?',(time.time(),))
        db().commit()
    app.extensions['eventai_prune_interactions']=prune

    @app.route('/api/recommendation-settings',methods=['GET','POST'])
    @auth()
    def settings():
        uid=user()['id']
        if request.method=='POST':
            d=request.get_json(silent=True)
            if not isinstance(d,dict) or type(d.get('enabled')) is not bool:return fail('Choose whether to enable personalization.')
            timestamp=time.time();p=preference(uid)
            booking_cutoff=p['booking_after_id'] if p['enabled'] and d['enabled'] else one('SELECT COALESCE(MAX(id),0) n FROM bookings WHERE user=?',(uid,))['n']
            db().execute('INSERT INTO recommendation_preferences(user,enabled,since,updated,booking_after_id) VALUES(?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET enabled=excluded.enabled,since=excluded.since,updated=excluded.updated,booking_after_id=excluded.booking_after_id',
                         (uid,int(d['enabled']),p['since'] if p['enabled'] and d['enabled'] else timestamp,timestamp,booking_cutoff))
            if not d['enabled']:
                db().execute('DELETE FROM interactions WHERE user=?',(uid,))
            db().commit()
        p=preference(uid)
        return jsonify(enabled=bool(p['enabled']),retention_days=90,
                       notice='Opt-in uses interests, views, searches, likes and confirmed bookings to personalize events. Turning off deletes tracked activity; saved likes and required booking records remain.')

    @app.post('/api/recommendation-history/clear')
    @auth()
    def clear_history():
        uid=user()['id'];p=preference(uid);timestamp=time.time()
        db().execute('DELETE FROM interactions WHERE user=?',(uid,))
        db().execute('DELETE FROM event_likes WHERE user=?',(uid,))
        booking_cutoff=one('SELECT COALESCE(MAX(id),0) n FROM bookings WHERE user=?',(uid,))['n']
        db().execute('INSERT INTO recommendation_preferences(user,enabled,since,updated,booking_after_id) VALUES(?,?,?,?,?) ON CONFLICT(user) DO UPDATE SET since=excluded.since,updated=excluded.updated,booking_after_id=excluded.booking_after_id',
                     (uid,p['enabled'],timestamp,timestamp,booking_cutoff));db().commit()
        return jsonify(message='Tracked activity and saved likes cleared. Booking records are retained; earlier bookings are no longer used for personalization.')

    @app.post('/api/interactions')
    @auth()
    def interaction():
        u=user();d=request.get_json(silent=True)
        if not isinstance(d,dict) or set(d)-{'kind','event','query'}:return fail('Invalid interaction fields.')
        kind=d.get('kind')
        if kind not in ('view','search'):return fail('Only views and searches can be submitted here.')
        if limited('interaction:'+str(u['id']),60,60):return jsonify(error='Too many interactions. Try again shortly.'),429
        if not preference(u['id'])['enabled']:return jsonify(recorded=False,reason='personalization_disabled')
        if kind=='view':
            if type(d.get('event')) is not int:return fail('Choose a valid event.')
            e=one("SELECT id FROM events WHERE id=? AND status='approved' AND datetime(date,'+'||duration_minutes||' minutes')>datetime(?)",(d['event'],now().isoformat()))
            if not e:return fail('Event unavailable.')
            recorded=insert_signal(u['id'],'view',e['id'])
        else:
            if not isinstance(d.get('query'),str) or len(d['query'])>120:return fail('Search must contain at most 120 characters.')
            query=clean_query(d['query'])
            if not query:return jsonify(recorded=False,reason='empty_search')
            recorded=insert_signal(u['id'],'search',query=query)
        db().commit();return jsonify(recorded=recorded)

    @app.route('/api/likes',methods=['GET','POST'])
    @auth()
    def likes():
        uid=user()['id']
        if request.method=='GET':return jsonify(events=[r['event'] for r in rows('SELECT event FROM event_likes WHERE user=? ORDER BY created DESC LIMIT 5000',(uid,))])
        d=request.get_json(silent=True)
        if not isinstance(d,dict) or set(d)-{'event','liked'} or type(d.get('event')) is not int or type(d.get('liked')) is not bool:
            return fail('Specify an event and true/false liked state.')
        if limited('like:'+str(uid),60,60):return jsonify(error='Too many updates. Try again shortly.'),429
        eid=d['event']
        if d['liked']:
            if not one("SELECT id FROM events WHERE id=? AND status='approved' AND datetime(date)>datetime(?)",(eid,now().isoformat())):return fail('Event unavailable.')
            changed=db().execute('INSERT OR IGNORE INTO event_likes VALUES(?,?,?)',(uid,eid,time.time())).rowcount
        else:changed=db().execute('DELETE FROM event_likes WHERE user=? AND event=?',(uid,eid)).rowcount
        if changed:insert_signal(uid,'like' if d['liked'] else 'unlike',eid)
        db().commit();return jsonify(liked=d['liked'],event=eid)

    def load_matrix():
        cutoff=time.time()-RETENTION
        # Only opted-in users contribute. Booking/like states are authoritative;
        # repeated client clicks cannot create duplicate purchases or like weight.
        signals=rows('''SELECT * FROM (
            SELECT i.user,i.event,1.0 weight,MAX(i.created) created FROM interactions i
            JOIN recommendation_preferences p ON p.user=i.user AND p.enabled=1
            JOIN users u ON u.id=i.user AND u.verified=1 AND u.role='user'
            WHERE i.kind='view' AND i.event IS NOT NULL AND i.created>=MAX(p.since,?) GROUP BY i.user,i.event
            UNION ALL
            SELECT l.user,l.event,4.0,l.created FROM event_likes l JOIN recommendation_preferences p ON p.user=l.user AND p.enabled=1
            JOIN users u ON u.id=l.user AND u.verified=1 AND u.role='user'
            WHERE l.created>=MAX(p.since,?)
            UNION ALL
            SELECT b.user,b.event,8.0,MAX(CAST(strftime('%s',b.created) AS REAL)) FROM bookings b
            JOIN recommendation_preferences p ON p.user=b.user AND p.enabled=1
            JOIN users u ON u.id=b.user AND u.verified=1 AND u.role='user'
            WHERE b.status='confirmed' AND b.id>p.booking_after_id AND CAST(strftime('%s',b.created) AS REAL)>=MAX(CAST(p.since AS INTEGER),?) GROUP BY b.user,b.event
        ) ORDER BY created DESC LIMIT ?''',(cutoff,cutoff,cutoff,MAX_SIGNALS))
        matrix=defaultdict(dict)
        for s in signals:
            decay=0.5**(max(0,time.time()-s['created'])/(30*86400))
            current=matrix[s['user']].get(s['event'],0)
            matrix[s['user']][s['event']]=min(12,current+s['weight']*decay)
        return matrix

    @app.get('/api/recommendations')
    @auth()
    def recommend():
        started=time.monotonic();u=user();p=preference(u['id'])
        if limited('recommend:'+str(u['id']),30,60):return jsonify(error='Please wait before refreshing recommendations.'),429
        try:limit=max(1,min(24,int(request.args.get('limit',12))))
        except ValueError:return fail('Invalid limit.')
        city=request.args.get('city','').strip()[:100];category=request.args.get('category','').strip()[:50]
        candidates=rows('''SELECT e.*,owner.name organizer,(SELECT available FROM event_inventory WHERE id=e.id) available
            FROM events e JOIN users owner ON owner.id=e.owner
            WHERE e.status='approved' AND datetime(e.date)>datetime(?)
            AND NOT EXISTS(SELECT 1 FROM bookings b WHERE b.event=e.id AND b.user=? AND b.status='confirmed')
            AND (?='' OR lower(e.city)=lower(?)) AND (?='' OR e.category=?)
            AND (SELECT available FROM event_inventory WHERE id=e.id)>0
            ORDER BY e.date,e.id LIMIT ?''',(now().isoformat(),u['id'],city,city,category,category,MAX_CATALOG))
        matrix=load_matrix()
        history_ids=list(matrix.get(u['id'],{}))[:500]
        history=[]
        if history_ids:history=rows('SELECT * FROM events WHERE id IN ('+','.join('?' for _ in history_ids)+')',history_ids)
        catalog=list({e['id']:e for e in candidates+history}.values())
        searches=[r['query'] for r in rows("SELECT query FROM interactions WHERE user=? AND kind='search' AND created>=? ORDER BY created DESC LIMIT 20",(u['id'],max(time.time()-RETENTION,p['since'])))]
        result,meta=rank(catalog,candidates,u['id'],matrix,interests=u['interests'],city=u['city'],searches=searches,personalized=bool(p['enabled']))
        booked=bool(one("SELECT id FROM bookings WHERE user=? AND status='confirmed' AND id>? AND CAST(strftime('%s',created) AS REAL)>=? LIMIT 1",(u['id'],p['booking_after_id'],max(time.time()-RETENTION,p['since']))))
        for item in result:
            if item['recommendation']['content']>0 and booked:item['recommendation']['reason']='Based on your previous bookings and interests'
        liked={r['event'] for r in rows('SELECT event FROM event_likes WHERE user=?',(u['id'],))}
        for e in result[:limit]:e['liked']=e['id'] in liked
        meta.update(candidate_count=len(candidates),candidate_limit=MAX_CATALOG,signal_limit=MAX_SIGNALS)
        app.logger.info('recommendations mode=%s candidates=%s elapsed_ms=%.1f',meta['mode'],len(candidates),(time.monotonic()-started)*1000)
        return jsonify(events=result[:limit],model=meta)

    @app.get('/api/admin/recommendation-stats')
    @auth(['admin'])
    def stats():
        return jsonify(consenting_users=one('SELECT COUNT(*) n FROM recommendation_preferences WHERE enabled=1')['n'],
                       interactions=rows('SELECT kind,COUNT(*) count FROM interactions WHERE created>=? GROUP BY kind',(time.time()-RETENTION,)),
                       saved_likes=one('SELECT COUNT(*) n FROM event_likes')['n'],retention_days=90)

    @app.cli.command('prune-interactions')
    def prune_command():
        """Delete raw recommendation interactions older than 90 days."""
        prune();print('Expired recommendation interactions removed.')

    # Retention is enforced on reads; physical cleanup at startup and by daily CLI.
    with app.app_context():prune()
