import json
from test_recommendations import rec
import app as module

def test_agent_chat_search(rec):
    c, p, login = rec
    with module.app.app_context():
        module.db().execute("UPDATE events SET price=300 WHERE id=100")
        module.db().commit()

    # Search query
    r = p('ai-agent/chat', {'message': 'show music events in Davangere'}).json
    assert r['action'] == 'show_events'
    assert any(e['id'] == 100 for e in r['data']['events'])

def test_agent_seat_chart_and_selection(rec):
    c, p, login = rec
    # Inspect seats for event 100
    r = p('ai-agent/chat', {'message': 'show seats for Acoustic guitar night'}).json
    assert r['action'] == 'show_seats'
    assert r['data']['event']['id'] == 100
    assert len(r['data']['seats']) > 0

    # Select specific seats by label
    r2 = p('ai-agent/chat', {
        'message': 'select seat A1 and A2',
        'context': {'event_id': 100}
    }).json
    assert r2['action'] == 'seats_selected'
    assert len(r2['data']['selected_seats']) == 2

def test_agent_booking_flow(rec):
    c, p, login = rec
    with module.app.app_context():
        module.db().execute("UPDATE events SET price=150 WHERE id=100")
        module.db().commit()

    # Ready to book
    r = p('ai-agent/chat', {
        'message': 'book 1 ticket for Acoustic guitar night',
        'context': {'event_id': 100}
    }).json
    assert r['action'] == 'booking_ready'
    assert r['data']['total_price'] > 0

    # Confirm booking with demo OTP
    r2 = p('ai-agent/chat', {
        'message': 'confirm booking with OTP 0000',
        'context': {'event_id': 100, 'selected_seats': r['data']['selected_seats']}
    }).json
    assert r2['action'] == 'booking_confirmed'
    assert r2['data']['reference'].startswith('demo_pay_')

def test_agent_my_bookings(rec):
    c, p, login = rec
    # Query user bookings
    r = p('ai-agent/chat', {'message': 'show my tickets'}).json
    assert r['action'] == 'show_bookings'
    assert 'confirmed' in r['data']
    assert 'pending' in r['data']

def test_agent_faq(rec):
    c, p, login = rec
    r = p('ai-agent/chat', {'message': 'what is the demo payment OTP?'}).json
    assert '0000' in r['text']
