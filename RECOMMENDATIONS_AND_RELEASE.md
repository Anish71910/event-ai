> Latest revision: see `CLIENT_REVISION.md` and `RAZORPAY_SETUP.md`. Tickets now require the owning organizer's approval, and booking requests reserve seats immediately. Razorpay integration is included but needs your server-side keys and webhook configuration.

# EventAI hybrid recommendations and release status

## What is implemented

- Content-based matching: sparse TF-IDF vectors with smoothed inverse document frequency and cosine similarity. Event category, title, city, venue and description are weighted. The user profile combines declared interests/city, recent viewed/liked/booked event vectors and sanitized recent search terms.
- Collaborative filtering: user-based nearest neighbours on an implicit user/event matrix, using cosine similarity and overlap shrinkage. Up to 30 similar users contribute. This is a real memory-based collaborative algorithm, not a hardcoded score.
- Hybrid scoring: 60% content, 30% collaborative, 10% community popularity when both signals exist. With just one personalized signal, that signal receives 85%, popularity 15%. With no usable signals, discovery sorts by popularity, then date/id. Early results are diversified by category. Scores are not success probabilities.
- Interaction weights: view 1, current like 4, confirmed booking 8; capped at 12 per user/event, decaying with a 30-day half-life. Searches shape only the content profile. Repeated views are deduplicated daily and their collaborative contribution is capped; likes and bookings use authoritative state so repeated clicks do not multiply their weights.
- “Picked for you” cards, explanation labels, likes, personalization settings, clear-history controls and admin aggregate metrics.

No external analytics service, embedding API or third-party model receives the data. No new ML runtime dependency is needed. This implementation uses the algorithms directly in Python; it does not claim to use scikit-learn or a trained neural model.

## Eligibility and privacy

Only approved, not-yet-started, available events are recommended. Already-confirmed bookings are excluded from the recommendation candidates. City/category filters are applied before ranking. Ticket history remains accessible.

Views/searches are tracked only after an authenticated user opts in. Bookings remain operational records regardless of consent, but only confirmed bookings made after opting in contribute to personalized ranking. Likes work as saved choices without consent; they are not used for recommendation training while consent is off. Organizer/admin behaviour does not contribute to the collaborative community matrix.

Turning off personalization deletes tracked interactions and excludes the account from collaborative training. Saved likes and required booking records remain. Clearing history deletes interactions and likes and sets a booking-ID cutoff so earlier purchases cannot reappear in the learning history; it does not delete operational bookings. Declared profile interests remain, so content recommendations can still reflect those interests when personalization stays enabled.

Ranking only reads the latest 90 days. Physical interaction cleanup runs on startup and hourly with `EVENTAI_WORKER=1`; offline servers cannot clean up until restarted. If the worker is disabled, run `python -m flask --app app prune-interactions` daily. Likes remain until unliked/cleared; booking records have a separate business retention policy. Backups may still contain earlier records and need a documented retention/deletion policy.

Search text is capped at 120 characters; obvious email addresses, long digit sequences and URLs are removed. This is not perfect personal-data detection. Do not solicit sensitive information in search, and publish a reviewed privacy notice before public launch. User IDs and signals never come from a caller-supplied user field. Admin metrics expose aggregate counts, not individual search histories.

## API map

| Endpoint | Purpose |
|---|---|
| GET `/api/recommendations?limit=6&city=Davangere` | Ranked candidates, explanations and model/fallback metadata; signed-in users only |
| GET/POST `/api/recommendation-settings` | Read or change own opt-in; POST `{ "enabled": true }` |
| POST `/api/interactions` | Own view/search only; e.g. `{ "kind": "view", "event": 100 }` |
| GET/POST `/api/likes` | Own saved event IDs or idempotent `{ "event": 100, "liked": true }` |
| POST `/api/recommendation-history/clear` | Clear own learning history and likes |
| GET `/api/admin/recommendation-stats` | Admin-only aggregate counts |
| GET `/healthz` | Basic database liveness; not a business-readiness check |

All mutations require the existing CSRF token and authentication. The browser cannot submit a booking interaction. The backend inserts it in the successful booking transaction. Client activity, likes and recommendation refreshes are rate limited. Public event listing accepts `limit` (max 100) and `offset`, with a bounded 2,000-event search pool. The current UI displays the first result page.

## Production hardening added

- Fail-closed production configuration: demo mode prohibited, explicit random secret, secure cookies, explicit trusted hosts, SMTP settings and an absolute persistent database path required.
- Production startup rejects the supplied demonstration accounts. The original shared secret/SMTP credential must be rotated; no credential rotation is performed by this update.
- Eight-hour authenticated sessions, session/CSRF rotation on login, revoked sessions rejected even by the session endpoint.
- Atomic OTP use and attempt limits, serialized rate counters, JSON/schema guards, bounded input lengths and consistent API errors.
- Booking retry protection: the UI sends a per-checkout `request_id`; one key/user can create only one booking. Reusing it with different details is rejected. Production requires this key. This is not payment-provider idempotency.
- SQLite WAL, query indexes, bounded recommendation pools, HSTS in production, host checks, security headers and request IDs. CSP still permits legacy inline handlers/styles; it is not a strict nonce-based CSP.
- Aggregate timing logs do not include user IDs, search text or credentials.
- Pinned tested dependencies, read-only `release_check.py` and WAL-safe `backup_database.py`.

