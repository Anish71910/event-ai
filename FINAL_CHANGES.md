# SmartEvents / EventAI — final implementation update

Updated 23 September 2026. This is the existing Flask + SQLite + HTML/CSS/JavaScript application. The hybrid recommendation engine is retained. No React, Django, MySQL or external AI service has been introduced.

## Booking and approval flow

Register → verify email → sign in → browse/search/filter → recommendations → choose seats or general-admission quantity → submit free/demo request or Razorpay checkout → server verifies capture → owning organizer approves → confirmed booking, unique QR and rewards → queued email delivery → My orders/history.

Admin approves organizers and event publication. The event's organizer approves tickets, as required in the earlier client changes. Successful payment alone does not bypass organizer review. Free bookings do not need a payment. Real paid bookings cannot bypass Razorpay using the demo endpoint when demo payments are disabled.

## Seat map

On an event's Edit screen, open **Configure seating plan**. Add section names, unique row labels, seat counts, whole-rupee prices and an optional aisle after a seat number. Save before publication. The plan sets capacity and minimum/starting event price. It supports up to 60 rows, 60 seats per row and 2,000 seats total. Customers can select up to 10 named seats per booking. The seat picker shows the stage, rows, aisles, prices, available/selected/held/booked states.

General admission remains available for events without a plan. The plan cannot change after the first booking/payment request, including a failed or expired provider order; this protects existing financial references and ticket history. Event description, location, timing and images remain editable within the existing permission/timing rules. Cancelled events are read-only.

Seat prices are calculated on the server from the selected seat IDs. Client amounts are ignored. SQLite write transactions prevent double selection of the same seat. Booking requests hold seats immediately; a click in the picker alone does not reserve them. Confirmation converts the same claims to booked seats, without double deduction. Rejection, cancellation or expiry releases claims. The server rejects a stale selection if another customer obtained it first. Unpaid Razorpay holds last 15 minutes; captured payments hold until the event starts while awaiting organizer approval. Demo/free holds last up to 24 hours or until the start, whichever is earlier.

## QR security and admission

Open **QR scanner** from the organizer or admin dashboard. Select the event, then use the camera, upload a QR image or paste the token. Camera access requires HTTPS or localhost. The bundled jsQR decoder runs locally; uploaded scanner images are not sent to a third-party scanner.

Backend checks authenticated role, approved organizer ownership, the selected event, current event/booking status, expiry and prior use. Updating the used flag is atomic. It distinguishes Valid, Already Used, Invalid and Cancelled. Wrong-event, unpublished, ended or cancelled events cannot admit guests. Admission attempts are rate limited and audited with token hashes rather than raw QR secrets. One scan admits the entire booking party; this is one QR per booking, not a separate independently redeemable QR per guest.

The ticket screen includes section/row/seat labels, booking details, QR download and print. Customers cannot retrieve another user's ticket. Cancelled tickets no longer expose a valid QR token through the ticket details endpoint.

## Email and scheduled reminders

The existing OTP workflow remains. New transactional outbox messages cover:

- Booking confirmation after organizer approval.
- Captured Razorpay payment confirmation; simulated payments are explicitly labeled DEMO.
- QR ticket email with an attached PNG.
- 24-hour reminder and an optional 2-hour reminder (`REMINDER_2H=1`, enabled by default).
- Confirmed-booking cancellation and pending-request cancellation/rejection/expiry.

Messages are queued in the same database transaction as the state change. Failed SMTP delivery does not undo a successful payment or create another booking. A worker checks every 30 seconds, leases jobs, retries with backoff and marks persistent failures after five attempts. Admin → Email delivery shows attempts, failures, retry actions and the worker's last successful cycle. No SMTP configuration means no claimed email delivery.

Reminder keys are unique by booking, interval and event start time. They are queued for eligible bookings made before that reminder's due time; a late booking receives its confirmation and next applicable reminder rather than a misleading old reminder. Used/cancelled tickets, ended events and stale reminders for a previous event time are suppressed. Late worker recovery can send the applicable upcoming reminder, but never a reminder after the event starts.

Database deduplication and leases prevent ordinary duplicate scheduling/concurrent sends. SMTP cannot provide exactly-once delivery across a crash after the mail server accepts a message but before the database records success; a retry can duplicate that ambiguous delivery. Stable Message-IDs assist downstream deduplication. This limitation is documented rather than hidden.

## Cancellations and payments

Attendees can cancel unused confirmed tickets before the event starts and cancel pending reservations. Organizers can cancel their own future events; admins can cancel platform events. Cancellation releases seats, invalidates QR codes and reverses rewards once. Captured real payments become **refund required**. The administrator initiates the refund in Razorpay, then refreshes provider status or receives the signed refund webhook. Cancellation is not displayed as a completed refund.

Checkout dismissal requests cancellation; a later capture is flagged for refund and cannot issue a ticket. Failed client callbacks trigger server-side provider reconciliation. Server order amounts, HMAC signatures, provider-fetched payment amounts/currency/order IDs, capture state, ownership and idempotency keys are checked. Duplicate callbacks/webhooks do not issue duplicate bookings. Ambiguous order-creation timeouts remain visible for reconciliation rather than automatically charging again.

## User, organizer and admin completion

Profiles, interests, email/password changes, OTP, orders, rewards, notifications, reviews, community and media remain connected. Search supports category, city/device proximity, date and starting-price range. Recommendations appear before the general event listing for signed-in users.

Organizer dashboards expose booking/attendee details, seats, check-in state, revenue and event performance. Admin can enable/disable user accounts, approve/revoke organizer approval, approve/reject events, monitor bookings and demo/real payments, inspect email delivery and evaluate recommendations. Account changes revoke existing sessions; revoking organizer approval returns its upcoming published events to pending review.

## Analytics, maps and recommendations

All statistics query stored records: users, organizers, events, confirmed bookings/revenue, popular events, category popularity, monthly bookings, revenue trends, user growth and organizer/event performance. Legacy users without a signup timestamp are counted but explicitly excluded from historical growth. Demo/test records are actual development records and must not be presented as live business revenue.

Leaflet 1.9.4 is bundled locally; map tiles come from OpenStreetMap with attribution. Maps show filtered event markers, the user's permitted device location, straight-line distance and event links. Existing 50 km location discovery is retained. The tile service requires internet access; it is not an offline map or a guaranteed production SLA. No reverse geocoder is used to invent a city from coordinates.

TF-IDF, cosine similarity, user-neighbour collaborative filtering, weighted interactions, 30-day half-life decay, the hybrid ranker, cold-start fallback and consent remain. Explanations describe content/history, similar-user signals or local popularity as applicable. Offline admin evaluation reports Precision@K, Recall@K and Hit Rate@K using a temporal last-distinct-booking holdout. Training activity after each held-out timestamp and the target user's held-out-event signals are excluded. At least two distinct confirmed bookings per opted-in user are required. No eligible data yields an explicit unavailable result, not fabricated percentages. Current metadata/preferences and incomplete historical availability are documented evaluation limits.

## Validation and remaining release gates

See TEST_REPORT.md for executed checks. Tests use temporary databases, mocked payment/SMTP providers and no real charges or email sends. Browser rendering, camera hardware, SMTP deliverability and actual Razorpay test/live checkout remain deployment checks. No cloud destination or deployment credentials were supplied; no public deployment has been performed. DEPLOYMENT.md includes worker/web services, HTTPS proxy configuration and setup steps.

The supplied screenshot guided the functional seat layout. It was not used as a static image pretending to be a working seat selector.
