# Start here

1. Extract the ZIP and open the `EventAI` folder.
2. Follow README.md to run locally. Double-click `start_windows.bat` on Windows if preferred.
3. Read FINAL_CHANGES.md for seat selection, QR security, email/reminders, cancellations and the latest client changes.
4. Configure SMTP and FFmpeg when testing mail/video. Local demo payments are explicitly simulated.
5. Read RAZORPAY_SETUP.md before enabling real/test Razorpay checkout.
6. For hosting, follow DEPLOYMENT.md and use a clean production database, HTTPS and the dedicated notification service.

The existing Flask + SQLite architecture and hybrid recommendation engine are retained. Event publication belongs to admin; ticket approval belongs to the owning organizer.

TEST_REPORT.md records 71 passing automated tests and the executed DOM/API smoke flow. Live account credentials, physical browser/device checks and deployment are still required before public launch. No private customer data or credentials are packaged.
