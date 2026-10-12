# Demo checkout and organizer ticket approval

For real Razorpay payments, read RAZORPAY_SETUP.md. This file describes PAYMENT_PROVIDER=demo only. The simulator never charges money or collects payment credentials.

1. Sign in as user@smartevents.demo (password Smart@2026). Choose an upcoming event and ticket quantity.
2. For paid events, select a simulated payment method and submit. Free events submit directly without a payment screen.
3. Seats are reserved immediately. The attendee sees Awaiting organizer, with no QR ticket or reward credit yet.
4. Sign in as organizer@smartevents.demo. In Studio, approve or reject ticket requests for your own events. Administrators and unrelated organizers cannot approve tickets.
5. Approval converts the seat hold into a confirmed booking and issues a QR ticket, applying rewards exactly once. Rejection releases the seats and notifies the attendee.

Free and demo requests expire after 24 hours or at event start, whichever is earlier. Availability excludes expired holds automatically. Points are checked at approval and are not reserved. Completed events and settled requests remain in history.

APIs: POST /api/book, GET /api/payments, GET /api/organizer/payments, POST /api/organizer/payments/review. All writes require CSRF protection. Paid demo requests are blocked when Razorpay is selected or demo mode is off; free organizer-reviewed bookings remain available.

Existing users, bookings and historical requests are preserved. Legacy pending requests receive holds in creation order where capacity permits. The shared inventory view prevents new requests from taking already confirmed or held seats.
