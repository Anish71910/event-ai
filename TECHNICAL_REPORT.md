# Implemented technology and architecture

## Stack

| Area | Implementation |
| --- | --- |
| Frontend | HTML template, CSS, vanilla JavaScript; responsive purple light/dark themes |
| Backend | Python Flask with modular route installers |
| Database | SQLite with foreign keys, WAL and immediate transactions for competing writes |
| Authentication | Server-side session identity/version, Werkzeug password hashes, OTP email verification, CSRF |
| Recommendations | Python sparse TF-IDF, normalized cosine similarity, user-neighbour collaborative filtering, weighted interactions and decay, hybrid ranking and fallback |
| Payment | Server-side Razorpay Orders/verification/webhooks through Python standard-library HTTPS; clearly separated demo/free flow |
| Ticketing | Unique booking tokens, Python qrcode PNG generation, local jsQR decoding and backend event-scoped validation |
| Email | SMTP over STARTTLS, SQLite transactional outbox, leased delivery, retries and scheduled reminders |
| Maps | Locally bundled Leaflet with attributed OpenStreetMap raster tiles |
| Location | Optional browser geolocation, haversine/straight-line distance and nearby search |
| Media | Pillow cover/gallery processing; FFmpeg/ffprobe for bounded short-video normalization |
| Serving | Waitress behind an HTTPS reverse proxy; separate notification worker for production |

## Modules

- `app.py`: application setup, sessions, registration/OTP, discovery, dashboard, QR compatibility, community and installer composition.
- `client_updates.py`, `client_features.py`, `profile_media.py`: lifecycle, organizer forms, images, categories, analytics, reviews, profiles and clips.
- `inventory.py`, `seating.py`: quantity inventory, transactional payment holds, named seats, price sections and claims.
- `demo_payments.py`, `razorpay_gateway.py`: isolated demo/free and real-provider payment state machines, verification and organizer confirmation.
- `operations.py`: secure event admission, cancellations, account operations, ticket details and expanded reports.
- `notifications.py`, `notification_worker.py`: outbox triggers, content/QR attachment construction, retry processing and reminders.
- `recommendation_engine.py`, `recommendations.py`, `evaluation.py`: existing ranker, privacy/interactions and offline temporal evaluation.
- `production.py`, `run_production.py`, `release_check.py`: configuration guards, input/security headers and production serving/checks.

## Database behavior

Existing customer/event/booking records are preserved by additive migrations. New data includes `event_seats`, `seat_claims`, `email_outbox`, worker heartbeat, admission/admin audit and user signup/disabled fields. Payment records retain selected seat IDs. Confirmed bookings, active holds and available capacity remain distinct; a seat cannot be both newly reserved by another payment and already held/booked. No payment confirmation is trusted solely from a browser callback.

Booking and QR messages are enqueued in the booking transaction. A worker processes them independently of browser traffic. Email failures are recorded and retried rather than rolling back financial state. Cancellation invalidates the ticket and restores seat availability; a captured-payment refund is a separate provider-confirmed transition.

## Recommendation methodology

Event terms are weighted by field and converted to TF-IDF vectors. Interest/location/history/search profiles use cosine similarity. Collaborative scores use similar users with overlap shrinkage. Signals retain their original view/like/booking weights, consent boundaries and 30-day half-life decay. The existing hybrid chooses applicable content/collaborative/popularity weights and diversifies leading categories. Scores are ranking values, not success probabilities.

Evaluation uses a temporal last-distinct-booking holdout and the same ranker. Precision@K divides hits by K, Recall@K divides hits by held-out relevant items, and Hit Rate@K averages whether each eligible user has a hit. Results are reported only when suitable consented history exists. Current metadata/preferences and unavailable historical inventory limit interpretation. The organizer planning estimate remains explicitly rule-based and low-confidence; it is not presented as a trained success-prediction model.

## Operational status

The code and repeatable local tests are supplied. No claim is made of public cloud deployment, live provider payment/email validation, hardware camera QA or certified performance/security. See TEST_REPORT.md, FINAL_CHANGES.md and DEPLOYMENT.md. This report reflects the implemented stack; no old synopsis was used to justify rebuilding into another framework. No source PPT or editable synopsis was supplied in this update, so those separate artifacts were not rewritten.
