"""Account editing and bounded, normalized event/community video uploads."""
import io,json,math,os,re,secrets,shutil,smtplib,ssl,subprocess,tempfile,time
from pathlib import Path
from email.message import EmailMessage
from flask import jsonify,request,session,send_file
from werkzeug.security import check_password_hash,generate_password_hash
from client_updates import now


def normalize_clip(raw):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ValueError('Video upload requires FFmpeg and ffprobe on the server. Contact the administrator.')
    if not raw or len(raw)>20*1024*1024:raise ValueError('Choose a video under 20 MB.')
    with tempfile.TemporaryDirectory(prefix='eventai-clip-') as folder:
        source=Path(folder)/'input';target=Path(folder)/'clip.mp4';source.write_bytes(raw)
        def probe(path):
            result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-show_entries','format=duration,format_name:stream=codec_type,width,height','-of','json',str(path)],capture_output=True,timeout=15)
            if result.returncode:raise ValueError('The file is not a readable MP4 or WebM video.')
            return json.loads(result.stdout)
        try:
            meta=probe(source);duration=float(meta['format']['duration'])
            if not math.isfinite(duration) or not 0<duration<=60:raise ValueError('Event clips must be 60 seconds or shorter.')
            if not set(meta['format']['format_name'].split(',')) & {'mov','mp4','matroska','webm'}:raise ValueError('Choose an MP4 or WebM video.')
            videos=[s for s in meta['streams'] if s.get('codec_type')=='video']
            if not videos or any(s.get('width',0)*s.get('height',0)>3840*2160 for s in videos):raise ValueError('Use a video no larger than 4K.')
            result=subprocess.run(['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-threads','1','-i',str(source),'-map','0:v:0','-map','0:a:0?','-t','61','-vf',"scale=w='min(720,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",'-r','30','-c:v','libx264','-preset','veryfast','-crf','26','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-map_metadata','-1','-movflags','+faststart',str(target)],capture_output=True,timeout=90)
            if result.returncode:raise ValueError('Video could not be processed. Try another MP4 or WebM clip.')
            actual=float(probe(target)['format']['duration'])
            if actual>60.2 or target.stat().st_size>20*1024*1024:raise ValueError('Processed clip exceeds the 60-second or 20 MB limit.')
            return target.read_bytes(),round(actual,2)
        except (subprocess.TimeoutExpired,KeyError,json.JSONDecodeError,OSError):
            raise ValueError('Video could not be processed within the upload limits.') from None


