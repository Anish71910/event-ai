import os, sqlite3, secrets, time, math, smtplib, io, ssl
from pathlib import Path
from datetime import datetime, timedelta
from client_updates import now
from email.message import EmailMessage
from functools import wraps
from flask import Flask, request, jsonify, session, g, render_template, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
import qrcode
import base64
from PIL import Image, UnidentifiedImageError
load_dotenv()
from production import validate_environment
configuration_errors=validate_environment()
if configuration_errors:raise RuntimeError('Production startup blocked: '+' '.join(configuration_errors))
ROOT=Path(__file__).parent
app=Flask(__name__)
(ROOT/'instance').mkdir(exist_ok=True)
key=ROOT/'instance'/'secret.key'
if not key.exists(): key.write_text(secrets.token_hex(32))
app.config.update(SECRET_KEY=os.getenv('SECRET_KEY') or key.read_text(), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=os.getenv('HTTPS_ONLY')=='1', MAX_CONTENT_LENGTH=5*1024*1024)
DB=os.getenv('DATABASE',str(ROOT/'instance'/'events.db'))
DEMO=os.getenv('DEMO_MODE','1')=='1'
def db():
    if 'db' not in g:
        g.db=sqlite3.connect(DB,timeout=15); g.db.row_factory=sqlite3.Row; g.db.execute('PRAGMA foreign_keys=ON')
    return g.db
@app.teardown_appcontext
def close(_):
    if 'db' in g:g.db.close()
def rows(sql,args=()):return [dict(r) for r in db().execute(sql,args).fetchall()]
def one(sql,args=()):
    r=db().execute(sql,args).fetchone();return dict(r) if r else None
def user():
    u=one('SELECT id,name,email,role,verified,approved,points,city,interests,version FROM users WHERE id=? AND disabled=0',(session.get('uid',0),))
    return u if u and session.get('version')==u['version'] else None
def auth(role=None):
    def wrap(fn):
        @wraps(fn)
        def run(*a,**kw):
            u=user()
            if not u or session.get('version')!=u['version']:return jsonify(error='Please sign in.'),401
            if not u['verified']:return jsonify(error='Verify your email first.'),403
            if role and u['role'] not in role:return jsonify(error='Access denied.'),403
            return fn(*a,**kw)
        return run
    return wrap
def fail(msg):return jsonify(error=msg),400
@app.before_request
def csrf():
    # Razorpay authenticates this one endpoint with a raw-body HMAC, not a browser session.
    if request.method=='POST' and request.path=='/api/razorpay/webhook':return
    if request.method in ('POST','PUT','PATCH','DELETE') and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','invalid')):return jsonify(error='Refresh the page and try again.'),403
@app.after_request
def headers(r):
    r.headers['X-Content-Type-Options']='nosniff';r.headers['X-Frame-Options']='DENY';r.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    if request.path.startswith('/api'):r.headers['Cache-Control']='no-store'
    return r
