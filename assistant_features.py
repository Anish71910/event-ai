"""Local trained mood classifier, grounded event retrieval and unread notices."""
import re,math,json,io,os
from collections import Counter
from datetime import datetime,timedelta
from pathlib import Path
from flask import request,jsonify,send_file
import qrcode
from client_updates import now

CORPUS={
 'relaxed':['relaxed calm peaceful quiet unwind stressed tired meditation yoga nature walk','relax and recharge outdoors art gentle music'],
 'energetic':['energetic excited dance party nightlife concert festival upbeat music','happy celebrate dancing live music fun'],
 'social':['social meet people friends networking community mixer business','lonely connect make friends group gathering'],
 'learning':['learning curious learn technology workshop conference coding science','study skills business seminar education'],
 'fun':['fun bored laugh comedy entertainment games sports food','cheer me up sad comedy laughter screening']}
MOOD_TAGS={'relaxed':['outdoors','art','yoga','meditation','acoustic','nature','wellness'], 'energetic':['music','nightlife','dance','festival'], 'social':['social','community','business','network','mixer'], 'learning':['technology','workshop','conference','education','business'], 'fun':['comedy','sports','food','screening','games']}
def words(s):return [w for w in re.findall(r'[a-z]+',s.lower()) if w not in {'i','me','my','and','or','the','a','an','to','for','of','up','with','is','am'}]
def train():
    counts={k:dict(Counter(words(' '.join(v)))) for k,v in CORPUS.items()}
    return {'algorithm':'multinomial-naive-bayes','counts':counts,'vocabulary':sorted({w for c in counts.values() for w in c})}
MODEL_PATH=Path(__file__).with_name('mood_model.json')
def predict(text):
    model=json.loads(MODEL_PATH.read_text());tokens=[w for w in words(text) if w in model['vocabulary']]
    if not tokens:return None
    scores={k:sum(math.log((c.get(w,0)+1)/(sum(c.values())+len(model['vocabulary']))) for w in tokens) for k,c in model['counts'].items()}
    return max(scores,key=scores.get)

