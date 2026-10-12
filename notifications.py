"""Transactional email outbox, leased delivery, retry backoff and event reminders."""
import io,os,secrets,smtplib,ssl,threading,time
from email.message import EmailMessage
from datetime import datetime
import qrcode
from flask import jsonify,request
from client_updates import now


def send_smtp(message):
    if not os.getenv('SMTP_HOST') or not os.getenv('SMTP_USER') or not os.getenv('SMTP_PASSWORD'):
        raise RuntimeError('SMTP is not configured')
    with smtplib.SMTP(os.environ['SMTP_HOST'],int(os.getenv('SMTP_PORT','587')),timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context());smtp.login(os.environ['SMTP_USER'],os.environ['SMTP_PASSWORD']);smtp.send_message(message)


def install(app,db,auth,user,one,rows,fail):
    with app.app_context():
        db().executescript('''CREATE TABLE IF NOT EXISTS worker_status(name TEXT PRIMARY KEY,last_run TEXT);
          CREATE TABLE IF NOT EXISTS email_outbox(
          id INTEGER PRIMARY KEY,user INTEGER NOT NULL REFERENCES users(id),kind TEXT NOT NULL,reference INTEGER NOT NULL,
          provider TEXT DEFAULT '',dedupe TEXT NOT NULL UNIQUE,event_start TEXT,status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0,next_try REAL NOT NULL DEFAULT 0,lease TEXT,lease_until REAL,
          last_error TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP,sent_at TEXT);
          CREATE INDEX IF NOT EXISTS email_delivery_due ON email_outbox(status,next_try);
          CREATE TRIGGER IF NOT EXISTS booking_mail AFTER INSERT ON bookings WHEN NEW.status='confirmed' BEGIN
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,dedupe) VALUES(NEW.user,'booking',NEW.id,'booking:'||NEW.id);
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,dedupe) VALUES(NEW.user,'ticket',NEW.id,'ticket:'||NEW.id);
          END;
          CREATE TRIGGER IF NOT EXISTS cancellation_mail AFTER UPDATE OF status ON bookings
            WHEN OLD.status='confirmed' AND NEW.status='cancelled' BEGIN
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,dedupe) VALUES(NEW.user,'cancellation',NEW.id,'cancel:'||NEW.id);
          END;
          CREATE TRIGGER IF NOT EXISTS captured_mail AFTER UPDATE OF status ON razorpay_payments
            WHEN NEW.status IN ('paid_pending','refund_required') AND NEW.paid_at IS NOT NULL BEGIN
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,provider,dedupe) VALUES(NEW.user,'payment',NEW.id,'razorpay','payment:razorpay:'||NEW.id);
          END;
          CREATE TRIGGER IF NOT EXISTS demo_payment_mail AFTER INSERT ON demo_payments WHEN NEW.method!='free' BEGIN
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,provider,dedupe) VALUES(NEW.user,'payment',NEW.id,'demo','payment:demo:'||NEW.id);
          END;
          CREATE TRIGGER IF NOT EXISTS demo_cancel_mail AFTER UPDATE OF status ON demo_payments WHEN NEW.status IN ('cancelled','rejected','expired') AND NEW.booking IS NULL BEGIN
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,provider,dedupe) VALUES(NEW.user,'request_cancel',NEW.id,'demo','request-cancel:demo:'||NEW.id);
          END;
          CREATE TRIGGER IF NOT EXISTS razorpay_cancel_mail AFTER UPDATE OF status ON razorpay_payments WHEN NEW.status IN ('cancelled','refund_required') AND NEW.booking IS NULL BEGIN
            INSERT OR IGNORE INTO email_outbox(user,kind,reference,provider,dedupe) VALUES(NEW.user,'request_cancel',NEW.id,'razorpay','request-cancel:razorpay:'||NEW.id);
          END;
        ''');db().commit()

    def enqueue_reminders():
        stamp=now();db().execute('BEGIN IMMEDIATE')
        for b in rows("SELECT b.id,b.user,b.created,e.date FROM bookings b JOIN events e ON e.id=b.event WHERE b.status='confirmed' AND b.used=0 AND e.status='approved' AND datetime(e.date)>datetime(?) AND datetime(e.date)<=datetime(?,'+24 hours')",(stamp.isoformat(),stamp.isoformat())):
            seconds=(datetime.fromisoformat(b['date'])-stamp).total_seconds()
            for hours,enabled in [(24,True),(2,os.getenv('REMINDER_2H','1')=='1')]:
                # Late bookings receive the next applicable reminder, not a misleading 24-hour reminder.
                created=one("SELECT datetime(?,'+330 minutes')<=datetime(?,'-'||?||' hours') eligible",(b['created'],b['date'],hours))['eligible']
                if enabled and created and 0<seconds<=hours*3600 and (hours!=24 or seconds>2*3600):
                    key=f"reminder:{b['id']}:{hours}:{b['date']}"
                    db().execute('INSERT OR IGNORE INTO email_outbox(user,kind,reference,dedupe,event_start) VALUES(?,?,?,?,?)',(b['user'],f'reminder_{hours}',b['id'],key,b['date']))
        db().commit()

    def message(job):
        recipient=one('SELECT name,email,disabled FROM users WHERE id=?',(job['user'],))
        if not recipient or recipient['disabled']:return None
        msg=EmailMessage();msg['From']=os.getenv('SMTP_FROM') or os.getenv('SMTP_USER','');msg['To']=recipient['email'];msg['Message-ID']=f"<eventai-{job['id']}@{os.getenv('EMAIL_MESSAGE_DOMAIN','eventai.local')}>"
        if job['kind'] in ('payment','request_cancel'):
            table='razorpay_payments' if job['provider']=='razorpay' else 'demo_payments'
            p=one(f'SELECT p.*,e.title FROM {table} p JOIN events e ON e.id=p.event WHERE p.id=?',(job['reference'],))
            if not p:return None
            demo=job['provider']=='demo';amount=p['amount'] if demo else p['amount']/100
            msg['Subject']=('EventAI — reservation cancelled' if job['kind']=='request_cancel' else 'EventAI — '+('DEMO payment confirmation' if demo else 'Payment confirmation'))
            text=f"Hello {recipient['name']},\n\n{p['title']}\nAmount: INR {amount:.2f}\nStatus: {p['status']}\nReference: {p.get('reference') or p.get('payment_id') or p.get('receipt')}\n"
            text+='DEMO: no money was charged.\n' if demo else 'Captured payments requiring refund are reconciled by the administrator. A cancellation does not mean a refund has been sent.\n'
            text+='Tickets are issued only after organizer approval. View My orders for the current status.'
        else:
            b=one('SELECT b.*,e.title,e.date,e.venue,e.city,e.status event_status FROM bookings b JOIN events e ON e.id=b.event WHERE b.id=?',(job['reference'],))
            if not b:return None
            if job['kind']!='cancellation' and (b['status']!='confirmed' or b['event_status']=='cancelled'):return None
            if job['kind'].startswith('reminder') and (b['date']!=job['event_start'] or b['date']<=now().isoformat() or b['used']):return None
            titles={'booking':'Booking confirmation','ticket':'Your QR ticket','cancellation':'Booking cancellation','reminder_24':'Event reminder — within 24 hours','reminder_2':'Event reminder — within 2 hours'}
            msg['Subject']='EventAI — '+titles[job['kind']]
            seats=rows('SELECT s.row_label,s.number FROM seat_claims c JOIN event_seats s ON s.id=c.seat WHERE c.booking=?',(b['id'],))
            labels=', '.join(str(s['row_label'])+str(s['number']) for s in seats) or 'General admission'
            text=f"Hello {recipient['name']},\n\n{titles[job['kind']]}\nBooking #{b['id']} — {b['title']}\nStarts: {b['date']} IST\nVenue: {b['venue']}, {b['city']}\nGuests: {b['qty']}\nSeats: {labels}\nAmount: INR {b['total']}\n"
            if job['kind']=='cancellation':text+='Your ticket is invalid and seats are released. Any captured payment requires a separately confirmed refund. See My orders.'
            elif job['kind']=='ticket':
                text+='Present the attached QR at the selected event. One scan admits the entire booking party. Do not share your QR publicly.'
            else:text+='View your ticket and current event details in My orders.'
            msg.set_content(text)
            if job['kind']=='ticket':
                buf=io.BytesIO();qrcode.make(b['token']).save(buf,format='PNG');msg.add_attachment(buf.getvalue(),maintype='image',subtype='png',filename=f'EventAI-ticket-{b["id"]}.png')
            return msg
        msg.set_content(text);return msg

    def deliver(limit=25):
        sent=failed=skipped=0
        for _ in range(limit):
            timestamp=time.time();lease=secrets.token_hex(16);db().execute('BEGIN IMMEDIATE')
            job=one("SELECT * FROM email_outbox WHERE (status='pending' AND next_try<=?) OR (status='sending' AND lease_until<?) ORDER BY id LIMIT 1",(timestamp,timestamp))
            if not job:db().rollback();break
            db().execute("UPDATE email_outbox SET status='sending',lease=?,lease_until=?,attempts=attempts+1 WHERE id=?",(lease,timestamp+180,job['id']));db().commit()
            try:
                msg=message(job)
                if msg is None:status='skipped';skipped+=1
                else:send_smtp(msg);status='sent';sent+=1
                db().execute('UPDATE email_outbox SET status=?,sent_at=?,lease=NULL,lease_until=NULL,last_error=NULL WHERE id=? AND lease=?',(status,now().isoformat() if msg else None,job['id'],lease));db().commit()
            except Exception as error:
                failed+=1;attempts=job['attempts']+1
                db().execute('UPDATE email_outbox SET status=?,next_try=?,last_error=?,lease=NULL,lease_until=NULL WHERE id=? AND lease=?',('failed' if attempts>=5 else 'pending',time.time()+min(3600,60*2**attempts),type(error).__name__+': delivery failed; check SMTP configuration and connectivity.',job['id'],lease));db().commit()
        return dict(sent=sent,failed=failed,skipped=skipped)
    def tick():
        enqueue_reminders();result=deliver()
        db().execute("INSERT OR REPLACE INTO worker_status VALUES('notifications',?)",(now().isoformat(),));db().commit();return result
    app.extensions['eventai_notifications_tick']=tick
    app.extensions['eventai_enqueue_reminders']=enqueue_reminders
    app.extensions['eventai_deliver_emails']=deliver

    @app.route('/api/admin/email-queue',methods=['GET','POST'])
    @auth(['admin'])
    def email_queue():
        if request.method=='POST':
            db().execute("UPDATE email_outbox SET status='pending',attempts=0,next_try=0,last_error=NULL WHERE id=? AND status='failed'",(request.json.get('id'),));db().commit();return jsonify(message='Failed email scheduled for retry.')
        return jsonify(worker=one("SELECT last_run FROM worker_status WHERE name='notifications'"),configured=bool(os.getenv('SMTP_PASSWORD') and os.getenv('SMTP_HOST') and os.getenv('SMTP_USER')),items=rows('SELECT id,kind,status,attempts,last_error,created,sent_at FROM email_outbox ORDER BY id DESC LIMIT 200'))

    def loop():
        while True:
            try:
                with app.app_context():tick()
            except Exception:app.logger.exception('Notification worker iteration failed')
            threading.Event().wait(30)
    if os.getenv('EVENTAI_WORKER','1')=='1' and os.getenv('EMAIL_WORKER','1')=='1':threading.Thread(target=loop,daemon=True,name='eventai-email').start()
