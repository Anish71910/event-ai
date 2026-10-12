# Seat selection and analytics colour fix — 24 September 2026

- Every analytics bar chart now uses multiple colours, including category, monthly revenue, seat holds, ratings, bookings, popular events and user growth. Summary cards and donut share the same palette, with light/dark mode text accents.
- Assigned-seat events now follow Book tickets → Select seats → Continue to payment. Payment stays disabled until seats are selected; general-admission events retain quantity booking.
- Fixed an unconditional crypto.randomUUID call that could abort setup over non-localhost HTTP and leave the previous payment handler active. A secure random fallback now supports that environment, and the previous submit handler is disabled immediately during setup.
- Updated asset version URLs to refresh cached scripts/styles.

Validation: 71 pytest tests passed. The DOM + real Flask flow passed with crypto.randomUUID disabled, including seat-step gating, paid demo checkout, organizer approval, historical seat labels and multi-colour bars. Real payment was not charged.

Update: back up your current project and database, then replace application code, templates and static files from this package. Keep your existing .env and instance folder/database. Restart Flask/Waitress and refresh the browser. Configure the event seating plan before its first booking, then approve the event again if the organizer's edit has returned it to pending review. Events without a saved seat plan use general admission.
