"""Deterministic sparse TF-IDF + user-neighbour collaborative recommendations.

No third-party analytics or external model calls. Scores are ranking scores,
not probabilities or predictions of event success.
"""
import math
import re
from collections import Counter, defaultdict

VERSION = 'hybrid-tfidf-userknn-v1'
STOP = set('a an and are as at be by for from in is it of on or the this to with your'.split())

def tokens(text):
    return [t for t in re.findall(r'[^\W_]{2,}', str(text).casefold(), re.UNICODE) if t not in STOP][:1500]

def unit(vector):
    norm = math.sqrt(sum(v*v for v in vector.values()))
    return {k:v/norm for k,v in vector.items()} if norm else {}

def cosine(a, b):
    if len(a)>len(b): a,b=b,a
    return sum(v*b.get(k,0.0) for k,v in a.items())

def event_terms(event):
    terms=Counter()
    for field,weight in [('title',2),('category',4),('city',2),('venue',1),('description',1)]:
        for term,count in Counter(tokens(event.get(field,''))).items(): terms[term]+=weight*count
    return terms

def content_scores(catalog, candidates, interests, city, history, searches):
    docs={e['id']:event_terms(e) for e in catalog}
    df=Counter(term for doc in docs.values() for term in doc)
    idf={term:math.log((1+len(docs))/(1+n))+1 for term,n in df.items()}
    def vector(terms):
        return unit({term:(1+math.log(count))*idf[term] for term,count in terms.items() if term in idf and count>0})
    vectors={eid:vector(doc) for eid,doc in docs.items()}
    profile=defaultdict(float)
    initial=Counter(tokens(interests)*3+tokens(city))
    for term,value in vector(initial).items():profile[term]+=2*value
    for eid,weight in history.items():
        for term,value in vectors.get(eid,{}).items():profile[term]+=min(weight,12)*value
    for query in searches[-20:]:
        for term,value in vector(Counter(tokens(query))).items():profile[term]+=0.5*value
    profile=unit(profile)
    return {e['id']:max(0.0,cosine(profile,vectors.get(e['id'],{}))) for e in candidates}

def collaborative_scores(uid, matrix, candidates, neighbours=30):
    target=matrix.get(uid,{})
    if not target:return {e['id']:0.0 for e in candidates},0
    target_unit=unit(target);similar=[]
    for other,ratings in matrix.items():
        if other==uid:continue
        overlap=len(target.keys() & ratings.keys())
        if not overlap:continue
        # Shrink weak, one-item overlaps instead of over-confident recommendations.
        similarity=cosine(target_unit,unit(ratings))*overlap/(overlap+2)
        if similarity>0:similar.append((similarity,other))
    similar.sort(key=lambda x:(-x[0],x[1]));similar=similar[:neighbours]
    scores={e['id']:0.0 for e in candidates}
    denominator=sum(s for s,_ in similar)
    if denominator:
        for eid in scores:
            scores[eid]=sum(s*min(matrix[other].get(eid,0)/12,1) for s,other in similar)/denominator
    return scores,len(similar)

def rank(catalog, candidates, uid, matrix, interests='', city='', searches=(), personalized=True):
    content=content_scores(catalog,candidates,interests,city,matrix.get(uid,{}),searches) if personalized else {}
    collab,neighbours=collaborative_scores(uid,matrix,candidates) if personalized else ({},0)
    popularity={e['id']:0.0 for e in candidates}
    for other,ratings in matrix.items():
        if other==uid:continue
        for eid,value in ratings.items():
            if eid in popularity:popularity[eid]+=min(value/12,1)
    maximum=max(popularity.values(),default=0)
    popularity={k:v/maximum if maximum else 0.0 for k,v in popularity.items()}
    has_content=any(content.values());has_collab=any(collab.values())
    weights=({'content':.60,'collaborative':.30,'popularity':.10} if has_content and has_collab else
             {'content':.85,'collaborative':0.,'popularity':.15} if has_content else
             {'content':0.,'collaborative':.85,'popularity':.15} if has_collab else
             {'content':0.,'collaborative':0.,'popularity':1.})
    ranked=[]
    for e in candidates:
        eid=e['id'];c=content.get(eid,0.);f=collab.get(eid,0.);p=popularity.get(eid,0.)
        score=weights['content']*c+weights['collaborative']*f+weights['popularity']*p
        reason=('Based on your interests and recent activity' if c>0 and weights['content']*c>=weights['collaborative']*f else
                'People with similar activity engaged with this event' if f>0 else
                ('Popular near you' if city and e.get('city','').casefold()==city.casefold() else 'Popular with the community') if p>0 else 'An upcoming experience to explore')
        ranked.append(dict(e,recommendation={'score':round(score,6),'content':round(c,6),'collaborative':round(f,6),'popularity':round(p,6),'reason':reason}))
    ranked.sort(key=lambda e:(-e['recommendation']['score'],e['date'],e['id']))
    # Soft diversification: no more than two same-category cards in the first six,
    # then fill remaining slots when the catalogue has little variety.
    preferred=[];overflow=[];counts=Counter()
    for e in ranked:
        if len(preferred)<6 and counts[e['category']]>=2:overflow.append(e)
        else:preferred.append(e);counts[e['category']]+=1
    return preferred+overflow,{'version':VERSION,'weights':weights,'has_collaborative_signal':has_collab,
                              'mode':'hybrid' if has_content and has_collab else 'content' if has_content else 'collaborative' if has_collab else 'discovery',
                              'personalized':personalized,'cold_start':not bool(matrix.get(uid))}
