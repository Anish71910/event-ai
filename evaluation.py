"""Offline temporal holdout evaluation using the existing hybrid ranker."""
import time
from collections import defaultdict
from flask import request,jsonify
from recommendation_engine import rank


def metrics(predictions,truth,k):
    if not predictions:return dict(precision=None,recall=None,hit_rate=None,users=0)
    precision=recall=hits=0
    for uid,ordered in predictions.items():
        relevant=set(truth[uid]);found=len(set(ordered[:k])&relevant)
        precision+=found/k;recall+=found/len(relevant);hits+=int(found>0)
    n=len(predictions)
    return dict(precision=precision/n,recall=recall/n,hit_rate=hits/n,users=n)


def evaluate(catalog,users,signals,k):
    predictions={};truth={}
    for u in users:
        purchases=sorted((s for s in signals if s['user']==u['id'] and s['kind']=='booking'),key=lambda s:(s['created'],s['event']))
        distinct=[]
        for s in purchases:
            if s['event'] not in [x['event'] for x in distinct]:distinct.append(s)
        if len(distinct)<2:continue
        test=distinct[-1];cutoff=test['created'];target=test['event'];matrix=defaultdict(dict)
        latest={}
        for s in signals:
            if s['created']>=cutoff or (s['user']==u['id'] and s['event']==target):continue
            key=(s['user'],s['event'],s['kind'])
            if key not in latest or latest[key]['created']<s['created']:latest[key]=s
        for s in latest.values():
            decay=.5**(max(0,cutoff-s['created'])/(30*86400))
            matrix[s['user']][s['event']]=min(12,matrix[s['user']].get(s['event'],0)+s['weight']*decay)
        seen={s['event'] for s in purchases if s['created']<cutoff}
        candidates=[e for e in catalog if e['id'] not in seen]
        if target not in {e['id'] for e in candidates}:continue
        ordered,_=rank(catalog,candidates,u['id'],matrix,interests=u['interests'],city=u['city'])
        predictions[u['id']]=[e['id'] for e in ordered[:k]];truth[u['id']]={target}
    return metrics(predictions,truth,k)


def install(app,auth,rows,fail):
    @app.get('/api/admin/recommendation-evaluation')
    @auth(['admin'])
    def evaluation():
        k=int(request.args.get('k',5))
        if not 1<=k<=20:return fail('K must be between 1 and 20.')
        users=rows("SELECT u.id,u.interests,u.city FROM users u JOIN recommendation_preferences p ON p.user=u.id WHERE p.enabled=1 AND u.role='user' AND u.verified=1 AND u.disabled=0 ORDER BY u.id LIMIT 200")
        cutoff=time.time()-90*86400
        signals=rows('''SELECT b.user,b.event,'booking' kind,8.0 weight,CAST(strftime('%s',b.created) AS REAL) created FROM bookings b
          JOIN recommendation_preferences p ON p.user=b.user AND p.enabled=1 WHERE b.status='confirmed' AND b.id>p.booking_after_id AND CAST(strftime('%s',b.created) AS REAL)>=MAX(p.since,?)
          UNION ALL SELECT i.user,i.event,'view',1.0,i.created FROM interactions i JOIN recommendation_preferences p ON p.user=i.user AND p.enabled=1 WHERE i.kind='view' AND i.created>=MAX(p.since,?)
          UNION ALL SELECT l.user,l.event,'like',4.0,l.created FROM event_likes l JOIN recommendation_preferences p ON p.user=l.user AND p.enabled=1 WHERE l.created>=MAX(p.since,?)
          ORDER BY created DESC LIMIT 20000''',(cutoff,cutoff,cutoff))
        catalog=rows('SELECT id,title,category,city,venue,description,date FROM events ORDER BY id DESC LIMIT 2000')
        result=evaluate(catalog,users,signals,k)
        return jsonify(k=k,**result,method='Temporal last-distinct-booking holdout. All training interactions occur before each held-out booking; the target user’s held-out event signals are excluded.',limitations='Requires at least two distinct confirmed bookings per opted-in user. Current metadata/interests are snapshots; historical availability is not reconstructed. Up to 200 users, 2000 events and 20000 signals. These are offline ranking metrics, not live conversion or accuracy guarantees.')
