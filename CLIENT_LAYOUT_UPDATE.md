# EventAI client layout update — 19 September 2026

This revision updates the existing Flask application and preserves organizer-only ticket approval, admin event approval, immediate seat holds, Razorpay integration, reviews and hybrid recommendations.

| Client point | Implemented behavior |
| --- | --- |
| 1. Custom categories | Event creation/editing accepts a suggested category or a custom name. Published active custom categories appear in Explore. |
| 2. Current location | Header location control supports a city or device geolocation. Device coordinates are shown in the location dialog; nearby search uses a 50 km radius. Permission is requested on click. |
| 3. Portrait posters | Discovery and recommendation posters display at 9:16. Existing landscape images are cropped visually; original uploads remain intact. Event details show the full image. |
| 4 and 9. Top search beside profile | Shared header contains event search, location, profile and theme controls. Search works from other sections too. |
| 5. Profile options | Account menu links to My orders, Rewards, Notifications and Edit profile. Name, city and interests can be updated. Password changes require the current password. Email changes require the current password and a code sent to the new address; other sessions are revoked after credential changes. |
| 6. Compact stats and pie chart | Compact statistics share a row with an accessible donut chart of actual confirmed tickets by category. Empty datasets explicitly show no confirmed bookings. |
| 7. Two-column management | Admin organizer approvals and current events are side by side. Organizer ticket approvals and current events use the same two-column layout. They stack on narrow screens. |
| 8. More room for posts | The community feed receives the main column; the friends panel is a narrow sidebar. |
| 10 and 11. Event clips | Organizers can add/replace clips during event creation/editing. Users can attach clips to eligible event memories, then replace/remove their own clips. Playback includes controls and does not autoplay. |
| 12. Events by date | Inclusive start-date range filters are applied server-side before pagination. Standard discovery is chronological; device-location discovery prioritizes proximity. |
| 13. Publish-memory popup | “Publish new memory” opens a dialog instead of occupying the feed. The community sidebar is a compact phone-shaped panel. |
| 14. Explore reference | Explore has a wide featured-event banner, category tiles, portrait event cards and a shared header, retaining EventAI's dark/light purple styling. Reference screenshots informed the layout; commercial movie artwork was not copied. |

## Video setup

Install **FFmpeg and ffprobe**, including an H.264 encoder, on the application server and make both commands available on its PATH. Verify `ffmpeg -version` and `ffprobe -version` from the same account running EventAI. Python requirements alone do not install these system programs. Restart the app after installation. `/api/media-capabilities` reports availability.

Accepted input: MP4 or WebM, at most 60 seconds, under 20 MB and no larger than 4K. The server probes and re-encodes uploads to H.264/AAC MP4, strips metadata, limits output dimensions to 720 pixels and supports HTTP byte-range playback. Filenames and client MIME types are not trusted. Decode/encode timeouts and per-user upload throttling are enforced. Uploads require authentication, CSRF tokens and ownership. Changing an organizer's event clip returns the event to pending admin review. Memory clip access follows the post's audience and accepted connections.

Allow multipart requests of at least **21 MB** at your reverse proxy for clip endpoints and an upstream timeout above 105 seconds. Other JSON requests retain the existing 5 MB limit. Clips are stored in the persistent SQLite database and included in database backups. Processing occurs in web workers: review capacity and move encoding to a separate queue/object storage before scaling to heavy video traffic.

If FFmpeg is unavailable, the application remains usable and reports that video upload needs server setup. No placeholder video is presented as a working uploaded clip. A memory or event is saved before its optional clip is processed; a failed clip upload reports this clearly and can be retried without creating another record.

## Location and email

Browser geolocation normally requires HTTPS (localhost is also supported). Users can always enter a city if permission is denied. No reverse-geocoding provider is integrated; device location is shown as coordinates rather than an invented city name. Coordinates are used for the discovery request and are not saved to the account.

Real email changes require working SMTP settings. Development mode can display a verification code when SMTP is unset, explicitly labeled as development-only. Production mode does not substitute a fake email delivery success.

## Updating an existing installation

Back up your current code and database first. Replace application code/assets without replacing your private `.env` or `instance` data. Startup creates the new `clips` and `email_changes` tables additively. Install the locked Python requirements and the video prerequisites, restart, then hard-refresh the browser. No customer database or private credentials are included in this ZIP.

Read START_HERE.md and RAZORPAY_SETUP.md before deployment. Live payment and email configuration remains server-specific. This revision has automated API tests and JavaScript syntax checks; browser/mobile visual QA and live deployment verification remain outstanding.
