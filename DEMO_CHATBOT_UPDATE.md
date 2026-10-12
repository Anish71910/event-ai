# Client update — 29 September 2026

## Demo checkout
Select seats, then choose Demo card, Demo UPI QR or Demo net banking. Card fields accept a fictional name and the displayed test values: 4242 4242 4242 4242, expiry 12/30, CVV 123. Details remain in the browser and are discarded when the OTP screen opens. Enter 0000 to simulate payment. The backend verifies this for the card flow and the new demo checkout. No money moves and no OTP SMS is sent. The account/email OTP mechanism is unchanged. Live Razorpay remains separate. The QR encodes demo text, not a payable UPI address.

Payment success means a simulated payment request was accepted; the organizer still approves the ticket. Seats, idempotency, notifications, QR ticket issuance and history use the existing application flow.

## Event assistant
Open the floating Event assistant button after signing in. Ask, for example:
- Relaxed events in Davangere under 500 tomorrow
- Music events after 6 pm
- Events this weekend in Bengaluru
- What is the price of [exact event title]?
- Where is [exact event title]?

Optional mood, city, budget and date fields disambiguate free text. Dates and times use IST. Near me uses the profile city, not unrequested device GPS. Requests are independent; filters remain in the open form. Results contain up to three approved upcoming events with inventory. There is no fallback that silently drops a specified city/date/budget. A missing match is reported directly. Exact price, venue and time questions return only those details plus links to events.

The small multinomial Naive Bayes mood model is actually trained from ten curated phrase examples across five labels in assistant_features.py. Its saved token counts/vocabulary are in mood_model.json. Retrain with `python assistant_features.py`. It is a local intent/mood classifier plus deterministic database retrieval, not an LLM or a production-scale language model. No accuracy percentage or broad language understanding is claimed. Existing hybrid recommendations are preserved. Add representative labelled examples before expecting broad phrasing/language coverage.

## Notifications
Unread count badges appear on the account button, Updates navigation and the profile Notifications link. Polling every 15 seconds adds a toast when a new notice arrives while the page is active. Opening Updates marks the notices fetched at that point as read. Read state is stored per user in SQLite and survives refresh/login.

## Update and validation
Preserve .env and instance/database when copying the updated code. Restart the app and refresh. No new runtime packages are needed. Model JSON and assistant_features.py must be deployed with the code.

76 pytest tests passed, covering new chatbot filters/classifier, per-user unread state, demo OTP rejection/success/replay and demo QR gating. DOM + real Flask smoke passed through seat selection, demo card fields, OTP 0000, organizer approval, QR ticket, chatbot price answer and marking notifications read. JavaScript syntax was checked. SMTP and real payment providers were not contacted. Physical mobile/browser visual testing was not performed.
