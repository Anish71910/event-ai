"""Role-scoped analytics, discovery categories and verified-attendee reviews."""
from flask import request,jsonify
from client_updates import now


def install(app,db,auth,user,one,rows,fail,notice):
    with app.app_context():
        db().executescript('''CREATE TABLE IF NOT EXISTS event_reviews(
          id INTEGER PRIMARY KEY,user INTEGER NOT NULL REFERENCES users(id),event INTEGER NOT NULL REFERENCES events(id),
          rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),comment TEXT NOT NULL,created TEXT NOT NULL,
          UNIQUE(user,event));''')
        db().commit()

    @app.get('/api/categories')
    def categories():
        counts={r['category']:r['count'] for r in rows("SELECT category,COUNT(*) count FROM events WHERE status='approved' AND datetime(date,'+'||duration_minutes||' minutes')>datetime(?) GROUP BY category",(now().isoformat(),))}
        names=list(dict.fromkeys(['Music','Nightlife','Comedy','Sports','Performances','Navratri','Fests & Fairs','Food & Drinks','Social Mixers','Screenings','Fitness','Conferences','Open Mics','Technology','Food','Art','Outdoors','Business']+sorted(counts)))
        return jsonify([dict(name=n,count=counts.get(n,0)) for n in names])

    @app.get('/api/reviews/<int:eid>')
    def reviews(eid):
        e=one('SELECT * FROM events WHERE id=?',(eid,));u=user()
        if not e or (e['status']!='approved' and not (u and (u['role']=='admin' or e['owner']==u['id'] or one('SELECT id FROM bookings WHERE user=? AND event=?',(u['id'],eid))))):return fail('Event unavailable.')
        items=rows('SELECT r.id,r.rating,r.comment,r.created,u.name FROM event_reviews r JOIN users u ON u.id=r.user WHERE r.event=? ORDER BY r.id DESC LIMIT 100',(eid,))
        aggregate=one('SELECT COUNT(*) count,ROUND(AVG(rating),1) average FROM event_reviews WHERE event=?',(eid,))
        mine=one('SELECT rating,comment FROM event_reviews WHERE user=? AND event=?',(u['id'],eid)) if u else None
        eligible=bool(u and u['verified'] and one("SELECT b.id FROM bookings b JOIN events e ON e.id=b.event WHERE b.user=? AND b.event=? AND b.status='confirmed' AND datetime(e.date,'+'||e.duration_minutes||' minutes')<=datetime(?)",(u['id'],eid,now().isoformat())))
        return jsonify(reviews=items,summary=aggregate,mine=mine,can_review=eligible)

    @app.post('/api/reviews/<int:eid>')
    @auth()
    def save_review(eid):
        d=request.json;rating=d.get('rating');comment=d.get('comment','')
        if isinstance(rating,bool) or not isinstance(rating,int) or not 1<=rating<=5 or not isinstance(comment,str) or not 10<=len(comment.strip())<=2000:return fail('Choose 1–5 stars and write a review of 10–2000 characters.')
        db().execute('BEGIN IMMEDIATE')
        eligible=one("SELECT e.owner,e.title FROM bookings b JOIN events e ON e.id=b.event WHERE b.user=? AND b.event=? AND b.status='confirmed' AND datetime(e.date,'+'||e.duration_minutes||' minutes')<=datetime(?)",(user()['id'],eid,now().isoformat()))
        if not eligible:db().rollback();return fail('Reviews are available after attending a completed event with a confirmed booking.')
        exists=one('SELECT id FROM event_reviews WHERE user=? AND event=?',(user()['id'],eid))
        db().execute('INSERT INTO event_reviews(user,event,rating,comment,created) VALUES(?,?,?,?,?) ON CONFLICT(user,event) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,created=excluded.created',
            (user()['id'],eid,rating,comment.strip(),now().isoformat()))
        if not exists:notice(eligible['owner'],'New attendee review for '+eligible['title']+'.')
        db().commit();return jsonify(message='Your review has been saved.')

    @app.get('/api/analytics')
    @auth(['organizer','admin'])
    def analytics():
        u=user();scope='1=1' if u['role']=='admin' else 'e.owner=?';args=() if u['role']=='admin' else (u['id'],)
        categories=rows(f"SELECT e.category label,COALESCE(SUM(b.qty),0) value FROM events e LEFT JOIN bookings b ON b.event=e.id AND b.status='confirmed' WHERE {scope} GROUP BY e.category ORDER BY value DESC",args)
        sales=rows(f"SELECT substr(b.created,1,7) label,SUM(b.total) value,SUM(b.qty) tickets FROM bookings b JOIN events e ON e.id=b.event WHERE b.status='confirmed' AND {scope} GROUP BY substr(b.created,1,7) ORDER BY label DESC LIMIT 6",args)[::-1]
        events=rows(f"SELECT e.title label,i.confirmed,i.held,i.available FROM events e JOIN event_inventory i ON i.id=e.id WHERE {scope} AND datetime(e.date,'+'||e.duration_minutes||' minutes')>datetime(?) ORDER BY e.date LIMIT 8",args+(now().isoformat(),))
        ratings=rows(f"SELECT CAST(r.rating AS TEXT)||' stars' label,COUNT(*) value FROM event_reviews r JOIN events e ON e.id=r.event WHERE {scope} GROUP BY r.rating ORDER BY r.rating",args)
        return jsonify(categories=categories,sales=sales,inventory=events,ratings=ratings,scope='Platform' if u['role']=='admin' else 'Your events')
