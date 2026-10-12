> Latest revision: see `CLIENT_REVISION.md` and `RAZORPAY_SETUP.md`. Tickets now require the owning organizer's approval, and booking requests reserve seats immediately. Razorpay integration is included but needs your server-side keys and webhook configuration.

# EventAI client-requested update

This update modifies the existing Flask application; it does not rebuild it or change payment integrations. This update is included in the complete EventAI ZIP.

## Included changes

1. One persistent in-app organizer notification after each approved event ends. A background worker checks every 30 seconds while the server runs. Requests also reconcile missed completions after a restart. These are not email, SMS or device push notifications.
2. JPEG/PNG cover uploads in event creation/editing, with preview, replacement and removal. Images are re-encoded, resized and stored in SQLite. Covers appear in discovery, detail views and management lists. Existing events retain labelled placeholders until a real photo is uploaded.
3. Admin editing of any upcoming event and organizer editing of owned upcoming events. Organizer edits return an event to pending approval; admin edits preserve its approval status. Ticket holders receive an in-app change notice. Capacity cannot fall below confirmed bookings and prices cannot change after bookings exist.
4. Separate role-specific navigation and landing screens: User Discover, Organizer Studio and Admin Control Room. Server-side permissions continue to protect privileged APIs.
5. Start time, duration in minutes, calculated end time and explicit IST display. For example, 11 AM + 180 minutes ends at 2 PM. Cross-midnight events are supported.
6. Completed events are excluded from public discovery. Ongoing events are labelled and cannot be booked. Booked-ticket history and organizer/admin history are retained. Discovery refreshes every 30 seconds while visible and no dialog is open.
7. Required confirmation-password field, checked in both frontend and backend.
8. Persistent “Account successfully created!” banner before email verification. Registration is not described as completed email verification.
9. Dark plum surfaces, light-purple actions, lavender accents and rounded mobile navigation.
10. Screenshot-inspired search, tabs, card hierarchy and mobile navigation, retaining EventAI branding rather than copying other apps' branding.

## Apply the individual files

Back up the existing project and its `instance` folder before replacing files. Keep your own `.env`, credentials and database; none are included with this update.

- Root: replace `app.py`; add `client_updates.py`.
- `templates`: replace `index.html`.
- `static`: replace `app.js`, `manifest.json`, `eventai-theme.css`; add `client-updates.js`. Keep `style.css` and `eventai-logo.jpg` from the preceding theme update.
- `tests`: replace `test_flows.py`; add `conftest.py` and `test_client_updates.py`.

Restart using the existing `python app.py` command. The first startup adds a duration column and the cover/completion tables without deleting existing records. Legacy events default to 180 minutes; review their durations before opening the server to users. Ended approved legacy events receive a completion notice on the first lifecycle check.

All stored event times are interpreted as IST. If earlier records were entered in another timezone, correct those records before deployment. Upcoming events can be edited through the application. Started/completed events are intentionally read-only and require an explicitly reviewed administrative correction if historical data is wrong.

## Verification

11 integration tests passed. Both JavaScript files passed syntax checks. Database migration was tested on a temporary copy of the uploaded database and preserved all existing user, event, booking and post counts. Desktop/mobile browser visual QA could not be completed because the Chromium download failed in this environment.

Run `python -m pytest tests -q`. Tests disable outbound SMTP and the completion worker and use a temporary database. Production throttling is unchanged. `EVENTAI_WORKER=0` disables background reconciliation; request-time reconciliation remains active. Run with the worker enabled for unattended in-app completion notices. An offline server cannot issue notifications until it restarts.

Real payments, trained ML and native push are outside this update and remain unimplemented. The original uploaded ZIP is unchanged.
