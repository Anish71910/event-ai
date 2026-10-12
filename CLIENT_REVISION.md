# Client revisions — September 2026

This revision supersedes earlier documents that described administrator ticket approval or instant free tickets.

| Client request | Implemented behavior |
| --- | --- |
| Keep expired events only in history | Completed events are excluded from discovery and AI candidates. Attendee booking history and organizer/admin event history preserve records. |
| Community for admin and organizer | Community appears in both role navigation bars. Existing audience and connection privacy rules apply. |
| Organizer approves tickets | Only the owning organizer can approve/reject ticket requests. This includes free bookings, simulated payments and verified Razorpay payments. Other organizers and administrators are denied by the backend. |
| Admin approves events | Event publication remains an administrator action. Ticket approval controls were removed from Admin. Existing organizer-account approval and payment-account reconciliation remain available. |
| Event-change notices for attendees | Updates and publication changes notify both confirmed attendees and users with pending requests. The Updates page is available in all role navigations. Notifications are in-app, not email or push. |
| Reduce available seats on booking request | Shared transactional seat holds reduce discovery, detail and recommendation availability immediately. Organizer approval converts the hold to a booking without deducting seats twice. Rejection/verified payment failure releases the hold; unpaid checkout expiry releases availability automatically. |
| Graphs | Role-scoped SVG bar charts show confirmed tickets by category, monthly confirmed revenue, current seat holds, and attendee rating counts. No invented chart data. |
| Smaller approval sections | Compact rows, smaller action buttons and collapsed history sections replace large approval cards. |
| AI integration | Existing TF-IDF content recommendations, user-neighbor collaborative filtering and hybrid ranking remain active. The UI now clearly identifies AI suggestions and explains the recommendation signals. Personalization remains opt-in. The organizer planning estimate is still explicitly rule-based; it is not presented as trained AI or a guaranteed forecast. |
| Categories | Image-led category browsing for Music, Technology, Food, Art, Outdoors and Business, with actual active-event counts. |
| User reviews | Confirmed bookers can submit or update one 1–5 star review per completed event. Reviews appear in event details and history. Unbooked users and pre-event reviews are blocked; displayed text is escaped. |

Seat holds reserve inventory, not reward points. Reward balance and event eligibility are rechecked at approval. Existing pending requests are migrated into holds in creation order where capacity permits; requests that cannot fit are flagged rather than overbooking the event.

`RAZORPAY_SETUP.md` explains real gateway configuration, hold durations, webhook events, organizer approval, late payments and manual refunds. No live payment was made during development. Browser visual QA remains outstanding because a browser executable could not be downloaded in this environment.
