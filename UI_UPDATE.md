> Latest revision: see `CLIENT_REVISION.md` and `RAZORPAY_SETUP.md`. Tickets now require the owning organizer's approval, and booking requests reserve seats immediately. Razorpay integration is included but needs your server-side keys and webhook configuration.

# Interface and imagery update

- Shared dark plum and light lavender themes; header toggle remembers the choice on that browser.
- Original EventAI branding throughout, image-led discovery hero and six local category illustrations.
- Event cards show real event titles, venues, prices, IST start/end times and duration. Expired events stay out of discovery.
- Detail panels have larger covers, organized timing/venue/host/availability information, optional organizer galleries and an explicit checkout total.
- Organizer cover preview and six-photo gallery manager; JPEG/PNG uploads validated server-side. Organizer gallery changes require admin review, like other event edits.
- Separate admin, organizer and attendee navigation; booking history, rewards, QR tickets, community, notifications, account verification and recommendation privacy controls remain available.
- Organizer payment queue and customer approval tracking. See DEMO_PAYMENTS.md.
- Mobile card grid and bottom navigation, keyboard focus styles, reduced-motion support and programmatic form-label associations.

The frontend remains dependency-free JavaScript with the existing feature modules. `experience.js` and `experience.css` integrate this update after the core modules, preserving existing behavior. Inline event handlers remain; Content Security Policy reflects that limitation. It is not a React rewrite.

## Visual review status

Real browser screenshots could not be produced in this build environment because the Chromium download timed out. No mock screenshot is presented as a rendered application. Before public deployment, review at 390px, 768px and 1440px widths, including all role dashboards, both themes, long titles, upload errors, empty states, dialog focus/scrolling and the complete organizer ticket approval flow.
