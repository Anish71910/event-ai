"""
AI Agent Engine for EventAI.
Provides an autonomous conversational assistant capable of:
- Discovering, filtering and recommending events
- Rendering interactive live seat layouts
- Selecting specific seats (by label A1/B2 or ID)
- Booking tickets with demo checkout (card, UPI, net banking) or free reservations
- Inspecting user bookings and QR tickets
- Answering queries about venues, prices, times, and policies
Supports optional Google Gemini API enhancement when GEMINI_API_KEY is present.
"""
import os
import re
import math
import json
import secrets
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from flask import request, jsonify, session
from client_updates import now

def install(app, db, auth, user, one, rows, fail, notice, limited):

    def ensure_seat_plan(eid):
        """Ensures the event has numbered seat inventory generated."""
        plan_func = app.extensions.get('eventai_default_plan')
        if plan_func:
            try:
                plan_func(eid)
            except Exception:
                pass

    def search_events(text="", city="", category="", budget=None, date="", mood=""):
        candidates = rows(
            """SELECT e.id, e.title, e.category, e.description, e.city, e.venue, e.date,
                      e.duration_minutes, e.price, e.lat, e.lon, u.name as organizer,
                      COALESCE(i.available, e.capacity) as available
               FROM events e
               JOIN users u ON u.id=e.owner
               LEFT JOIN event_inventory i ON i.id=e.id
               WHERE e.status='approved' AND datetime(e.date) > datetime(?) AND COALESCE(i.available, e.capacity) > 0
               ORDER BY e.date""",
            (now().isoformat(),)
        )

        text_lower = text.lower().strip()
        filtered = []

        # Find named matches first
        named = [e for e in candidates if e['title'].lower() in text_lower]

        for e in candidates:
            if named and e not in named:
                continue

            event_date = datetime.fromisoformat(e['date'])
            tags = f"{e['category']} {e['description']} {e['title']}".lower()

            if city and city.casefold() not in e['city'].casefold():
                continue

            # Update with minimum seat price if custom seat layout exists
            min_price = one(
                """SELECT MIN(s.price) as price FROM event_seats s
                   WHERE s.event=? AND NOT EXISTS(
                       SELECT 1 FROM seat_claims c
                       LEFT JOIN bookings b ON b.id=c.booking
                       LEFT JOIN seat_holds h ON h.provider=c.provider AND h.payment=c.payment
                       WHERE c.seat=s.id AND (b.status='confirmed' OR (b.id IS NULL AND datetime(h.expires)>datetime(?)))
                   )""",
                (e['id'], now().isoformat())
            )
            if min_price and min_price['price'] is not None:
                e['price'] = min_price['price']

            if budget is not None and e['price'] > budget:
                continue

            if date and event_date.date().isoformat() != date:
                continue

            if category and e['category'].lower() != category.lower():
                continue

            filtered.append(e)

        return filtered

    def get_event_seat_chart(eid):
        """Fetches seat layout with live states for the agent."""
        e = one("SELECT * FROM events WHERE id=?", (eid,))
        if not e:
            return None

        ensure_seat_plan(eid)

        seat_rows = rows(
            """SELECT s.id, s.event, s.section, s.row_label, s.number, s.price, s.aisle,
                      CASE WHEN b.status='confirmed' THEN 'booked'
                           WHEN b.id IS NULL AND datetime(h.expires)>datetime(?) THEN 'held'
                           ELSE 'available' END as state
               FROM event_seats s
               LEFT JOIN seat_claims c ON c.seat=s.id
               LEFT JOIN bookings b ON b.id=c.booking
               LEFT JOIN seat_holds h ON h.provider=c.provider AND h.payment=c.payment
               WHERE s.event=?
               ORDER BY s.section, s.row_label, s.number""",
            (now().isoformat(), eid)
        )

        return {
            'event_id': e['id'],
            'title': e['title'],
            'venue': e['venue'],
            'city': e['city'],
            'date': e['date'],
            'capacity': e['capacity'],
            'seats': seat_rows
        }

    def resolve_seats(eid, seat_identifiers):
        """Converts seat labels (e.g. 'A1', 'B2') or IDs into seat records."""
        ensure_seat_plan(eid)
        all_seats = rows(
            """SELECT s.id, s.row_label, s.number, s.price, s.section,
                      CASE WHEN b.status='confirmed' THEN 'booked'
                           WHEN b.id IS NULL AND datetime(h.expires)>datetime(?) THEN 'held'
                           ELSE 'available' END as state
               FROM event_seats s
               LEFT JOIN seat_claims c ON c.seat=s.id
               LEFT JOIN bookings b ON b.id=c.booking
               LEFT JOIN seat_holds h ON h.provider=c.provider AND h.payment=c.payment
               WHERE s.event=?""",
            (now().isoformat(), eid)
        )

        resolved = []
        for ident in seat_identifiers:
            found = None
            if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
                sid = int(ident)
                found = next((s for s in all_seats if s['id'] == sid), None)
            elif isinstance(ident, str):
                ident_norm = ident.strip().upper()
                # match e.g. "A1", "A-1", "ROW A 1"
                m = re.match(r'^(?:ROW\s*)?([A-Z0-9]+)[-\s]*(\d+)$', ident_norm)
                if m:
                    rlabel, snum = m.group(1), int(m.group(2))
                    found = next((s for s in all_seats if s['row_label'].upper() == rlabel and s['number'] == snum), None)

            if found:
                resolved.append(found)

        return resolved

    def execute_booking(u, eid, seat_ids, qty, method, demo_otp, points=0, request_id=None):
        """Performs ticket booking on behalf of the user."""
        if not u:
            return {'success': False, 'error': 'Please sign in to book tickets.'}
        if not u.get('verified'):
            return {'success': False, 'error': 'Verify your email before booking.'}

        e = one("SELECT * FROM events WHERE id=?", (eid,))
        if not e or e['status'] != 'approved' or e['date'] <= now().isoformat():
            return {'success': False, 'error': 'Event is no longer available for booking.'}

        quote_fn = app.extensions.get('eventai_seat_quote')
        hold_fn = app.extensions.get('eventai_hold_seats')
        if not quote_fn or not hold_fn:
            return {'success': False, 'error': 'Booking service unavailable.'}

        quote_data = {'seats': seat_ids}
        try:
            gross, selected_seats = quote_fn(e, quote_data, qty)
        except ValueError as err:
            return {'success': False, 'error': str(err)}

        if gross > 0:
            if not demo_otp or str(demo_otp).strip() != '0000':
                return {'success': False, 'error': 'Please enter demo OTP 0000 to simulate payment.'}
            if method not in ['demo_card', 'demo_upi', 'demo_netbanking']:
                method = 'demo_card'
        else:
            method = 'free'

        points = int(points or 0)
        max_points = min(max(0, u['points']), gross // 5)
        if points > max_points:
            points = max_points

        amount = gross - points
        req_key = request_id or secrets.token_hex(16)
        payload = hashlib.sha256(json.dumps([e['id'], qty, points, method, selected_seats]).encode()).hexdigest()

        db().execute('BEGIN IMMEDIATE')
        try:
            existing = one('SELECT * FROM demo_payments WHERE user=? AND request_id=?', (u['id'], req_key))
            if existing:
                db().rollback()
                return {
                    'success': True,
                    'payment_id': existing['id'],
                    'reference': existing['reference'],
                    'status': existing['status'],
                    'amount': existing['amount'],
                    'seats': selected_seats,
                    'message': 'Booking previously confirmed.'
                }

            avail = one('SELECT available FROM event_inventory WHERE id=?', (e['id'],))['available']
            if qty > avail:
                db().rollback()
                return {'success': False, 'error': 'Not enough seats available.'}

            stamp = now()
            ref = ('booking_' if method == 'free' else 'demo_pay_') + secrets.token_hex(10)
            pid = db().execute(
                """INSERT INTO demo_payments(user, event, request_id, payload, reference, qty,
                   unit_price, redeemed, amount, method, created, expires)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (u['id'], e['id'], req_key, payload, ref, qty, e['price'], points, amount, method,
                 stamp.isoformat(), min(stamp + timedelta(hours=24), datetime.fromisoformat(e['date'])).isoformat())
            ).lastrowid

            db().execute('UPDATE demo_payments SET seat_json=? WHERE id=?', (json.dumps(selected_seats), pid))
            expiry = one('SELECT expires FROM demo_payments WHERE id=?', (pid,))['expires']

            if not hold_fn('demo', pid, e['id'], qty, expiry):
                db().rollback()
                return {'success': False, 'error': 'Selected seats could not be held. Please try other seats.'}

            notice(e['owner'], f'Booking request {ref}: {u["name"]}, ₹{amount}, {qty} ticket(s) for {e["title"]}. Awaiting organizer approval.')
            notice(u['id'], f'Seats reserved for {e["title"]}. Awaiting organizer approval; no QR ticket issued yet.')
            db().commit()

            return {
                'success': True,
                'payment_id': pid,
                'reference': ref,
                'status': 'pending',
                'amount': amount,
                'qty': qty,
                'seats': selected_seats,
                'event_title': e['title'],
                'message': 'Booking request submitted! Seats held pending organizer approval.'
            }
        except Exception as ex:
            db().rollback()
            return {'success': False, 'error': str(ex)}

    def get_user_reservations(uid):
        """Fetches active bookings and pending payment requests for the user."""
        confirmed = rows(
            """SELECT b.id, b.token, b.qty, b.total, b.status, b.created,
                      e.id as event_id, e.title, e.date, e.venue, e.city,
                      (SELECT GROUP_CONCAT(s.section || ' ' || s.row_label || s.number, ', ')
                       FROM seat_claims sc JOIN event_seats s ON s.id=sc.seat
                       WHERE sc.booking=b.id) as seats
               FROM bookings b
               JOIN events e ON e.id=b.event
               WHERE b.user=? AND b.status='confirmed'
               ORDER BY b.id DESC LIMIT 10""",
            (uid,)
        )
        pending = rows(
            """SELECT p.id, p.reference, p.qty, p.amount, p.status, p.created, p.seat_json,
                      e.id as event_id, e.title, e.date, e.venue, e.city
               FROM demo_payments p
               JOIN events e ON e.id=p.event
               WHERE p.user=? AND p.status='pending'
               ORDER BY p.id DESC LIMIT 10""",
            (uid,)
        )
        return {'confirmed': confirmed, 'pending': pending}

    def call_gemini_api(user_prompt, conversation_history, system_context):
        """Optional call to Google Gemini API when GEMINI_API_KEY is configured."""
        api_key = os.getenv('GEMINI_API_KEY')
        if not api_key:
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        messages = []
        for turn in conversation_history[-6:]:
            role = "user" if turn.get('sender') == 'user' else "model"
            messages.append({"role": role, "parts": [{"text": turn.get('text', '')}]})

        messages.append({"role": "user", "parts": [{"text": f"{system_context}\n\nUser request: {user_prompt}"}]})

        payload = {
            "contents": messages,
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 600
            }
        }

        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                candidates = data.get('candidates', [])
                if candidates:
                    parts = candidates[0].get('content', {}).get('parts', [])
                    if parts and 'text' in parts[0]:
                        return parts[0]['text'].strip()
        except Exception:
            return None

        return None

    # Endpoints
    @app.post('/api/ai-agent/chat')
    def agent_chat():
        u = user()
        rate_key = 'ai_agent:' + str(u['id'] if u else request.remote_addr)
        if limited(rate_key, 40, 60):
            return jsonify(error='Please slow down. Try again in a moment.'), 429

        data = request.json or {}
        message = data.get('message', '').strip()
        if not message or len(message) > 600:
            return fail('Please provide a message under 600 characters.')

        context = data.get('context', {})
        current_event_id = context.get('event_id')
        current_event = one("SELECT * FROM events WHERE id=?", (current_event_id,)) if current_event_id else None
        history = context.get('history', [])

        text_lower = message.lower()

        # 1. MY BOOKINGS / TICKETS INTENT
        if any(kw in text_lower for kw in ['my ticket', 'my booking', 'my order', 'booked ticket', 'view ticket', 'show my tickets']):
            if not u:
                return jsonify({
                    'text': 'Please sign in to view your tickets and reservations.',
                    'action': 'require_auth',
                    'suggestions': ['Sign In ↗', 'Find Events']
                })
            res = get_user_reservations(u['id'])
            total_count = len(res['confirmed']) + len(res['pending'])
            if total_count == 0:
                return jsonify({
                    'text': "You don't have any bookings yet. Would you like me to recommend some upcoming events?",
                    'action': 'show_bookings',
                    'data': res,
                    'suggestions': ['Show Music Events', 'Events in Bengaluru', 'Explore Events']
                })
            return jsonify({
                'text': f"Here are your current bookings and reservations ({total_count} total):",
                'action': 'show_bookings',
                'data': res,
                'suggestions': ['Book another event', 'Help with tickets']
            })

        # 2. SEAT SELECTION & SHOW SEATS INTENT
        # Check if user mentions seat labels (e.g. "A1", "B2", "select seat A1 and A2")
        seat_matches = re.findall(r'\b[A-Za-z]\s*[-]?\s*\d{1,2}\b', message)
        wants_seats = any(kw in text_lower for kw in ['seat', 'seats', 'chair', 'layout', 'seat map', 'seating'])

        # Resolve event if specified in message
        matched_event = None
        all_approved = rows("SELECT id, title, price, city, venue, date FROM events WHERE status='approved' AND datetime(date)>datetime(?)", (now().isoformat(),))
        for ev in all_approved:
            if ev['title'].lower() in text_lower:
                matched_event = ev
                break

        target_event = matched_event or current_event

        # If user explicitly specified seat labels (e.g., "select A1, A2")
        if seat_matches and target_event:
            resolved = resolve_seats(target_event['id'], seat_matches)
            if resolved:
                avail_resolved = [s for s in resolved if s['state'] == 'available']
                unavail = [s for s in resolved if s['state'] != 'available']
                chart = get_event_seat_chart(target_event['id'])
                selected_ids = [s['id'] for s in avail_resolved]
                total_cost = sum(s['price'] for s in avail_resolved)
                seat_names = [f"{s['row_label']}{s['number']}" for s in avail_resolved]

                reply_msg = f"Selected {len(avail_resolved)} seat(s): {', '.join(seat_names)} for **{target_event['title']}** (Total: ₹{total_cost})."
                if unavail:
                    unavail_names = [f"{s['row_label']}{s['number']}" for s in unavail]
                    reply_msg += f" Note: Seat(s) {', '.join(unavail_names)} are already booked or held."

                return jsonify({
                    'text': reply_msg,
                    'action': 'seats_selected',
                    'data': {
                        'event': target_event,
                        'seats': chart['seats'],
                        'selected_seats': selected_ids,
                        'total_price': total_cost
                    },
                    'suggestions': [f"Book seats ({', '.join(seat_names)}) →", 'Change Seats', 'Browse other events']
                })

        # If user asks to view/show seats for an event
        if wants_seats or 'select' in text_lower:
            if target_event:
                chart = get_event_seat_chart(target_event['id'])
                if chart:
                    return jsonify({
                        'text': f"Here is the live seating plan for **{target_event['title']}** at {target_event['venue']}. Click any seat below to select or change:",
                        'action': 'show_seats',
                        'data': {
                            'event': target_event,
                            'seats': chart['seats'],
                            'selected_seats': context.get('selected_seats', [])
                        },
                        'suggestions': ['Confirm Selection', 'View Event Details', 'Back to Events']
                    })

        # 3. BOOKING / CHECKOUT INTENT
        is_book_intent = any(kw in text_lower for kw in ['book', 'checkout', 'confirm booking', 'buy ticket', 'reserve', 'pay'])
        if is_book_intent and (target_event or context.get('selected_seats')):
            if not target_event and current_event:
                target_event = current_event

            if target_event:
                sel_seats = context.get('selected_seats', [])
                # If specific seats weren't selected, check if user provided a quantity or choose first available
                qty_match = re.search(r'\b(\d+)\s*(?:ticket|tickets|seat|seats|person|people)\b', text_lower)
                qty = int(qty_match.group(1)) if qty_match else (len(sel_seats) if sel_seats else 1)

                ensure_seat_plan(target_event['id'])
                chart = get_event_seat_chart(target_event['id'])
                avail_seats = [s for s in chart['seats'] if s['state'] == 'available'] if chart else []

                if not sel_seats and avail_seats:
                    # Auto pick requested quantity of adjacent/available seats
                    sel_seats = [s['id'] for s in avail_seats[:min(qty, len(avail_seats))]]

                chosen_seat_objs = [s for s in chart['seats'] if s['id'] in sel_seats] if chart else []
                total_cost = sum(s['price'] for s in chosen_seat_objs) if chosen_seat_objs else target_event['price'] * qty
                seat_labels = [f"{s['row_label']}{s['number']}" for s in chosen_seat_objs]

                # Check if user already provided demo OTP or confirmation in message
                if '0000' in message or 'confirm' in text_lower:
                    if not u:
                        return jsonify({
                            'text': 'Please sign in to complete your booking.',
                            'action': 'require_auth',
                            'data': {'event': target_event, 'selected_seats': sel_seats},
                            'suggestions': ['Sign In ↗']
                        })

                    book_res = execute_booking(
                        u=u,
                        eid=target_event['id'],
                        seat_ids=sel_seats,
                        qty=len(sel_seats) or qty,
                        method='demo_card' if total_cost > 0 else 'free',
                        demo_otp='0000'
                    )

                    if book_res.get('success'):
                        return jsonify({
                            'text': f"🎉 **Booking confirmed!** Your seats ({', '.join(seat_labels)}) for **{target_event['title']}** have been held. Reference: `{book_res['reference']}`. Awaiting organizer approval.",
                            'action': 'booking_confirmed',
                            'data': book_res,
                            'suggestions': ['View My Tickets', 'Book Another Event']
                        })
                    else:
                        return jsonify({
                            'text': f"Booking could not be completed: {book_res.get('error')}",
                            'action': 'booking_error',
                            'suggestions': ['Select Different Seats', 'Try Again']
                        })

                # Otherwise, display the booking confirmation card in chat
                return jsonify({
                    'text': f"Ready to book **{target_event['title']}**!\n"
                            f"• Seats: {', '.join(seat_labels) if seat_labels else f'{qty} General Admission'}\n"
                            f"• Date: {target_event['date'][:10]} at {target_event['venue']}\n"
                            f"• Amount: ₹{total_cost}\n\n"
                            f"Confirm your simulated payment below (Demo OTP: `0000`):",
                    'action': 'booking_ready',
                    'data': {
                        'event': target_event,
                        'seats': chart['seats'] if chart else [],
                        'selected_seats': sel_seats,
                        'seat_labels': seat_labels,
                        'qty': len(sel_seats) or qty,
                        'total_price': total_cost,
                        'user_points': u.get('points', 0) if u else 0
                    },
                    'suggestions': ['Confirm Booking (Demo OTP: 0000) ⚡', 'Change Seats', 'Cancel']
                })

        # 4. GENERAL FAQ AND POLICIES
        if 'otp' in text_lower:
            return jsonify({
                'text': "For simulated payments, use demo OTP: **0000** (no SMS is sent). For account verification, use the 6-digit code sent to your registered email.",
                'action': 'info',
                'suggestions': ['Find Events', 'How to Book']
            })

        if 'refund' in text_lower or 'cancel' in text_lower:
            return jsonify({
                'text': "You can cancel unused bookings for upcoming events before they start under **My Orders / Tickets**. Demo payments move no real money, and held seats are immediately released upon cancellation.",
                'action': 'info',
                'suggestions': ['View My Tickets', 'Find Events']
            })

        if any(kw in text_lower for kw in ['how to book', 'how do i book', 'help']):
            return jsonify({
                'text': "Here is how to book tickets:\n1. Ask me to find an event or pick one from the list.\n2. Click **Select Seats** to view the live seating map.\n3. Click your preferred seats (up to 10).\n4. Click **Confirm Booking** with demo OTP `0000`.\n5. The organizer approves your ticket, and your unique QR code is generated!",
                'action': 'info',
                'suggestions': ['Browse Music Events', 'Events in Bengaluru', 'My Tickets']
            })

        # 5. SEARCH & RECOMMEND EVENTS INTENT
        # Extract city if any
        city = ""
        for known_city in ['Davangere', 'Bengaluru', 'Mysuru', 'Mumbai', 'Delhi', 'Chennai', 'Pune']:
            if known_city.lower() in text_lower:
                city = known_city
                break

        # Extract budget
        budget = None
        m_budget = re.search(r'(?:under|below|budget|within|up to)\s*(?:rs\.?|₹)?\s*(\d+)', text_lower)
        if m_budget:
            budget = float(m_budget.group(1))
        elif 'free' in text_lower:
            budget = 0.0

        # Extract category
        category = ""
        for cat in ['Music', 'Technology', 'Food', 'Art', 'Outdoors', 'Business', 'Comedy']:
            if cat.lower() in text_lower:
                category = cat
                break

        events_result = search_events(text=message, city=city, category=category, budget=budget)

        if events_result:
            # If user asked specific price for an event
            if any(kw in text_lower for kw in ['price', 'cost', 'how much']) and len(events_result) == 1:
                ev = events_result[0]
                return jsonify({
                    'text': f"**{ev['title']}**: ₹{ev['price']} per seat (starting price). Would you like to view the seat map?",
                    'action': 'show_events',
                    'data': {'events': events_result},
                    'suggestions': [f"Select seats for {ev['title']} 🪑", 'Details', 'Other events']
                })

            # Check if Gemini API can synthesize a personalized response
            gemini_intro = None
            if os.getenv('GEMINI_API_KEY'):
                prompt_ctx = f"EventAI website events found: {[{'title': e['title'], 'city': e['city'], 'price': e['price'], 'date': e['date']} for e in events_result[:3]]}. Recommend them warmly in 2-3 sentences."
                gemini_intro = call_gemini_api(message, history, prompt_ctx)

            text_reply = gemini_intro or f"I found {len(events_result)} exciting upcoming event(s) matching your request:"
            return jsonify({
                'text': text_reply,
                'action': 'show_events',
                'data': {'events': events_result[:6]},
                'suggestions': [f"Select seats for {events_result[0]['title']} 🪑", 'Show all seats', 'Filter by city']
            })

        # Fallback if no specific events matched
        all_upcoming = search_events()[:3]
        return jsonify({
            'text': "I couldn't find any events matching those exact filters. Here are some popular upcoming experiences you might enjoy:",
            'action': 'show_events',
            'data': {'events': all_upcoming},
            'suggestions': ['Explore all events', 'Free events', 'Events in Bengaluru']
        })

    @app.get('/api/ai-agent/seats/<int:eid>')
    def agent_seats(eid):
        chart = get_event_seat_chart(eid)
        if not chart:
            return jsonify(error='Event not found.'), 404
        return jsonify(chart)

    @app.post('/api/ai-agent/book')
    @auth()
    def agent_book():
        u = user()
        d = request.json or {}
        eid = d.get('event')
        qty = int(d.get('qty', 1))
        seats = d.get('seats', [])
        method = d.get('method', 'demo_card')
        demo_otp = d.get('demo_otp', '0000')
        points = int(d.get('points', 0))

        res = execute_booking(u, eid, seats, qty, method, demo_otp, points)
        if not res.get('success'):
            return fail(res.get('error', 'Booking failed.'))
        return jsonify(res)

    @app.get('/api/ai-agent/my-bookings')
    @auth()
    def agent_my_bookings():
        return jsonify(get_user_reservations(user()['id']))