@app.errorhandler(ValueError)
def invalid(e):return fail('Please enter valid values.')
@app.errorhandler(sqlite3.IntegrityError)
def conflict(e):return fail('That record already exists or is invalid.')
def setup():
    with app.app_context():
        db().execute('PRAGMA journal_mode=WAL')
        db().executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,email TEXT UNIQUE,password TEXT,role TEXT,verified INTEGER DEFAULT 0,approved INTEGER DEFAULT 0,points INTEGER DEFAULT 0,city TEXT DEFAULT '',interests TEXT DEFAULT '',version INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,owner INTEGER REFERENCES users(id),title TEXT,category TEXT,city TEXT,venue TEXT,lat REAL,lon REAL,date TEXT,price INTEGER,capacity INTEGER,description TEXT,status TEXT DEFAULT 'pending',reason TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY,user INTEGER REFERENCES users(id),event INTEGER REFERENCES events(id),qty INTEGER,total INTEGER,redeemed INTEGER,earned INTEGER,token TEXT UNIQUE,used INTEGER DEFAULT 0,status TEXT DEFAULT 'confirmed',created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,user INTEGER,amount INTEGER,label TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS otps(email TEXT,purpose TEXT,hash TEXT,expires REAL,attempts INTEGER,sent REAL,PRIMARY KEY(email,purpose));
        CREATE TABLE IF NOT EXISTS connections(sender INTEGER,recipient INTEGER,status TEXT DEFAULT 'pending',PRIMARY KEY(sender,recipient));
        CREATE TABLE IF NOT EXISTS posts(id INTEGER PRIMARY KEY,user INTEGER,event INTEGER,caption TEXT,audience TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS notices(id INTEGER PRIMARY KEY,user INTEGER,text TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS throttle(key TEXT PRIMARY KEY,count INTEGER,until REAL);
        CREATE TABLE IF NOT EXISTS booking_requests(user INTEGER REFERENCES users(id),request_id TEXT,payload TEXT,booking INTEGER REFERENCES bookings(id),PRIMARY KEY(user,request_id));
        ''')
        if 'photo' not in [r[1] for r in db().execute('PRAGMA table_info(posts)')]: db().execute('ALTER TABLE posts ADD COLUMN photo BLOB')
        if DEMO and not one('SELECT id FROM users LIMIT 1'):
            admin_password = os.getenv(
            "DEMO_ADMIN_PASSWORD", "Smart@2026"
        )

        for name, email, role in [
            ('Tejas', 'user@smartevents.demo', 'user'),
            ('Prerana Experiences', 'organizer@smartevents.demo', 'organizer'),
            ('Smart Events Admin', 'admin@smartevents.demo', 'admin')
         ]:
            db().execute('INSERT OR IGNORE INTO users(name,email,password,role,verified,approved,city,interests) VALUES(?,?,?,?,1,1,?,?)'(name,email,generate_password_hash(admin_password if role == 'admin' else 'Smart@2026' ),role,'Davangere','Music')
                        )
            data=[('Golden Hour Live','Music','Bengaluru','Palace Grounds',12.998,77.592,799,500,'An open-air evening of live music, warm lights and unforgettable company.'),('Create Tomorrow','Technology','Davangere','Innovation Centre',14.464,75.921,299,150,'Meet builders, explore AI and turn your next big idea into something real.'),('The Taste Collective','Food','Bengaluru','Lakeside Pavilion',12.971,77.594,499,250,'Discover regional flavours and chef-led experiences in a beautiful outdoor setting.'),('Canvas & Connections','Art','Mysuru','Heritage Studio',12.295,76.639,399,80,'A relaxed creative gathering with guided painting and a community of curious minds.'),('Sunrise Trail','Outdoors','Davangere','Kunduvada Lake',14.439,75.911,0,60,'Start your weekend with a lakeside walk, fresh air and new connections.'),('Founders After Hours','Business','Bengaluru','The Assembly',12.96,77.60,599,100,'Meaningful conversations with local founders and independent creators.')]
            for i,e in enumerate(data):
                title,cat,city,venue,lat,lon,price,cap,desc=e
                db().execute('INSERT INTO events(owner,title,category,city,venue,lat,lon,date,price,capacity,description,status) VALUES(2,?,?,?,?,?,?,?,?,?,?,?)',(title,cat,city,venue,lat,lon,(now()+timedelta(days=7+i*3)).isoformat(timespec='minutes'),price,cap,desc,'approved'))
        db().commit()
def notice(uid,text):db().execute('INSERT INTO notices(user,text) VALUES(?,?)',(uid,text))
def limited(key,limit=8,seconds=900):
    timestamp=time.time();db().execute('BEGIN IMMEDIATE');r=one('SELECT * FROM throttle WHERE key=?',(key,))
    if not r or r['until']<timestamp:db().execute('INSERT OR REPLACE INTO throttle VALUES(?,1,?)',(key,timestamp+seconds));db().commit();return False
    db().execute('UPDATE throttle SET count=count+1 WHERE key=?',(key,));db().commit();return r['count']>=limit
@app.get('/')
def home():return render_template('index.html')
@app.get('/api/session')
def state():
    session.setdefault('csrf',secrets.token_hex(24));return jsonify(user=user(),csrf=session['csrf'],demo=DEMO)
@app.post('/api/login')
def login():
    d=request.json;email=d.get('email','').strip().lower()
    if limited('login:'+request.remote_addr+':'+email):return fail('Too many attempts. Try again in 15 minutes.')
    u=one('SELECT * FROM users WHERE email=?',(email,))
    if not u or not check_password_hash(u['password'],d.get('password','')):return fail('Incorrect email or password.')
    if u.get('disabled'):return fail('This account is disabled. Contact the administrator.')
    if not u['verified']:return fail('Verify your email using the verification tab.')
    session.clear();session['uid']=u['id'];session['version']=u['version'];session['csrf']=secrets.token_hex(24);session.permanent=True
    return jsonify(user=user())
@app.post('/api/logout')
def logout():session.clear();return jsonify(ok=True)
@app.post('/api/register')
def register():
    d=request.json;email=d.get('email','').strip().lower();password=d.get('password','')
    if password != d.get('confirm_password'):return fail('Passwords do not match.')
    if '@' not in email or len(password)<10 or not d.get('name','').strip():return fail('Enter a name, valid email and password of at least 10 characters.')
    if limited('register:'+request.remote_addr):return fail('Please try again later.')
    role=d.get('role','user')
    if role not in ['user','organizer']:return fail('Invalid role.')
    db().execute('INSERT INTO users(name,email,password,role,city,interests) VALUES(?,?,?,?,?,?)',(d['name'][:100],email,generate_password_hash(password),role,d.get('city','')[:100],d.get('interests','')[:200]));db().commit()
    return jsonify(ok=True,message='Account successfully created! Please verify your email to sign in.')
@app.post('/api/otp/send')
def sendotp():
    d=request.json;email=d.get('email','').lower().strip();purpose=d.get('purpose','verify')
    if purpose not in ['verify','reset']:return fail('Invalid request.')
    if limited('otp:'+request.remote_addr,10):return fail('Please try again in 15 minutes.')
    prev=one('SELECT * FROM otps WHERE email=? AND purpose=?',(email,purpose))
    if prev and time.time()-prev['sent']<60:return fail('Wait 60 seconds before resending.')
    u=one('SELECT id FROM users WHERE email=?',(email,));response={'message':'If the account exists, a code has been sent.'}
    if u:
        code=str(secrets.randbelow(900000)+100000)
        if os.getenv('SMTP_PASSWORD'):
            msg=EmailMessage();msg['Subject']='Smart Events verification code';msg['From']=os.getenv('SMTP_USER','smartevent26@gmail.com');msg['To']=email;msg.set_content(f'Your Smart Events code is {code}. It expires in 10 minutes. Do not share it.')
            try:
                with smtplib.SMTP(os.getenv('SMTP_HOST','smtp.gmail.com'),int(os.getenv('SMTP_PORT','587')),timeout=15) as smtp:
                    smtp.starttls(context=ssl.create_default_context());smtp.login(msg['From'],os.environ['SMTP_PASSWORD']);smtp.send_message(msg)
            except Exception:return fail('Email delivery failed. Check server email configuration.')
        elif DEMO:response['demo_code']=code;response['message']='Development code only — no email was sent.'
        else:return fail('Email service is not configured.')
        db().execute('INSERT OR REPLACE INTO otps VALUES(?,?,?,?,0,?)',(email,purpose,generate_password_hash(code),time.time()+600,time.time()));db().commit()
    return jsonify(response)
@app.post('/api/otp/verify')
def verifyotp():
    d=request.json;email=d.get('email','').lower().strip();purpose=d.get('purpose')
    db().execute('BEGIN IMMEDIATE');r=one('SELECT * FROM otps WHERE email=? AND purpose=?',(email,purpose))
    if not r or r['expires']<time.time() or r['attempts']>=5:db().rollback();return fail('Code expired or attempts exhausted. Request another code.')
    db().execute('UPDATE otps SET attempts=attempts+1 WHERE email=? AND purpose=?',(email,purpose))
    if not check_password_hash(r['hash'],d.get('code','')):db().commit();return fail('Incorrect code.')
    if purpose=='reset':
        if len(d.get('password',''))<10:db().commit();return fail('Password must have at least 10 characters.')
        db().execute('UPDATE users SET password=?,version=version+1 WHERE email=?',(generate_password_hash(d['password']),email))
    else:db().execute('UPDATE users SET verified=1 WHERE email=?',(email,))
    db().execute('DELETE FROM otps WHERE email=? AND purpose=?',(email,purpose));db().commit();return jsonify(message='Success. You can now sign in.')
@app.get('/api/events')
def events():
    q=request.args.get('q','').lower()[:120];cat=request.args.get('category','')[:50];city=request.args.get('city','').lower()[:100];u=user()
    limit=max(1,min(100,int(request.args.get('limit',48))));offset=max(0,min(100000,int(request.args.get('offset',0))))
    minimum=float(request.args.get('min_price',0));maximum=float(request.args.get('max_price',100000))
    if not math.isfinite(minimum) or not math.isfinite(maximum) or not 0<=minimum<=maximum<=100000:return fail('Invalid price range.')
    date_from=request.args.get('date_from','');date_to=request.args.get('date_to','')
    try:
        for value in (date_from,date_to):
            if value and (len(value)!=10 or datetime.fromisoformat(value).strftime('%Y-%m-%d')!=value):raise ValueError()
        if date_from and date_to and date_from>date_to:raise ValueError()
    except ValueError:return fail('Choose valid date bounds in YYYY-MM-DD order.')
    data=rows("SELECT e.*,u.name organizer,(SELECT available FROM event_inventory WHERE id=e.id) available FROM events e JOIN users u ON u.id=e.owner WHERE e.status='approved' AND datetime(e.date,'+' || e.duration_minutes || ' minutes')>datetime(?) AND (?='' OR e.category=?) AND instr(lower(e.city),?)>0 AND instr(lower(e.title||' '||e.venue||' '||e.description),?)>0 AND e.price BETWEEN ? AND ? AND (?='' OR date(e.date)>=?) AND (?='' OR date(e.date)<=?) ORDER BY e.date,e.id LIMIT 2000",(now().isoformat(timespec='minutes'),cat,cat,city,q,minimum,maximum,date_from,date_from,date_to,date_to))
    data=[e for e in data if q in (e['title']+' '+e['venue']+' '+e['description']).lower() and (not cat or e['category']==cat) and city in e['city'].lower()]
    for e in data:e['match']=bool(u and e['category'] in u['interests'])
    if request.args.get('lat'):
        lat=float(request.args['lat']);lon=float(request.args.get('lon','nan'));radius=float(request.args.get('radius',50))
        if not -90<=lat<=90 or not -180<=lon<=180 or not 0<radius<=500:return fail('Invalid location or radius (maximum 500 km).')
        for e in data:
            a=math.sin(math.radians(e['lat']-lat)/2)**2+math.cos(math.radians(lat))*math.cos(math.radians(e['lat']))*math.sin(math.radians(e['lon']-lon)/2)**2;e['distance']=round(6371*2*math.asin(min(1,math.sqrt(a))),1)
        data=[e for e in data if e['distance']<=radius];data.sort(key=lambda e:e['distance'])
    else:data.sort(key=lambda e:(e['date'],e['id']))
    return jsonify(data[offset:offset+limit])
@app.get('/api/dashboard')
@auth()
def dashboard():
    u=user();return jsonify(user=u,bookings=rows('SELECT b.*,e.title,e.date,e.duration_minutes,e.venue,e.city FROM bookings b JOIN events e ON e.id=b.event WHERE b.user=? ORDER BY b.id DESC',(u['id'],)),ledger=rows('SELECT * FROM ledger WHERE user=? ORDER BY id DESC',(u['id'],)),notices=rows('SELECT * FROM notices WHERE user=? ORDER BY id DESC LIMIT 30',(u['id'],)))
@app.get('/api/qr/<token>')
@auth()
def qr(token):
    if not one("SELECT id FROM bookings WHERE token=? AND user=? AND status='confirmed'",(token,user()['id'])):return fail('Ticket unavailable or cancelled.')
    out=io.BytesIO();qrcode.make(token).save(out,format='PNG');out.seek(0);return send_file(out,mimetype='image/png')
@app.post('/api/predict')
@auth(['organizer','admin'])
def predict():
    d=request.json;price=max(0,int(d.get('price',0)));cap=max(1,int(d.get('capacity',1)));city=d.get('city','');cat=d.get('category','');score=55
    score+=10 if city.lower() in ['bengaluru','davangere','mysuru'] else 0;score+=10 if price<=500 else -10;score+=5 if cap<=250 else -5
    return jsonify(score=score,attendance=round(cap*score/100),revenue=round(cap*score/100)*price,method='Rule-based planning estimate; not a trained ML forecast',confidence='Low — historical training data is not available',tips=['Use an accessible venue and a clear location.','Compare pricing with similar events.','Promote early and review actual booking demand.'])
@app.get('/api/admin')
@auth(['admin'])
def admin():return jsonify(events=rows('SELECT e.*,u.name organizer FROM events e JOIN users u ON u.id=e.owner ORDER BY e.id DESC'),organizers=rows("SELECT id,name,email,approved FROM users WHERE role='organizer'"),stats=one("SELECT COUNT(*) bookings,COALESCE(SUM(total),0) revenue,COALESCE(SUM(qty),0) tickets FROM bookings WHERE status='confirmed'"),users=one('SELECT COUNT(*) n FROM users')['n'])
@app.post('/api/admin/review')
@auth(['admin'])
def review():
    d=request.json
    if d.get('type')=='organizer':db().execute("UPDATE users SET approved=1 WHERE id=? AND role='organizer'",(d['id'],));notice(d['id'],'Your organizer account has been approved.')
    else:
        e=one('SELECT e.*,u.approved FROM events e JOIN users u ON u.id=e.owner WHERE e.id=?',(d['id'],))
        if not e:return fail('Event not found.')
        if e['status']=='cancelled':return fail('Cancelled events cannot be republished.')
        if d.get('status') not in ['approved','rejected']:return fail('Invalid review.')
        if d['status']=='approved' and not e['approved']:return fail('Verify and approve the organizer first.')
        if d['status']=='rejected' and not d.get('reason','').strip():return fail('Please provide a rejection reason.')
        db().execute('UPDATE events SET status=?,reason=? WHERE id=?',(d['status'],d.get('reason','')[:500],e['id']));notice(e['owner'],e['title']+': '+d['status']+' '+d.get('reason',''))
    if d.get('type')!='organizer':
        for attendee in rows("SELECT user FROM bookings WHERE event=? UNION SELECT user FROM demo_payments WHERE event=? AND status='pending' UNION SELECT user FROM razorpay_payments WHERE event=? AND status IN ('created','authorized','paid_pending')",(e['id'],e['id'],e['id'])):
            notice(attendee['user'],'Event update: '+e['title']+' is now '+d['status']+'. '+d.get('reason',''))
    db().commit();return jsonify(message='Review saved.')
@app.route('/api/community',methods=['GET','POST'])
@auth()
def community():
    uid=user()['id']
    if request.method=='POST':
        d=request.json
        if not d.get('caption','').strip() or d.get('audience') not in ['community','connections']:return fail('Enter a caption and audience.')
        if not one("SELECT b.id FROM bookings b JOIN events e ON e.id=b.event WHERE b.user=? AND b.event=? AND b.status='confirmed' AND datetime(e.date,'+' || e.duration_minutes || ' minutes')<datetime(?)",(uid,d.get('event'),now().isoformat())):return fail('Memories require a booking for a past event.')
        photo=None
        if d.get('photo'):
            try:
                raw=base64.b64decode(d['photo'].split(',')[-1],validate=True)
                if len(raw)>2*1024*1024:return fail('Choose a photo under 2 MB.')
                im=Image.open(io.BytesIO(raw))
                if im.width*im.height>16000000:return fail('Photo dimensions are too large.')
                im=im.convert('RGB');im.thumbnail((1400,1400));out=io.BytesIO();im.save(out,format='JPEG',quality=85);photo=out.getvalue()
            except (ValueError,UnidentifiedImageError,OSError):return fail('Choose a valid JPEG or PNG photo.')
        pid=db().execute('INSERT INTO posts(user,event,caption,audience,photo) VALUES(?,?,?,?,?)',(uid,d['event'],d['caption'][:2000],d['audience'],photo)).lastrowid;db().commit();return jsonify(message='Memory published.',id=pid)
    posts=rows("SELECT p.id,p.user,p.event,p.caption,p.audience,p.created,(p.photo IS NOT NULL) has_photo,EXISTS(SELECT 1 FROM clips WHERE kind='memory' AND target=p.id) has_clip,u.name,e.title FROM posts p JOIN users u ON u.id=p.user JOIN events e ON e.id=p.event WHERE p.audience='community' OR p.user=? OR EXISTS(SELECT 1 FROM connections c WHERE c.status='accepted' AND ((c.sender=? AND c.recipient=p.user) OR (c.recipient=? AND c.sender=p.user))) ORDER BY p.id DESC",(uid,uid,uid))
    return jsonify(posts=posts,people=rows('SELECT id,name,city,interests FROM users WHERE id!=? AND verified=1',(uid,)),connections=rows('SELECT * FROM connections WHERE sender=? OR recipient=?',(uid,uid)),past=rows("SELECT DISTINCT e.id,e.title FROM events e JOIN bookings b ON b.event=e.id WHERE b.user=? AND datetime(e.date,'+' || e.duration_minutes || ' minutes')<datetime(?) AND b.status='confirmed'",(uid,now().isoformat())))
@app.get('/api/photo/<int:pid>')
@auth()
def photo(pid):
    uid=user()['id'];p=one("SELECT * FROM posts p WHERE p.id=? AND (p.audience='community' OR p.user=? OR EXISTS(SELECT 1 FROM connections c WHERE c.status='accepted' AND ((c.sender=? AND c.recipient=p.user) OR (c.recipient=? AND c.sender=p.user))))",(pid,uid,uid,uid))
    if not p or not p['photo']:return fail('Photo unavailable.')
    return send_file(io.BytesIO(p['photo']),mimetype='image/jpeg')
@app.post('/api/connect')
@auth()
def connect():
    d=request.json;uid=user()['id'];other=int(d['id'])
    if other==uid or not one('SELECT id FROM users WHERE id=? AND verified=1',(other,)):return fail('Invalid person.')
    if d.get('action') in ['accepted','declined']:db().execute("UPDATE connections SET status=? WHERE sender=? AND recipient=? AND status='pending'",(d['action'],other,uid))
    else:
        if one('SELECT * FROM connections WHERE (sender=? AND recipient=?) OR (sender=? AND recipient=?)',(uid,other,other,uid)):return fail('A connection request already exists.')
        db().execute('INSERT INTO connections(sender,recipient) VALUES(?,?)',(uid,other));notice(other,user()['name']+' sent you a connection request.')
    db().commit();return jsonify(message='Connection updated.')
setup()
from client_updates import install
install(app, db, auth, user, notice, fail, one, rows)
from inventory import install as install_inventory
install_inventory(app,db,one)
from recommendations import install as install_recommendations
install_recommendations(app,db,auth,user,one,rows,fail,limited)
from demo_payments import install as install_payments
install_payments(app,db,auth,user,one,rows,fail,notice,limited,lambda:DEMO and os.getenv('PAYMENT_PROVIDER','demo')=='demo')
from razorpay_gateway import install as install_razorpay
install_razorpay(app,db,auth,user,one,rows,fail,notice,limited)
from client_features import install as install_features
install_features(app,db,auth,user,one,rows,fail,notice)
from seating import install as install_seating
install_seating(app,db,auth,user,one,rows,fail)
with app.app_context():app.extensions['eventai_migrate_holds']()
from profile_media import install as install_profile_media
install_profile_media(app,db,auth,user,one,rows,fail,notice,limited)
from operations import install as install_operations
install_operations(app,db,auth,user,one,rows,fail,notice,limited)
from notifications import install as install_notifications
install_notifications(app,db,auth,user,one,rows,fail)
from evaluation import install as install_evaluation
install_evaluation(app,auth,rows,fail)
from assistant_features import install as install_assistant
install_assistant(app,db,auth,user,one,rows,fail,limited)
from ai_agent import install as install_ai_agent
install_ai_agent(app,db,auth,user,one,rows,fail,notice,limited)
from production import install as install_production
install_production(app,db)
if __name__=='__main__':app.run(host=os.getenv('HOST','127.0.0.1'),port=int(os.getenv('PORT','5000')),debug=False)