def install(app,db,auth,user,one,rows,fail,notice,limited):
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS email_changes(user INTEGER PRIMARY KEY REFERENCES users(id),email TEXT NOT NULL,
          hash TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS clips(kind TEXT NOT NULL,target INTEGER NOT NULL,owner INTEGER NOT NULL REFERENCES users(id),
          video BLOB NOT NULL,duration REAL NOT NULL,created TEXT NOT NULL,PRIMARY KEY(kind,target));
        ''');db().commit()

    @app.route('/api/profile',methods=['GET','POST'])
    @auth()
    def profile():
        if request.method=='GET':return jsonify(user=user())
        d=request.json;u=user();name=d.get('name',u['name']).strip()
        if not name:return fail('Enter your name.')
        password=d.get('new_password','')
        if password:
            if not isinstance(password,str) or not 10<=len(password)<=256 or password!=d.get('confirm_new_password'):return fail('New passwords must match and contain at least 10 characters.')
            if limited('profile-password:'+str(u['id']),6):return fail('Too many attempts. Try again later.')
        db().execute('BEGIN IMMEDIATE');account=one('SELECT * FROM users WHERE id=?',(u['id'],))
        if account['version']!=u['version']:db().rollback();return fail('Sign in again.')
        if password and not check_password_hash(account['password'],d.get('current_password','')):db().rollback();return fail('Current password is incorrect.')
        db().execute('UPDATE users SET name=?,city=?,interests=? WHERE id=?',(name,d.get('city',u['city']).strip(),d.get('interests',u['interests']).strip(),u['id']))
        if password:
            db().execute('UPDATE users SET password=?,version=version+1 WHERE id=?',(generate_password_hash(password),u['id']))
            session['version']=u['version']+1;notice(u['id'],'Your account password was changed. Other sessions were signed out.')
        db().commit();return jsonify(message='Profile updated.',user=user())

    @app.post('/api/profile/email/send')
    @auth()
    def email_send():
        d=request.json;u=user();email=d.get('email','').strip().lower()
        if limited('email-change:'+str(u['id']),5,900):return fail('Too many email change attempts. Try again later.')
        account=one('SELECT * FROM users WHERE id=?',(u['id'],))
        if not check_password_hash(account['password'],d.get('current_password','')):return fail('Current password is incorrect.')
        if email==u['email'] or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or one('SELECT id FROM users WHERE email=?',(email,)):return fail('Choose an unused valid email address.')
        code=str(secrets.randbelow(900000)+100000);response={'message':'Verification code sent to your new email. Your current email stays active until verified.'}
        if os.getenv('SMTP_PASSWORD'):
            msg=EmailMessage();msg['Subject']='EventAI: verify your new email';msg['From']=os.getenv('SMTP_USER');msg['To']=email;msg.set_content('Your EventAI email change code is '+code+'. It expires in 10 minutes.')
            try:
                with smtplib.SMTP(os.getenv('SMTP_HOST','smtp.gmail.com'),int(os.getenv('SMTP_PORT','587')),timeout=15) as smtp:
                    smtp.starttls(context=ssl.create_default_context());smtp.login(msg['From'],os.environ['SMTP_PASSWORD']);smtp.send_message(msg)
            except Exception:return fail('Email could not be delivered. Your account email has not changed.')
        elif os.getenv('DEMO_MODE','1')=='1':response.update(demo_code=code,message='Development code only; no email sent. Your current email has not changed.')
        else:return fail('Email delivery is not configured. Your account email has not changed.')
        db().execute('INSERT OR REPLACE INTO email_changes VALUES(?,?,?,?,0,?)',(u['id'],email,generate_password_hash(code),time.time()+600,u['version']));db().commit()
        return jsonify(response)

    @app.post('/api/profile/email/confirm')
    @auth()
    def email_confirm():
        u=user();d=request.json;db().execute('BEGIN IMMEDIATE');pending=one('SELECT * FROM email_changes WHERE user=?',(u['id'],))
        if not pending or pending['version']!=u['version'] or pending['expires']<time.time() or pending['attempts']>=5:db().rollback();return fail('Email change code expired. Request a new one.')
        db().execute('UPDATE email_changes SET attempts=attempts+1 WHERE user=?',(u['id'],))
        account=one('SELECT * FROM users WHERE id=?',(u['id'],))
        if account['version']!=u['version']:db().rollback();return fail('Sign in again.')
        if not check_password_hash(pending['hash'],d.get('code','')) or not check_password_hash(account['password'],d.get('current_password','')):
            db().commit();return fail('Incorrect verification code or current password.')
        if one('SELECT id FROM users WHERE email=?',(pending['email'],)):db().rollback();return fail('That email is already in use.')
        db().execute('UPDATE users SET email=?,version=version+1 WHERE id=?',(pending['email'],u['id']))
        db().execute('DELETE FROM email_changes WHERE user=?',(u['id'],));notice(u['id'],'Your account email was changed and verified. Other sessions were signed out.')
        db().commit();session['version']=u['version']+1
        return jsonify(message='Email updated and verified.',user=user())

    def allowed(kind,target,write=False):
        u=user()
        if kind=='event':
            e=one('SELECT * FROM events WHERE id=?',(target,))
            owner=bool(e and u and u['verified'] and (u['role']=='admin' or u['role']=='organizer' and e['owner']==u['id']))
            return bool(e and (owner and e['date']>now().isoformat() if write else e['status']=='approved' or owner))
        if kind=='memory':
            if not u or not u['verified']:return False
            p=one('SELECT * FROM posts WHERE id=?',(target,))
            if not p:return False
            if write:return p['user']==u['id']
            return p['user']==u['id'] or p['audience']=='community' or bool(one("SELECT sender FROM connections WHERE status='accepted' AND ((sender=? AND recipient=?) OR (sender=? AND recipient=?))",(u['id'],p['user'],p['user'],u['id'])))
        return False

    @app.get('/api/media-capabilities')
    def media_capabilities():return jsonify(clips=bool(shutil.which('ffmpeg') and shutil.which('ffprobe')),max_seconds=60,max_megabytes=20)

    @app.get('/api/clips/<kind>/<int:target>/info')
    def clip_info(kind,target):
        if not allowed(kind,target):return jsonify(error='Clip unavailable.'),404
        clip=one('SELECT duration,created FROM clips WHERE kind=? AND target=?',(kind,target))
        return jsonify(clip=clip)

    @app.get('/api/clips/<kind>/<int:target>')
    def clip_get(kind,target):
        if not allowed(kind,target):return jsonify(error='Clip unavailable.'),404
        clip=one('SELECT video FROM clips WHERE kind=? AND target=?',(kind,target))
        if not clip:return jsonify(error='Clip unavailable.'),404
        response=send_file(io.BytesIO(clip['video']),mimetype='video/mp4',conditional=True,download_name='eventai-clip.mp4')
        response.headers['Cache-Control']='private, no-store';return response

    @app.post('/api/clips/<kind>/<int:target>')
    @auth()
    def clip_upload(kind,target):
        if not allowed(kind,target,True):return jsonify(error='You cannot change this clip.'),403
        if limited('clip:'+str(user()['id']),10,600):return fail('Too many uploads. Please wait before uploading again.')
        if request.is_json and request.json.get('remove'):
            video=None;duration=0
        else:
            file=request.files.get('clip')
            if not file:return fail('Choose an MP4 or WebM video.')
            try:video,duration=normalize_clip(file.read(20*1024*1024+1))
            except ValueError as error:return fail(str(error))
        db().execute('BEGIN IMMEDIATE')
        if not allowed(kind,target,True):db().rollback();return jsonify(error='This item can no longer be edited.'),403
        if video:db().execute('INSERT OR REPLACE INTO clips VALUES(?,?,?,?,?,?)',(kind,target,user()['id'],video,duration,now().isoformat()))
        else:db().execute('DELETE FROM clips WHERE kind=? AND target=?',(kind,target))
        if kind=='event' and user()['role']!='admin':db().execute("UPDATE events SET status='pending' WHERE id=?",(target,))
        db().commit();return jsonify(message='Clip saved.'+(' Event needs admin review.' if kind=='event' and user()['role']!='admin' else ''),duration=duration)
