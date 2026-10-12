"""Razorpay Orders + Standard Checkout, captured-payment verification and review.

INR totals in the existing app are whole rupees; provider amounts are paise.
No credentials or card/UPI data are stored in the payment table.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from datetime import datetime,timedelta
from flask import request, jsonify
from client_updates import now


class GatewayError(Exception):
    pass


def provider():
    return os.getenv('PAYMENT_PROVIDER','demo' if os.getenv('DEMO_MODE','1')=='1' else 'disabled')


def configuration():
    key=os.getenv('RAZORPAY_KEY_ID','').strip()
    ready=provider()=='razorpay' and bool(key and os.getenv('RAZORPAY_KEY_SECRET') and os.getenv('RAZORPAY_WEBHOOK_SECRET'))
    mode='live' if key.startswith('rzp_live_') else 'test'
    ready=ready and key.startswith(('rzp_test_','rzp_live_'))
    if mode=='live' and (os.getenv('APP_ENV')!='production' or os.getenv('DEMO_MODE')!='0'):
        ready=False
    return {'provider':provider(),'enabled':bool(ready),'mode':mode,'organizer_approval':True}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise GatewayError('Unexpected payment provider redirect.')


def gateway_api(method,path,data=None):
    """Fixed HTTPS origin; no automatic POST retry after an ambiguous timeout."""
    if not configuration()['enabled']:raise GatewayError('Razorpay is not configured on this server.')
    credentials=(os.environ['RAZORPAY_KEY_ID']+':'+os.environ['RAZORPAY_KEY_SECRET']).encode()
    req=urllib.request.Request('https://api.razorpay.com/v1/'+path,
        data=json.dumps(data).encode() if data is not None else None,method=method,
        headers={'Authorization':'Basic '+base64.b64encode(credentials).decode(),'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as response:
            result=json.loads(response.read(1024*1024))
            if not isinstance(result,dict):raise GatewayError('Invalid payment provider response.')
            return result
    except (urllib.error.URLError,TimeoutError,OSError,ValueError) as error:
        raise GatewayError('Could not confirm Razorpay status. Please refresh payment status; do not pay again if debited.') from None


def signature_ok(body,signature,secret):
    return isinstance(signature,str) and bool(secret) and bool(re.fullmatch(r'[a-fA-F0-9]{64}',signature)) and hmac.compare_digest(
        hmac.new(secret.encode(),body,hashlib.sha256).hexdigest(),signature.lower())


def install(app,db,auth,user,one,rows,fail,notice,limited):
    with app.app_context():
        db().executescript('''
        CREATE TABLE IF NOT EXISTS razorpay_payments(
          id INTEGER PRIMARY KEY,user INTEGER NOT NULL REFERENCES users(id),event INTEGER NOT NULL REFERENCES events(id),
          request_id TEXT NOT NULL,payload TEXT NOT NULL,receipt TEXT NOT NULL UNIQUE,
          order_id TEXT UNIQUE,payment_id TEXT UNIQUE,key_id TEXT NOT NULL,mode TEXT NOT NULL,
          qty INTEGER NOT NULL,unit_price INTEGER NOT NULL,redeemed INTEGER NOT NULL,amount INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'INR',status TEXT NOT NULL DEFAULT 'creating',
          method TEXT DEFAULT '',created TEXT NOT NULL,paid_at TEXT,reviewed_by INTEGER REFERENCES users(id),
          reviewed_at TEXT,reason TEXT DEFAULT '',booking INTEGER REFERENCES bookings(id),
          UNIQUE(user,request_id));
        CREATE INDEX IF NOT EXISTS razorpay_review_queue ON razorpay_payments(status,id);
        CREATE TABLE IF NOT EXISTS razorpay_webhooks(digest TEXT PRIMARY KEY,created TEXT NOT NULL);
        ''')
        db().commit()

    def messages(p):
        return {'paid_pending':'Payment verified. Seats held pending organizer approval; no ticket issued yet.',
          'approved':'Booking confirmed. Your QR ticket is ready.',
          'authorized':'Payment authorized. Waiting for Razorpay to capture it; do not pay again.',
          'refund_required':'Payment received but booking cannot proceed. Admin must process a refund.',
          'refunded':'Payment refunded. No active ticket.',
          'refund_review':'A partial refund needs administrator reconciliation.',
          'creating':'Order creation is unresolved. Contact the administrator before retrying.',
          'created':'Checkout ready. No confirmed payment yet.',
          'failed':'Payment attempt failed. Check your bank status before trying again.'}.get(p['status'],p['status'])

    def reply(p):
        return jsonify(id=p['id'],status=p['status'],message=messages(p),booking_id=p['booking'])

    def lookup(pid):
        p=one('SELECT * FROM razorpay_payments WHERE id=?',(pid,));u=user()
        return p if p and (u['id']==p['user'] or u['role']=='admin' or
            u['role']=='organizer' and one('SELECT id FROM events WHERE id=? AND owner=?',(p['event'],u['id']))) else None

    def check_account(p):
        if p['key_id']!=os.getenv('RAZORPAY_KEY_ID'):
            raise GatewayError('This transaction belongs to another Razorpay key. Restore its key to reconcile it.')

    def admin_notice(text):
        for admin in rows("SELECT id FROM users WHERE role='admin'"):notice(admin['id'],text)

    def refund_booking(p):
        if p['booking']:
            b=one('SELECT * FROM bookings WHERE id=?',(p['booking'],))
            if b and b['status']=='confirmed':
                db().execute("UPDATE bookings SET status='cancelled' WHERE id=?",(b['id'],))
                adjustment=b['redeemed']-b['earned']
                db().execute('UPDATE users SET points=points+? WHERE id=?',(adjustment,p['user']))
                if adjustment:db().execute('INSERT INTO ledger(user,amount,label) VALUES(?,?,?)',
                    (p['user'],adjustment,'Refund adjustment for booking #'+str(b['id'])))
                db().execute("DELETE FROM interactions WHERE user=? AND dedupe=?",(p['user'],'booking:'+str(b['id'])))

    def sync_entity(pid,entity):
        """Entity must be fetched from Razorpay API, never accepted from client JSON."""
        db().execute('BEGIN IMMEDIATE')
        p=one('SELECT * FROM razorpay_payments WHERE id=?',(pid,))
        if not p:db().rollback();raise GatewayError('Payment record not found.')
        if (entity.get('order_id')!=p['order_id'] or entity.get('amount')!=p['amount']
            or entity.get('currency')!='INR' or not re.fullmatch(r'pay_[A-Za-z0-9]+',str(entity.get('id','')))):
            db().rollback();raise GatewayError('Payment order, amount or currency does not match.')
        captured=entity.get('status')=='captured' and entity.get('captured') is True
        payment_id=entity['id'];refund=int(entity.get('amount_refunded') or 0)
        if p['payment_id'] and p['payment_id']!=payment_id:
            if p['status'] not in ('created','creating','authorized','failed'):
                db().rollback()
                if entity.get('status')=='failed':return p
                raise GatewayError('A different successful payment is already linked to this order.')
        status=p['status'];reason=p['reason'];old=status
        if entity.get('status')=='refunded' and refund>=p['amount']:
            status='refunded';reason='Full refund confirmed by Razorpay.';refund_booking(p)
        elif refund>0:
            status='refund_review';reason='Partial refund detected. Booking is disabled pending reconciliation.';refund_booking(p)
        elif captured and old in ('created','creating','authorized','failed','cancelled'):
            status='paid_pending'
            e=one('SELECT * FROM events WHERE id=?',(p['event'],))
            if old=='cancelled':
                status='refund_required';reason='Payment captured after checkout cancellation.'
            elif e['status']!='approved' or e['date']<=now().isoformat():
                status='refund_required';reason='Payment captured after event became unavailable.'
            elif not app.extensions['eventai_hold_seats']('razorpay',pid,p['event'],p['qty'],e['date']):
                status='refund_required';reason='The unpaid seat hold expired and seats are no longer available.'
        elif entity.get('status')=='authorized' and old in ('created','creating','failed'):
            status='authorized'
        elif entity.get('status')=='failed' and old in ('created','creating','authorized'):
            status='failed'
        # Failed attempts do not pin payment_id: the same order may be retried.
        linked=payment_id if captured or refund>0 or entity.get('status')=='authorized' else None if status=='failed' else p['payment_id']
        db().execute('UPDATE razorpay_payments SET status=?,reason=?,payment_id=?,method=?,paid_at=COALESCE(paid_at,?) WHERE id=?',
            (status,reason,linked,str(entity.get('method',''))[:40],now().isoformat() if captured else None,pid))
        if status in ('failed','refund_required','refunded','refund_review'):
            db().execute("DELETE FROM seat_holds WHERE provider='razorpay' AND payment=?",(pid,))
        if status!=old and status in ('paid_pending','refund_required','refunded','refund_review'):
            notice(p['user'],'Razorpay '+payment_id+': '+messages(dict(p,status=status)))
            owner=one('SELECT owner FROM events WHERE id=?',(p['event'],))['owner']
            notice(owner,f'Razorpay {payment_id}: ₹{p["amount"]/100:.2f}; {status}. Review ticket request #{pid}.')
            if status in ('refund_required','refund_review'):admin_notice(f'Razorpay {payment_id}: {status}. Reconcile payment #{pid}.')
        db().commit();return one('SELECT * FROM razorpay_payments WHERE id=?',(pid,))

    def refresh(p):
        check_account(p)
        if not p['order_id']:raise GatewayError('Order creation needs reconciliation. Check its receipt in Razorpay dashboard.')
        if p['payment_id']:
            return sync_entity(p['id'],gateway_api('GET','payments/'+p['payment_id']))
        data=gateway_api('GET','orders/'+p['order_id']+'/payments')
        items=data.get('items',[])
        priority={'refunded':4,'captured':3,'authorized':2,'failed':1}
        items=sorted((x for x in items if isinstance(x,dict)),key=lambda x:priority.get(x.get('status'),0),reverse=True)
        return sync_entity(p['id'],items[0]) if items else p

    @app.errorhandler(GatewayError)
    def gateway_error(error):return jsonify(error=str(error)),502

    @app.get('/api/payment-config')
    def payment_configuration():return jsonify(configuration())

    @app.post('/api/razorpay/order')
    @auth()
    def razorpay_order():
        config=configuration()
        if not config['enabled']:return fail('Razorpay keys and webhook secret must be configured on the server.')
        d=request.json;uid=user()['id'];key=d.get('request_id','')
        if not isinstance(key,str) or not 8<=len(key)<=80:return fail('A unique request ID is required.')
        qty=int(d.get('qty',1));points=int(d.get('points',0));eid=int(d.get('event',0))
        if not 1<=qty<=10 or points<0:return fail('Choose 1–10 tickets and valid points.')
        if limited('razorpay-order:'+str(uid),10,60):return jsonify(error='Please wait a minute before retrying.'),429
        selected=d.get('seats',[])
        if not isinstance(selected,list) or any(type(x)!=int for x in selected) or len(selected)>10:return fail('Invalid seat selection.')
        payload=hashlib.sha256(json.dumps([eid,qty,points,sorted(selected)]).encode()).hexdigest()
        db().execute('BEGIN IMMEDIATE')
        previous=one('SELECT * FROM razorpay_payments WHERE user=? AND request_id=?',(uid,key))
        if previous:
            db().rollback()
            if previous['payload']!=payload:return fail('Request ID was already used with different booking details.')
            check_account(previous)
            if previous['status'] not in ('created','failed') or not previous['order_id']:return reply(previous)
            db().execute('BEGIN IMMEDIATE')
            e=one("SELECT * FROM events WHERE id=? AND status='approved'",(eid,))
            if not e or e['date']<=now().isoformat() or not app.extensions['eventai_hold_seats']('razorpay',previous['id'],eid,qty,min(now()+timedelta(minutes=15),datetime.fromisoformat(e['date'])).isoformat()):
                db().rollback();return fail('Seats are no longer available for this checkout.')
            db().commit()
            return order_reply(previous)
        e=one("SELECT * FROM events WHERE id=? AND status='approved'",(eid,))
        if not e or e['date']<=now().isoformat():db().rollback();return fail('Event is no longer bookable.')
        available=one('SELECT available FROM event_inventory WHERE id=?',(eid,))['available']
        gross,selected=app.extensions['eventai_seat_quote'](e,d,qty)
        if gross<=0 or qty>available or points>min(max(0,user()['points']),gross//5):
            db().rollback();return fail('Check ticket availability and reward balance. Free events do not need payment.')
        receipt='evt_'+secrets.token_hex(15)
        pid=db().execute('''INSERT INTO razorpay_payments(user,event,request_id,payload,receipt,key_id,mode,qty,
           unit_price,redeemed,amount,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
           (uid,eid,key,payload,receipt,os.environ['RAZORPAY_KEY_ID'],config['mode'],qty,e['price'],points,(gross-points)*100,now().isoformat())).lastrowid
        db().execute('UPDATE razorpay_payments SET seat_json=? WHERE id=?',(json.dumps(selected),pid))
        if not app.extensions['eventai_hold_seats']('razorpay',pid,eid,qty,min(now()+timedelta(minutes=15),datetime.fromisoformat(e['date'])).isoformat()):
            db().rollback();return fail('Not enough seats remain.')
        db().commit()
        # Persist before external I/O. Ambiguous failures remain 'creating' and are never auto-retried.
        result=gateway_api('POST','orders',{'amount':(gross-points)*100,'currency':'INR','receipt':receipt,
            'partial_payment':False,'notes':{'eventai_payment':str(pid)}})
        if not re.fullmatch(r'order_[A-Za-z0-9]+',str(result.get('id',''))) or result.get('amount')!=(gross-points)*100 or result.get('currency')!='INR':
            raise GatewayError('Razorpay order response needs administrator reconciliation.')
        db().execute("UPDATE razorpay_payments SET order_id=?,status='created' WHERE id=? AND status='creating'",(result['id'],pid));db().commit()
        return order_reply(one('SELECT * FROM razorpay_payments WHERE id=?',(pid,)))

    def order_reply(p):
        return jsonify(id=p['id'],order_id=p['order_id'],key_id=p['key_id'],amount=p['amount'],currency='INR',mode=p['mode'],status=p['status'])

    @app.post('/api/razorpay/verify')
    @auth()
    def razorpay_verify():
        if limited('razorpay-verify:'+str(user()['id']),30,60):return jsonify(error='Please wait before verifying again.'),429
        d=request.json;p=lookup(d.get('id'))
        if not p:return fail('Payment not found.')
        check_account(p)
        payid=d.get('razorpay_payment_id','')
        if not isinstance(payid,str) or not re.fullmatch(r'pay_[A-Za-z0-9]+',payid):return fail('Invalid payment ID.')
        if d.get('razorpay_order_id')!=p['order_id'] or not signature_ok(
            ((p['order_id'] or '')+'|'+payid).encode(),d.get('razorpay_signature',''),os.getenv('RAZORPAY_KEY_SECRET','')):
            return jsonify(error='Payment signature verification failed.'),400
        return reply(sync_entity(p['id'],gateway_api('GET','payments/'+payid)))

    @app.post('/api/razorpay/refresh')
    @auth()
    def razorpay_refresh():
        if limited('razorpay-refresh:'+str(user()['id']),20,60):return jsonify(error='Please wait before refreshing again.'),429
        p=lookup(request.json.get('id'))
        if not p:return fail('Payment not found.')
        return reply(refresh(p))

    @app.get('/api/razorpay/payments')
    @auth()
    def razorpay_list():
        return jsonify(rows('''SELECT p.*,e.title FROM razorpay_payments p JOIN events e ON e.id=p.event
           WHERE p.user=? ORDER BY p.id DESC LIMIT 200''',(user()['id'],)))

    @app.get('/api/admin/razorpay/payments')
    @auth(['admin'])
    def razorpay_admin_list():
        return jsonify(rows('''SELECT p.*,e.title,u.name,u.email FROM razorpay_payments p
          JOIN events e ON e.id=p.event JOIN users u ON u.id=p.user
          ORDER BY (p.status IN ('paid_pending','refund_required','creating','refund_review')) DESC,p.id DESC LIMIT 500'''))

    @app.get('/api/organizer/razorpay/payments')
    @auth(['organizer'])
    def razorpay_organizer_list():
        return jsonify(rows('''SELECT p.*,e.title,u.name,u.email FROM razorpay_payments p
          JOIN events e ON e.id=p.event JOIN users u ON u.id=p.user WHERE e.owner=?
          ORDER BY (p.status='paid_pending') DESC,p.id DESC LIMIT 500''',(user()['id'],)))

    @app.post('/api/admin/razorpay/recover-order')
    @auth(['admin'])
    def recover_order():
        p=lookup(request.json.get('id'));oid=request.json.get('order_id','')
        if not p or p['status']!='creating' or not isinstance(oid,str) or not re.fullmatch(r'order_[A-Za-z0-9]+',oid):return fail('Select an unresolved order and a valid Razorpay order ID.')
        check_account(p);remote=gateway_api('GET','orders/'+oid)
        if remote.get('receipt')!=p['receipt'] or remote.get('amount')!=p['amount'] or remote.get('currency')!='INR':return fail('Order does not match the stored receipt and amount.')
        db().execute("UPDATE razorpay_payments SET order_id=?,status='created' WHERE id=? AND status='creating'",(oid,p['id']));db().commit()
        return reply(refresh(one('SELECT * FROM razorpay_payments WHERE id=?',(p['id'],))))

    @app.post('/api/organizer/razorpay/review')
    @auth(['organizer'])
    def razorpay_review():
        d=request.json;decision=d.get('decision');reason=str(d.get('reason','')).strip()[:500]
        if decision not in ('approved','rejected') or (decision=='rejected' and not reason):return fail('Choose approve or reject and provide a rejection reason.')
        p=lookup(d.get('id'))
        if not p:return fail('Payment not found.')
        if not one('SELECT id FROM events WHERE id=? AND owner=?',(p['event'],user()['id'])):
            return jsonify(error='Only the event organizer can approve these tickets.'),403
        p=refresh(p) # Provider must still confirm capture, full amount and no refunds.
        db().execute('BEGIN IMMEDIATE');p=one('SELECT * FROM razorpay_payments WHERE id=?',(p['id'],))
        if p['status']=='approved' and decision=='approved':db().rollback();return reply(p)
        if p['status']!='paid_pending':db().rollback();return fail('Only fully captured, unrefunded payments awaiting approval can be reviewed.')
        e=one('SELECT * FROM events WHERE id=?',(p['event'],));u=one('SELECT * FROM users WHERE id=?',(p['user'],))
        sold=one("SELECT COALESCE(SUM(qty),0) n FROM bookings WHERE event=? AND status='confirmed'",(p['event'],))['n']
        if decision=='approved':
            if e['status']!='approved' or e['date']<=now().isoformat():reason='Event is unavailable or already started.'
            elif e['price']!=p['unit_price']:reason='Event price changed.'
            elif not app.extensions['eventai_hold_seats']('razorpay',p['id'],p['event'],p['qty'],e['date']):reason='Not enough seats remain.'
            elif u['points']<p['redeemed'] or not u['verified'] or u['disabled']:reason='Reward balance or account eligibility changed.'
            else:reason=''
        status='refund_required' if reason else 'approved';bid=None
        if status=='approved':
            total=p['amount']//100;earned=total//10
            bid=db().execute('INSERT INTO bookings(user,event,qty,total,redeemed,earned,token) VALUES(?,?,?,?,?,?,?)',
                (p['user'],p['event'],p['qty'],total,p['redeemed'],earned,secrets.token_urlsafe(24))).lastrowid
            db().execute('UPDATE users SET points=points+? WHERE id=?',(earned-p['redeemed'],p['user']))
            for value,label in [(-p['redeemed'],'Redeemed: '),(earned,'Purchase: ')]:
                if value:db().execute('INSERT INTO ledger(user,amount,label) VALUES(?,?,?)',(p['user'],value,label+e['title']))
            app.extensions['eventai_record_booking'](p['user'],p['event'],bid)
        db().execute('UPDATE razorpay_payments SET status=?,reason=?,booking=?,reviewed_by=?,reviewed_at=? WHERE id=?',
            (status,reason,bid,user()['id'],now().isoformat(),p['id']))
        db().execute("DELETE FROM seat_holds WHERE provider='razorpay' AND payment=?",(p['id'],))
        notice(p['user'],e['title']+': '+messages(dict(p,status=status)))
        if status=='refund_required':admin_notice('Refund required for '+p['payment_id']+'. '+reason+' Process in Razorpay dashboard, then refresh status.')
        db().commit();return reply(one('SELECT * FROM razorpay_payments WHERE id=?',(p['id'],)))

    @app.post('/api/razorpay/webhook')
    def razorpay_webhook():
        raw=request.get_data(cache=True)
        if len(raw)>262144:return jsonify(error='Payload too large.'),413
        if not signature_ok(raw,request.headers.get('X-Razorpay-Signature',''),os.getenv('RAZORPAY_WEBHOOK_SECRET','')):
            return jsonify(error='Invalid webhook signature.'),400
        digest=hashlib.sha256(raw).hexdigest()
        if one('SELECT digest FROM razorpay_webhooks WHERE digest=?',(digest,)):return jsonify(ok=True,duplicate=True)
        d=request.get_json(silent=True)
        if not isinstance(d,dict):return fail('Invalid webhook payload.')
        event=d.get('event')
        if event not in ('payment.captured','payment.authorized','payment.failed','order.paid','refund.processed'):return jsonify(ok=True,ignored=True)
        payload=d.get('payload') or {}
        if not isinstance(payload,dict):return fail('Invalid webhook payload.')
        payment_part=payload.get('payment') or {};refund_part=payload.get('refund') or {}
        if not isinstance(payment_part,dict) or not isinstance(refund_part,dict):return fail('Invalid webhook entity.')
        entity=payment_part.get('entity') or {}
        if not isinstance(entity,dict):return fail('Invalid payment entity.')
        payid=entity.get('id')
        if event=='refund.processed':
            refund_entity=refund_part.get('entity') or {}
            if not isinstance(refund_entity,dict):return fail('Invalid refund entity.')
            payid=refund_entity.get('payment_id')
        if not isinstance(payid,str) or not re.fullmatch(r'pay_[A-Za-z0-9]+',payid):return fail('Missing payment entity.')
        remote=gateway_api('GET','payments/'+payid)
        p=one('SELECT * FROM razorpay_payments WHERE order_id=?',(remote.get('order_id'),))
        if p:check_account(p);sync_entity(p['id'],remote)
        # Only successful processing is deduplicated; provider/API failures return 5xx for retry.
        db().execute('INSERT OR IGNORE INTO razorpay_webhooks VALUES(?,?)',(digest,now().isoformat()))
        db().execute("DELETE FROM razorpay_webhooks WHERE datetime(created)<datetime(?,'-30 days')",(now().isoformat(),))
        db().commit();return jsonify(ok=True,ignored=not bool(p))
