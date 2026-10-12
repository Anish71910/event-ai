# Seat maps for every event

Events without an organizer layout now generate a Standard numbered map from their saved capacity and price when first opened. Existing custom layouts are preserved. Rows show in theatre order, with row A closest to the stage at the bottom, numbered seats, an aisle, price-section headings and available/selected/unavailable states. The default has up to 20 seats per row; all default seats retain the event price. Organizers can customize sections and prices before the first booking request.

User flow: View event → Book tickets / select seats → choose seats → Continue to payment → organizer approval → QR ticket. Seat IDs and authoritative prices are validated by the backend. Existing general-admission confirmed bookings and active holds are assigned seats in order during conversion, preserving occupied capacity. Earlier issued general-admission tickets should be reopened to see newly assigned seats; physical venue layouts should be configured by the organizer before sales.

Validation: 72 pytest tests passed, including conversion of existing bookings and holds, duplicate-seat rejection and missing-seat rejection once the map exists. DOM + real Flask smoke passed through automatic layout, selected-seat demo payment, organizer confirmation and ticket seat labels. JavaScript syntax check passed. No real charge or live device test was performed.

Update your application code/templates/static files, preserve .env and the existing database/instance folder, then restart the application and refresh the browser. Asset URLs are versioned to refresh cached scripts.
