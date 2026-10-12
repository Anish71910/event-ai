"""Fail-closed release configuration and API guardrails for EventAI."""
import os
import math
import re
import sqlite3
import uuid
from datetime import timedelta
from pathlib import Path
from flask import request, jsonify, g
from werkzeug.exceptions import HTTPException

def validate_environment(environ=None):
    e=os.environ if environ is None else environ
    if e.get('APP_ENV','development')!='production':return []
    problems=[]
    provider=e.get('PAYMENT_PROVIDER','disabled')
    if provider not in ('disabled','razorpay'):problems.append('Production PAYMENT_PROVIDER must be razorpay or disabled.')
    if provider=='razorpay':
        if not e.get('RAZORPAY_KEY_ID','').startswith('rzp_live_'):problems.append('Production Razorpay requires a live key ID.')
        if not e.get('RAZORPAY_KEY_SECRET'):problems.append('Set RAZORPAY_KEY_SECRET on the server.')
        if len(e.get('RAZORPAY_WEBHOOK_SECRET',''))<16:problems.append('Set a separate RAZORPAY_WEBHOOK_SECRET of at least 16 characters.')
    if e.get('DEMO_MODE')!='0':problems.append('DEMO_MODE must be 0.')
    key=e.get('SECRET_KEY','')
    if len(key)<32 or len(set(key))<12:problems.append('Set a unique random SECRET_KEY with at least 32 characters.')
    if e.get('HTTPS_ONLY')!='1':problems.append('HTTPS_ONLY must be 1 behind an HTTPS reverse proxy.')
    if not e.get('TRUSTED_HOSTS','').strip() or '*' in e.get('TRUSTED_HOSTS',''):problems.append('Set explicit TRUSTED_HOSTS without wildcards.')
    if not e.get('SMTP_USER') or not e.get('SMTP_PASSWORD'):problems.append('Configure SMTP credentials for email verification.')
    if not e.get('DATABASE') or not (Path(e['DATABASE']).is_absolute() or (isinstance(e['DATABASE'], str) and (e['DATABASE'].startswith('/') or e['DATABASE'].startswith('\\')))):problems.append('DATABASE must be an absolute path on persistent local storage.')
    return problems

def install(app,db):
    production=os.getenv('APP_ENV')=='production'
    app.config.update(PERMANENT_SESSION_LIFETIME=timedelta(hours=8),SESSION_REFRESH_EACH_REQUEST=False)
    if production:
        app.config['TRUSTED_HOSTS']=[s.strip() for s in os.environ['TRUSTED_HOSTS'].split(',') if s.strip()]
        with app.app_context():
            if db().execute("SELECT 1 FROM users WHERE email LIKE '%@smartevents.demo' LIMIT 1").fetchone():
                raise RuntimeError('Production startup blocked: use a clean database without demo accounts.')

    def input_guard():
        g.request_id=uuid.uuid4().hex
        # Webhooks have their own bounded raw-body signature and payload validation.
        if request.path=='/api/razorpay/webhook':return
        if request.path.startswith('/api/') and request.method in ('POST','PUT','PATCH','DELETE'):
            if request.method=='POST' and re.fullmatch(r'/api/clips/(event|memory)/\d+',request.path) and request.mimetype=='multipart/form-data':
                request.max_content_length=21*1024*1024
                return
            d=request.get_json(silent=True)
            if not isinstance(d,dict):return jsonify(error='Send a JSON object.'),400
            text_fields={'email':254,'password':256,'confirm_password':256,'current_password':256,'new_password':256,'confirm_new_password':256,'name':100,'city':100,'interests':200,
                         'title':150,'category':50,'venue':150,'date':40,'description':3000,'caption':2000,
                         'reason':500,'role':30,'purpose':20,'code':20,'token':200,'audience':30,'query':120,
                         'action':30,'provider':30,'decision':30,'kind':30,'request_id':80,'cover':3000000,'photo':3000000,
                         'order_id':80,'razorpay_order_id':80,'razorpay_payment_id':80,'razorpay_signature':128}
            for key,limit in text_fields.items():
                if key in d and (not isinstance(d[key],str) or len(d[key])>limit):
                    return jsonify(error=f'Invalid {key} field.'),400
            for key in ('qty','points','price','capacity','duration_minutes','lat','lon','id','event','remove'):
                if key in d and (isinstance(d[key],bool) or not isinstance(d[key],(int,float,str))):
                    return jsonify(error=f'Invalid {key} field.'),400
            for key in ('qty','points','price','capacity','duration_minutes','lat','lon','id','event','remove'):
                if key in d:
                    try:
                        value=float(d[key])
                        if not math.isfinite(value) or abs(value)>1000000000 or (key not in ('lat','lon') and not value.is_integer()):raise ValueError()
                    except (ValueError,TypeError,OverflowError):return jsonify(error=f'Invalid {key} field.'),400
            if 'seats' in d and (not isinstance(d['seats'],list) or len(d['seats'])>10 or any(type(v)!=int or not 0<v<1000000000 for v in d['seats'])):
                return jsonify(error='Invalid seat selection.'),400
            if 'email' in d and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',d['email'].strip()):
                return jsonify(error='Enter a valid email address.'),400
    # Validate before CSRF, lifecycle and any authentication handler.
    app.before_request_funcs.setdefault(None,[]).insert(0,input_guard)

    @app.after_request
    def security_headers(response):
        response.headers['X-Request-ID']=getattr(g,'request_id','')
        response.headers['Referrer-Policy']='strict-origin-when-cross-origin'
        response.headers['Permissions-Policy']='geolocation=(self), camera=(self), microphone=()'
        # Legacy UI still uses inline handlers/styles. Remove unsafe-inline after
        # a full event-listener refactor; do not pretend this is a strict CSP.
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob: https://tile.openstreetmap.org; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        if os.getenv('PAYMENT_PROVIDER')=='razorpay':
            policy=response.headers['Content-Security-Policy']
            policy=policy.replace("script-src 'self' 'unsafe-inline'", "script-src 'self' 'unsafe-inline' https://checkout.razorpay.com")
            policy=policy.replace("connect-src 'self'", "connect-src 'self' https://api.razorpay.com https://checkout.razorpay.com https://lumberjack.razorpay.com")
            policy=policy.replace("img-src 'self' data: blob:","img-src 'self' data: blob: https://*.razorpay.com")
            response.headers['Content-Security-Policy']=policy+"; frame-src https://api.razorpay.com https://checkout.razorpay.com"
        if production:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response

    @app.errorhandler(HTTPException)
    def http_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error=error.description,request_id=getattr(g,'request_id','')),error.code
        return error

    @app.errorhandler(sqlite3.OperationalError)
    def database_error(error):
        app.logger.error('Database operation failed request_id=%s',getattr(g,'request_id',''))
        return jsonify(error='Service temporarily unavailable. Please retry.',request_id=getattr(g,'request_id','')),503

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception('Unhandled request error request_id=%s',getattr(g,'request_id',''))
        return jsonify(error='An unexpected error occurred.',request_id=getattr(g,'request_id','')),500

    @app.get('/healthz')
    def health():
        db().execute('SELECT 1').fetchone()
        return jsonify(status='ok')
