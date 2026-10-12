# Deployment and operations

## Required environment

Python 3.11+ (tested with 3.12), persistent local storage for SQLite, HTTPS reverse proxy, SMTP credentials, and a Razorpay account for real paid bookings. FFmpeg and ffprobe enable event/community video uploads. Use a single host with local SQLite storage; do not place the database on a shared network filesystem or scale this deployment across unrelated hosts.

No live deployment, mail delivery or real provider payment is claimed in this package. A server/domain and operator-controlled credentials are required. Do not paste secrets into frontend JavaScript or commit `.env`.

## Windows local demonstration

From the extracted EventAI folder:

```bat
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.lock.txt
python app.py
```

Open http://127.0.0.1:5000. Local default mode creates clearly labeled demonstration users/events and simulated payments. The web process runs reminders while it remains open. Configure SMTP in `.env` to send actual messages. For a separate worker set `EMAIL_WORKER=0` for the web app and run `python notification_worker.py` in another terminal. Windows Task Scheduler may instead run `python notification_worker.py --once` every minute, with its working directory set to EventAI and non-overlapping executions. Monitor Admin → Email delivery.

## Linux VPS / ServerByt-style server

1. Back up the old application and database. Provision a dedicated `eventai` service account. Place code in `/opt/eventai`, the writable database directory in `/var/lib/eventai`, and create `/opt/eventai/instance`. Give the service account ownership of the two writable directories. Code should be read-only to the service account after installation.
2. Create `/opt/eventai/.venv` and install `requirements.lock.txt`. Install system FFmpeg/ffprobe when video is required. Confirm they are on the service account's PATH.
3. Copy `.env.production.example` to `/etc/eventai.env`, restrict permissions to the service account, and replace every example value. Set `APP_ENV=production`, `DEMO_MODE=0`, `HTTPS_ONLY=1`, explicit `TRUSTED_HOSTS`, a random secret of at least 32 characters, and `DATABASE=/var/lib/eventai/events.db`. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and optionally SMTP_FROM. Set EMAIL_MESSAGE_DOMAIN to your own domain and REMINDER_2H as desired. Secrets must be compatible with systemd EnvironmentFile syntax; do not shell-source an untrusted file.
4. Initialize a **fresh production database**, without demonstration accounts. Run `create_admin.py` as the service account with the environment loaded by your process manager, or temporarily use a protected `/opt/eventai/.env` containing the same settings. The script prompts for the administrator's email/password. Do not copy the development demo database into production.
5. Run `python release_check.py` with the same environment. Resolve configuration errors. Passing it is not security certification or an actual payment/email test.
6. Install `deploy/eventai-web.service` and `deploy/eventai-email.service` into `/etc/systemd/system`. Review paths/account/environment before enabling them. The web service runs Waitress on localhost. The separate email service continuously sends/retries queued messages and schedules reminders; restart policies cover process failures.
7. Configure the domain's DNS and valid TLS certificate. Adapt `deploy/nginx.conf.example`, replace its domain/certificate paths, validate nginx configuration and enable HTTPS. Allow 21 MB request bodies and 120-second proxy timeouts for video processing. Expose HTTPS, not the Flask development server.
8. Enable/start both services and inspect their logs. Confirm `/healthz` responds, sign in using the real admin account and check the email worker heartbeat. Never create a public demo-admin password.
9. Complete the deployment checks below before inviting real customers.

Example service activation, after setup and review:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now eventai-web eventai-email
sudo systemctl status eventai-web eventai-email
```

Behind this proxy the application does not blindly trust arbitrary forwarded IP headers. Its existing IP-based throttles may conservatively group clients through the proxy; review trusted-proxy handling for your exact topology before changing this behavior.

## Razorpay activation

Read RAZORPAY_SETUP.md. Start in a private staging configuration using `rzp_test_...` credentials and `PAYMENT_PROVIDER=razorpay`. Production mode deliberately rejects test keys and demo accounts; use a separate staging database and environment. Verify order creation, success/failure/dismissal, seat holds, organizer approval, signed webhook recovery, cancellation and refund processing using your Razorpay account. Then use live keys only with `APP_ENV=production` and `DEMO_MODE=0`.

Configure `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and a separate `RAZORPAY_WEBHOOK_SECRET` on the server. Register the HTTPS `/api/razorpay/webhook` endpoint for the events listed in RAZORPAY_SETUP.md. Use automatic capture or ensure authorized payments are captured by your provider setup; authorization alone never issues a ticket. Secret/API credentials are never returned to the browser; the public checkout key is intentionally public.

Refunds are initiated in the Razorpay dashboard and reconciled by signed webhook or Refresh provider status. Maintain an operational refund process; a cancelled booking is not a completed refund.

## Mail and reminder verification

- Confirm the SMTP account permits authenticated TLS sending; use an app password if required by that provider.
- Verify sender-domain SPF/DKIM/DMARC with your mail provider.
- Make a controlled booking and organizer approval; confirm payment, booking and attached QR emails in the intended test mailbox.
- Schedule controlled future events far enough in advance to exercise both reminders. Confirm each interval has one outbox record and cancellation suppresses future reminders.
- Stop SMTP temporarily, verify retries/failed status, restore it and retry from admin. Stop/restart the worker and confirm its heartbeat returns.
- A service restart is safe for database scheduling; SMTP's acceptance/commit ambiguity can still cause an occasional duplicate send. See FINAL_CHANGES.md.

## Map and browser checks

Maps require access to `https://tile.openstreetmap.org`. Attribution remains visible and the browser sends a normal origin referrer. Use an appropriate tile service if your production traffic exceeds the public service policy; no bulk/offline tile downloading is implemented.

Check desktop and physical Android/iOS screens in both themes. Test keyboard navigation, dialogs, price/date filters, scrolling seat rows, selection conflicts, QR image upload, camera permission denial/success and scanner feedback. Camera/device location needs HTTPS outside localhost. Verify checkout popup/CSP behavior on the actual deployed origin.

## Backups and monitoring

Run `python backup_database.py --output-dir /your/private/backup/directory` regularly; the database includes uploaded images, clips, seats, bookings and the email outbox. Keep encrypted off-host backups, limit retention of sensitive customer data and test restoration. Watch failed email jobs, stale worker heartbeat, refund-required/creating payments, available disk space and database backup success. Do not log raw QR tokens, SMTP credentials or payment secrets.

SQLite write transactions protect reservations, but representative load testing remains necessary. Video encoding currently runs in web workers with size/time limits; move it to a dedicated bounded queue/object storage before high-volume video use. Database storage, SMTP quotas, tile-service capacity, refunds and operational monitoring need real deployment ownership.

Sources: https://razorpay.com/docs/server-integration/python/test-app/ ; https://leafletjs.com/examples/quick-start/ ; https://operations.osmfoundation.org/policies/tiles/