def install(app,db,auth,user,one,rows,fail,limited):
    with app.app_context():
        if 'notice_seen' not in [r[1] for r in db().execute('PRAGMA table_info(users)')]:db().execute('ALTER TABLE users ADD COLUMN notice_seen INTEGER NOT NULL DEFAULT 0')
        db().commit()
    @app.route('/api/notice-status',methods=['GET','POST'])
    @auth()
    def notices():
        uid=user()['id'];latest=one('SELECT COALESCE(MAX(id),0) id FROM notices WHERE user=?',(uid,))['id']
        if request.method=='POST':
            seen=request.json.get('through',0)
            if type(seen)!=int or seen<0:return fail('Invalid notification marker.')
            db().execute('UPDATE users SET notice_seen=MAX(notice_seen,?) WHERE id=?',(min(seen,latest),uid));db().commit()
        seen=one('SELECT notice_seen FROM users WHERE id=?',(uid,))['notice_seen']
        return jsonify(latest=latest,count=one('SELECT COUNT(*) n FROM notices WHERE user=? AND id>?',(uid,seen))['n'])
    @app.get('/api/demo-payment-qr')
    @auth()
    def demo_qr():
        if not (os.getenv('DEMO_MODE','1')=='1' and os.getenv('PAYMENT_PROVIDER','demo')=='demo'):return fail('Demo payment is disabled.')
        output=io.BytesIO();qrcode.make('EVENTAI DEMO ONLY — NO PAYMENT — OTP 0000').save(output,format='PNG');output.seek(0)
        return send_file(output,mimetype='image/png')
    @app.post('/api/event-assistant')
    @auth()
    def assistant():
        if limited('assistant:'+str(user()['id']),30,60):return jsonify(error='Please wait a minute.'),429
        d=request.json;message=d.get('message','')
        if not isinstance(message,str) or not 1<=len(message.strip())<=500:return fail('Ask a question using 1–500 characters.')
        text=message.lower().strip();f=d.get('filters',{})
        if not isinstance(f,dict):return fail('Invalid filters.')
        def reply(answer,items=[]):return jsonify(answer=answer,events=items)
        if text in ['hi','hello','hey']:return reply('What mood, date, budget or city should I use?')
        if 'how' in text and ('book' in text or 'ticket' in text):return reply('Open an event, select seats and complete payment. The organizer then approves your ticket.')
        if 'otp' in text:return reply('Demo payment OTP: 0000. Account verification uses the OTP sent to your email.')
        if 'refund' in text:return reply('Cancel an unused booking before it starts from My orders. Captured payments require refund processing; demo payments move no money.')
        candidates=rows("SELECT e.id,e.title,e.category,e.description,e.city,e.venue,e.date,e.duration_minutes,e.price,e.lat,e.lon,u.name organizer,i.available FROM events e JOIN users u ON u.id=e.owner JOIN event_inventory i ON i.id=e.id WHERE e.status='approved' AND datetime(e.date)>datetime(?) AND i.available>0 ORDER BY e.date",(now().isoformat(),))
        named=[e['id'] for e in candidates if e['title'].lower() in text]
        mood=f.get('mood') or (None if named else predict(text))
        if mood and mood not in MOOD_TAGS:return fail('Choose a listed mood.')
        city=str(f.get('city','')).strip()
        if not city:
            known=rows('SELECT DISTINCT city FROM events')
            city=next((x['city'] for x in known if x['city'].lower() in text),'')
            loc=re.search(r'\b(?:in|near|at)\s+([a-z]+(?:\s+[a-z]+)?)(?=\s+(?:under|below|today|tomorrow|this|after|before|for)\b|[?.!,]|$)',text)
            if not city and loc:city=loc.group(1)
        if 'near me' in text and not f.get('city'):city=user().get('city','')
        if 'near me' in text and not city:return reply('Which city should I search? Add your city to your profile or use the City filter.')
        budget=f.get('budget','');m=re.search(r'(?:under|below|within|budget(?: of)?|up to|max(?:imum)?)\s*(?:rs\.?\s*|₹\s*)?(\d+(?:\.\d+)?)',text)
        if not m:m=re.search(r'(?:₹|rs\.?|inr)\s*(\d+(?:\.\d+)?)',text)
        if m:budget=m.group(1)
        if re.search(r'\bfree\b',text):budget=0
        try:
            ceiling=float(budget) if budget!='' else None
            if ceiling is not None and (not math.isfinite(ceiling) or ceiling<0):raise ValueError()
            day=f.get('date','');today=now().date()
            if 'tomorrow' in text:day=(today+timedelta(days=1)).isoformat()
            elif 'today' in text or 'tonight' in text:day=today.isoformat()
            explicit=re.search(r'\b\d{4}-\d{2}-\d{2}\b',text)
            if explicit:day=explicit.group()
            if day:datetime.strptime(day,'%Y-%m-%d')
        except (ValueError,TypeError):return fail('Use a valid budget and date (YYYY-MM-DD).')
        hour_range=None
        if not named:
            if 'morning' in text:hour_range=(5,12)
            elif 'afternoon' in text:hour_range=(12,17)
            elif 'evening' in text:hour_range=(17,22)
            elif 'tonight' in text or 'night' in text:hour_range=(18,24)
        clock=re.search(r'\b(after|before|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b',text)
        if clock:
            h=int(clock[2]);minute=int(clock[3] or 0)
            if not 1<=h<=12 or minute>59:return fail('Use a valid time such as after 6 pm.')
            point=h%12*60+minute+(720 if clock[4]=='pm' else 0)
        categories=[x['category'] for x in rows('SELECT DISTINCT category FROM events') if re.search(r'\b'+re.escape(x['category'].lower())+r'\b',text)]
        relevant=bool(named or mood or city or ceiling is not None or day or hour_range or clock or categories or re.search(r'\b(events?|recommend|suggest|available|show|find)\b',text))
        if not relevant:return reply('I can help with available events, bookings and demo payments. Ask about a mood, date, budget or city.')
        result=[]
        for e in candidates:
            if named and e['id'] not in named:continue
            date=datetime.fromisoformat(e['date']);tags=(e['category']+' '+e['description']+' '+e['title']).lower()
            if city and city.casefold() not in e['city'].casefold():continue
            price=one('''SELECT MIN(s.price) price FROM event_seats s WHERE s.event=? AND NOT EXISTS(
                SELECT 1 FROM seat_claims c LEFT JOIN bookings b ON b.id=c.booking
                LEFT JOIN seat_holds h ON h.provider=c.provider AND h.payment=c.payment
                WHERE c.seat=s.id AND (b.status='confirmed' OR (b.id IS NULL AND datetime(h.expires)>datetime(?))))''',(e['id'],now().isoformat()))['price']
            if price is not None:e['price']=price
            if ceiling is not None and e['price']>ceiling:continue
            if day and date.date().isoformat()!=day:continue
            if 'weekend' in text and not 0<=(date.date()-today).days<=((5-today.weekday())%7)+1:continue
            if 'weekend' in text and date.weekday()<5:continue
            if hour_range and not hour_range[0]<=date.hour<hour_range[1]:continue
            if clock and ((clock[1]=='after' and date.hour*60+date.minute<=point) or (clock[1]=='before' and date.hour*60+date.minute>=point) or (clock[1]=='at' and date.hour*60+date.minute!=point)):continue
            if categories and e['category'] not in categories:continue
            if mood and not categories and not any(tag in tags for tag in MOOD_TAGS[mood]):continue
            result.append(e)
        if not result:return reply('No available events match those preferences.')
        result=result[:3]
        if re.search(r'\b(?:price|cost|how much)\b',text):answer='\n'.join(f"{e['title']}: ₹{e['price']} per seat (from)." for e in result)
        elif re.search(r'\b(?:when|time|start|end)\b',text):answer='\n'.join(f"{e['title']}: {e['date'][:16].replace('T',' ')}–{(datetime.fromisoformat(e['date'])+timedelta(minutes=e['duration_minutes'])).strftime('%Y-%m-%d %H:%M')} IST." for e in result)
        elif re.search(r'\b(?:where|venue)\b',text):answer='\n'.join(f"{e['title']}: {e['venue']}, {e['city']}." for e in result)
        else:answer='\n'.join(f"{e['title']} — {e['city']}, {e['date'][:16].replace('T',' ')} IST, from ₹{e['price']}." for e in result)
        return reply(answer,result)

if __name__=='__main__':
    MODEL_PATH.write_text(json.dumps(train(),indent=2));print('Trained local mood classifier:',MODEL_PATH)