## Files to apply to the existing project

Keep the preceding EventAI theme/client update files. This update requires:

- Root: replace `app.py`, `client_updates.py`, `README.md`, `TEST_REPORT.md`; add `recommendation_engine.py`, `recommendations.py`, `production.py`, `release_check.py`, `backup_database.py`, `requirements.lock.txt`, `.env.production.example` and `.gitignore`.
- `static`: replace `app.js`, `client-updates.js`, `eventai-theme.css`; add `recommendations.js`.
- `templates`: replace `index.html`.
- `tests`: replace `conftest.py` and `test_flows.py`; add `test_recommendations.py`. Keep `test_client_updates.py`.

Back up the database first. Restarting automatically adds recommendation tables and indexes without deleting account or booking records. Startup cleanup removes expired OTPs, expired throttle entries and interaction logs older than 90 days. The complete EventAI ZIP includes these changes; the original uploaded ZIP is unchanged.

## Configuration and launch procedure

1. Use a supported Python runtime compatible with the pinned packages. Install with `python -m pip install -r requirements.lock.txt`. Run `python -m pytest tests -q` in a non-production test environment.
2. Provision HTTPS and persistent **local** database storage on one server. SQLite WAL is not a multi-host/network-filesystem database. Choose your hosting platform before finalizing reverse-proxy and process-manager configuration.
3. Copy `.env.production.example` to a private server-side `.env`, supplying a fresh random secret, rotated SMTP app password, real trusted hostname and persistent database path. Generate a secret using `python -c "import secrets; print(secrets.token_hex(32))"`. Never paste generated secrets into public logs or source control.
4. Start with a clean database (`DEMO_MODE=0` before initialization), then run `python create_admin.py`. Keep the supplied demonstration database backed up privately, not published. Do not delete business data simply to pass a deployment check; migrate reviewed non-demo records separately if needed.
5. Run `python release_check.py`. This is a configuration check, not proof of production readiness.
6. Behind the HTTPS reverse proxy, launch the included WSGI server: `waitress-serve --host=127.0.0.1 --port=5000 app:app`. Bind the reverse proxy to the public interface. Do not expose Flask's development server or trust arbitrary forwarded headers.
7. Keep `EVENTAI_WORKER=1` for completion notices and hourly retention cleanup. The server must remain running. If using multiple worker processes, completion IDs prevent duplicate notices, but SQLite throughput must be measured for that workload.
8. Back up using `python backup_database.py --output-dir /secure/eventai-backups` (use a real writable protected path). Schedule backups through your hosting tools, encrypt off-server copies, and test restoration separately. No scheduled task is created here.

## Not yet public-launch ready

The recommendation features and listed safeguards are implemented; the complete platform is **not certified production-ready**. Remaining gates:

- Validate the implemented Razorpay checkout, signed webhooks, retry handling and reconciliation with your own Test Mode keys before live use. Refund initiation is manual in Razorpay; self-service cancellation is not implemented.
- Verify delivery using your rotated SMTP credentials, including failure/retry handling. No external email was sent during this update.
- Actual HTTPS deployment, backup/restore drill, monitoring and incident procedures.
- Desktop/mobile rendered UI and end-to-end browser verification. Chromium was unavailable in this environment.
- Representative load tests, dependency vulnerability review and an independent security review. Pinned dependencies alone are not a security audit.
- Evaluate recommendations using real, consented data (time-based holdout, Recall@K/NDCG@K, coverage and cold-start cohorts). Passing synthetic tests does not establish recommendation quality or uplift. There is no fabricated accuracy percentage.
- Review recommendation/privacy notices and account/data retention requirements with your business owner. Add account deletion/export workflows before promising those controls to users.

Capacity bounds: recommendations inspect at most 2,000 eligible candidates and the 50,000 most recent bounded input signals, plus up to 500 own historical events. These bounds prevent unbounded work but can omit useful older signals/events. Move to an offline-trained/precomputed service and a server database if measured traffic/catalogue size exceeds this single-server design. No scale target can be guaranteed without a target workload and measurements.

## Verification

23 integration/unit tests pass, including content/collaborative mathematics, hybrid fallback, consent, deduplication, signal spoofing rejection, like ownership, candidate filtering, retention, history clearing, session revocation, configuration guards and concurrent idempotent bookings. Existing client-update tests continue to pass. JavaScript syntax checks pass. Visual/browser and live infrastructure checks remain outstanding.
