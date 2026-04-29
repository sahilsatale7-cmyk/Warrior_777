# TODO: Fix KeyError and Convert Currency to INR

## Step 1: Fix KeyError in admin_guests (app.py)
- [x] Use `.get('room_id')` in `admin_guests()` route
- [x] Use `.get('booking_id')` safely

## Step 2: Fix currency in public templates
- [x] `templates/index.html` — room price tags
- [x] `templates/rooms.html` — price tags, placeholders, labels
- [x] `templates/room_detail.html` — price display
- [x] `templates/booking.html` — total price
- [x] `templates/my_bookings.html` — booking total

## Step 3: Fix currency in admin templates
- [x] `templates/admin/dashboard.html` — stats, table, Chart.js
- [x] `templates/admin/bookings.html` — total price column
- [x] `templates/admin/booking_detail.html` — total/base price
- [x] `templates/admin/calendar.html` — room price, daily cells
- [x] `templates/admin/reports.html` — stats, table, Chart.js
- [x] `templates/admin/rooms.html` — table, form labels
- [x] `templates/admin/settings.html` — total revenue
- [x] `templates/admin/guests.html` — safe handling + no currency

## Step 4: Test
- [ ] Restart Flask app
- [ ] Verify /admin/guests loads without error
- [ ] Spot-check ₹ symbols across site

