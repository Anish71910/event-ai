# Focused security review

This document records implementation safeguards and tested cases, not an independent penetration-test certificate.

## Implemented and exercised

- Session-based authentication with password hashes, session-version revocation, HttpOnly/SameSite cookies, production Secure cookies, CSRF for browser mutations, and existing login/OTP throttles.
- Role and event-owner authorization for management, media, ticket approval and admission. Admin cannot approve an organizer's tickets through the organizer payment-review routes.
- Event-specific QR validation, random ticket secrets, atomic one-time use, cancelled/ended-event rejection, owner-only ticket retrieval and admission auditing using a hash of the token.
- Current UI retrieves QR images by booking ID so the secret is not embedded in normal QR-image request URLs. The older owner-authenticated token URL remains for compatibility; avoid logging raw legacy QR URLs or sharing them.
- SQLite immediate transactions for reservations and admission. Server-owned prices, bounded quantities, integer validation, request idempotency and unique payment/order identifiers.
- Signed Razorpay raw webhooks and provider-fetched state. The webhook's exact path is exempt from browser CSRF/form parsing because it authenticates the raw payload separately; no organizer/admin endpoint shares this exception.
- Named-seat ownership, plan locking, concurrency control and immutable financial references. Late capture after cancellation requires refund reconciliation, never an automatic new ticket.
- Verified email changes, current-password validation, bounded OTP attempts and revoked other sessions after credential changes.
- Durable transactional outbox, unique reminder keys, worker leases, cancellation/stale reminder suppression and controlled retries. SMTP's acceptance-versus-commit ambiguity is documented.
- Bounded media upload validation/re-encoding and private community-media access.
- Parameterized SQL for user data and escaped user content in the UI/map popups.
- Production startup rejects demo accounts, missing SMTP/security settings and unsuitable payment configuration; private environment files and databases are excluded from the package.

## Residual risks / deployment work

- The inherited UI uses inline event handlers/styles. Its CSP therefore retains `unsafe-inline`; it is not a strict CSP. Removing that allowance requires a dedicated event-listener/style refactor and browser regression testing.
- SQLite and synchronous video encoding suit a modest single-host deployment. Perform load tests, enforce disk/backup retention, and use a bounded background encoding service before heavy traffic.
- Real SMTP delivery, actual gateway checkout/webhooks/refunds, HTTPS/proxy configuration and physical browser/camera behavior require destination-specific testing.
- The attempted Python dependency advisory audit timed out. Run it successfully on a network-enabled release host; also review bundled Leaflet 1.9.4 and jsQR 1.4.0 advisories. Their license files are included.
- Signed webhook IDs are retained for 30 days. Durable payment state, unique IDs and idempotent transitions continue to prevent repeated booking issuance after that window.
- Ordinary email duplicates are prevented by scheduling keys and leases, but strict exactly-once SMTP delivery is not technically guaranteed across a crash after server acceptance.
- Reward reversal may leave a negative balance when previously earned points have already been spent. New redemption is blocked until a nonnegative balance is available; required booking/payment history is preserved.
- Refunds require administrator operations in Razorpay and provider confirmation. Staff must monitor unresolved and refund-required payments.
- The current account role model does not add MFA or device/session inventory. No claim is made that these exist.

Run the included tests and operational checks after configuration changes. Keep source/dependency/runtime patches current and commission an independent review for a public paid service.
