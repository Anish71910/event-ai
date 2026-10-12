"""EventAI event lifecycle, safe cover uploads and organizer/admin editing."""
import base64
import io
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, send_file
from PIL import Image, ImageOps, UnidentifiedImageError

def now():
    # All event input/display is explicitly India Standard Time.
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

def install(app, db, auth, user, notice, fail, one, rows):
    with app.app_context():
        columns = {r[1] for r in db().execute('PRAGMA table_info(events)')}
        if 'duration_minutes' not in columns:
            db().execute('ALTER TABLE events ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 180')
        db().executescript('''
            CREATE TABLE IF NOT EXISTS event_images(event INTEGER PRIMARY KEY REFERENCES events(id), image BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS event_completions(event INTEGER PRIMARY KEY REFERENCES events(id), notified_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS event_gallery(id INTEGER PRIMARY KEY,event INTEGER NOT NULL REFERENCES events(id),image BLOB NOT NULL);
        ''')
        db().commit()

    def complete_events():
        # The unique event key prevents duplicate notifications across workers/restarts.
        with app.app_context():
            db().execute('BEGIN IMMEDIATE')
            ended = rows("SELECT id,owner,title FROM events WHERE status='approved' AND datetime(date,'+' || duration_minutes || ' minutes') <= datetime(?) AND id NOT IN (SELECT event FROM event_completions)", (now().isoformat(),))
            for e in ended:
                db().execute('INSERT INTO event_completions VALUES(?,?)', (e['id'], now().isoformat()))
                notice(e['owner'], 'Event completed: ' + e['title'] + '. Review your bookings and check-ins in Organizer Studio.')
            db().commit()

    app.extensions['eventai_complete_events'] = complete_events

    next_scan=[0.0]
    scan_lock=threading.Lock()
    @app.before_request
    def lifecycle():
        if request.path.startswith('/api/') and time.monotonic()>=next_scan[0] and scan_lock.acquire(blocking=False):
            try:
                complete_events();next_scan[0]=time.monotonic()+30
            finally:scan_lock.release()

    @app.after_request
    def enrich_events(response):
        if response.is_json and response.status_code == 200:
            def enrich(value):
                if isinstance(value, list):
                    return [enrich(v) for v in value]
                if isinstance(value, dict):
                    result = {k: enrich(v) for k,v in value.items()}
                    if 'date' in result and 'duration_minutes' in result:
                        start = datetime.fromisoformat(result['date'])
                        end = start + timedelta(minutes=result['duration_minutes'])
                        result.update(end_time=end.isoformat(timespec='minutes'), timezone='Asia/Kolkata',
                                      lifecycle='completed' if end <= now() else 'ongoing' if start <= now() else 'upcoming')
                        eid = result.get('event', result.get('id'))
                        result['has_image'] = bool(one('SELECT event FROM event_images WHERE event=?',(eid,)))
                        rating=one('SELECT COUNT(*) review_count,ROUND(AVG(rating),1) average_rating FROM event_reviews WHERE event=?',(eid,))
                        result.update(rating)
                        inventory=one('SELECT available,held FROM event_inventory WHERE id=?',(eid,))
                        if inventory:result.update(inventory)
                    return result
                return value
            response.set_data(app.json.dumps(enrich(response.get_json())))
        return response

    @app.get('/api/event-image/<int:eid>')
    def cover(eid):
        e=one('SELECT * FROM events WHERE id=?',(eid,)); u=user()
        private = u and u['verified'] and __import__('flask').session.get('version') == u['version'] and (u['role']=='admin' or u['id']==(e or {}).get('owner'))
        if not e or (e['status']!='approved' and not private):
            return fail('Image unavailable.')
        image=one('SELECT image FROM event_images WHERE event=?',(eid,))
        if not image:return fail('No cover uploaded.')
        return send_file(io.BytesIO(image['image']),mimetype='image/jpeg')

    def decode_image(data):
        try:
            raw=base64.b64decode(data.split(',')[-1],validate=True)
            if len(raw)>2*1024*1024:raise ValueError('Cover must be under 2 MB.')
            im=Image.open(io.BytesIO(raw))
            if im.format not in ('PNG','JPEG') or im.width*im.height>16000000:
                raise ValueError('Choose a JPEG/PNG under 16 megapixels.')
            im=ImageOps.exif_transpose(im).convert('RGB'); im.thumbnail((1600,1200))
            out=io.BytesIO(); im.save(out,format='JPEG',quality=85)
            return out.getvalue()
        except (UnidentifiedImageError,OSError,Image.DecompressionBombError,TypeError):
            raise ValueError('Choose a valid JPEG or PNG cover.')

    def visible_event(eid):
        e=one('SELECT * FROM events WHERE id=?',(eid,));u=user()
        return e if e and (e['status']=='approved' or u and u['verified'] and (u['role']=='admin' or u['id']==e['owner'])) else None

    @app.get('/api/gallery/<int:eid>')
    def gallery_list(eid):
        if not visible_event(eid):return fail('Event unavailable.')
        return jsonify(rows('SELECT id FROM event_gallery WHERE event=? ORDER BY id',(eid,)))

    @app.get('/api/gallery-image/<int:pid>')
    def gallery_image(pid):
        photo=one('SELECT * FROM event_gallery WHERE id=?',(pid,))
        if not photo or not visible_event(photo['event']):return fail('Image unavailable.')
        return send_file(io.BytesIO(photo['image']),mimetype='image/jpeg')

    @app.post('/api/gallery/<int:eid>')
    @auth(['organizer','admin'])
    def gallery_edit(eid):
        d=request.json;u=user()
        photo=None
        if not d.get('remove'):
            try:photo=decode_image(d.get('photo',''))
            except (ValueError,TypeError):return fail('Choose a JPEG/PNG under 2 MB and 16 megapixels.')
        db().execute('BEGIN IMMEDIATE')
        e=one('SELECT * FROM events WHERE id=?',(eid,))
        if not e or (u['role']!='admin' and e['owner']!=u['id']) or datetime.fromisoformat(e['date'])<=now():
            db().rollback();return fail('You cannot change images for this event.')
        if d.get('remove'):
            db().execute('DELETE FROM event_gallery WHERE id=? AND event=?',(d['remove'],eid))
        else:
            if one('SELECT COUNT(*) n FROM event_gallery WHERE event=?',(eid,))['n']>=6:
                db().rollback();return fail('Maximum six gallery photos. Remove one first.')
            db().execute('INSERT INTO event_gallery(event,image) VALUES(?,?)',(eid,photo))
        if u['role']!='admin':db().execute("UPDATE events SET status='pending' WHERE id=?",(eid,))
        db().commit();return jsonify(message='Gallery saved.'+(' Awaiting admin review.' if u['role']!='admin' else ''))

    @auth(['organizer','admin'])
    def organizer():
        u=user()
        if request.method=='GET':
            return jsonify(events=rows("SELECT e.*,COALESCE(SUM(CASE WHEN b.status='confirmed' THEN b.qty ELSE 0 END),0) sold,COALESCE(SUM(b.total),0) revenue FROM events e LEFT JOIN bookings b ON b.event=e.id WHERE e.owner=? GROUP BY e.id ORDER BY e.id DESC",(u['id'],)), approved=u['approved'])
        d=request.get_json(silent=True)
        if not isinstance(d,dict):return fail('Submit a valid event form.')
        try:
            for k in ('title','category','city','venue','date','description'):
                if not isinstance(d.get(k),str) or not d[k].strip():return fail('Complete all event fields.')
            start=datetime.fromisoformat(d['date'])
            if start.tzinfo or start<=now():return fail('Choose a future start time in India Standard Time.')
            price=int(d['price']);cap=int(d['capacity']);duration=int(d.get('duration_minutes',180))
            lat=float(d['lat']);lon=float(d['lon'])
            if price<0 or not 1<=cap<=100000 or not 1<=duration<=10080 or not -90<=lat<=90 or not -180<=lon<=180:
                return fail('Check price, capacity, coordinates and duration (1–10080 minutes).')
            photo=decode_image(d['cover']) if d.get('cover') else None
        except (ValueError,KeyError,TypeError,OverflowError):return fail('Check the event fields and upload a valid JPEG/PNG under 2 MB.')
        db().execute('BEGIN IMMEDIATE')
        old=None
        if d.get('id'):
            old=one('SELECT * FROM events WHERE id=?',(d['id'],))
            if not old or (u['role']!='admin' and old['owner']!=u['id']):
                db().rollback();return fail('You cannot edit this event.')
            if old['status']=='cancelled':db().rollback();return fail('Cancelled events are read-only.')
            plan=one('SELECT COUNT(*) n,MIN(price) price FROM event_seats WHERE event=?',(old['id'],))
            if plan['n'] and (cap!=plan['n'] or price!=plan['price']):db().rollback();return fail('For a seated event, edit capacity and prices through the seating plan.')
            if datetime.fromisoformat(old['date'])<=now():
                db().rollback();return fail('Started or completed events are read-only to preserve ticket history.')
            sold=one("SELECT COALESCE(SUM(qty),0) n FROM bookings WHERE event=? AND status='confirmed'",(old['id'],))['n']
            held=one('SELECT held FROM event_inventory WHERE id=?',(old['id'],))['held']
            if cap<sold+held or ((sold+held) and price!=old['price']):
                db().rollback();return fail('Capacity cannot be below bookings. Ticket price is locked after a booking.')
        status=old['status'] if old and u['role']=='admin' else 'pending'
        vals=(d['title'][:150],d['category'][:50],d['city'][:100],d['venue'][:150],lat,lon,start.isoformat(timespec='minutes'),price,cap,d['description'][:3000],duration,status)
        if old:
            eid=old['id']
            db().execute("UPDATE events SET title=?,category=?,city=?,venue=?,lat=?,lon=?,date=?,price=?,capacity=?,description=?,duration_minutes=?,status=?,reason='' WHERE id=?",vals+(eid,))
            for b in rows("SELECT user FROM bookings WHERE event=? AND status='confirmed' UNION SELECT user FROM demo_payments WHERE event=? AND status='pending' UNION SELECT user FROM razorpay_payments WHERE event=? AND status IN ('created','authorized','paid_pending')",(eid,eid,eid)):
                notice(b['user'], 'Event details updated: '+d['title']+'. Check your ticket for the latest venue and timings (IST).')
            notice(old['owner'],'Event updated: '+d['title']+('. Awaiting admin review.' if status=='pending' else '.'))
        else:
            eid=db().execute('INSERT INTO events(title,category,city,venue,lat,lon,date,price,capacity,description,duration_minutes,status,owner) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',vals+(u['id'],)).lastrowid
        if photo:
            db().execute('INSERT OR REPLACE INTO event_images VALUES(?,?)',(eid,photo))
        elif d.get('remove_cover'):
            db().execute('DELETE FROM event_images WHERE event=?',(eid,))
        db().commit()
        return jsonify(message='Event saved.'+(' Submitted for admin approval.' if status=='pending' else ''),id=eid)
    app.add_url_rule('/api/organizer',endpoint='organizer',view_func=organizer,methods=['GET','POST'])

    if os.getenv('EVENTAI_WORKER','1')=='1':
        def monitor():
            next_cleanup=0.0
            while True:
                try:
                    complete_events()
                    if time.monotonic()>=next_cleanup and 'eventai_prune_interactions' in app.extensions:
                        with app.app_context():app.extensions['eventai_prune_interactions']()
                        next_cleanup=time.monotonic()+3600
                except Exception:app.logger.exception('Completion notification check failed')
                threading.Event().wait(30)
        threading.Thread(target=monitor,name='eventai-completions',daemon=True).start()
