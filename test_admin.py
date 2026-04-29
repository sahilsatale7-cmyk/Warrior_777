from app import app

client = app.test_client()

# Test admin login page
r = client.get('/admin/login')
print('Admin login:', r.status_code)

# Test admin dashboard (should redirect to login without session)
r = client.get('/admin/dashboard', follow_redirects=True)
print('Admin dashboard (no auth):', r.status_code)

# Login as admin
r = client.post('/admin/login', data={'username': 'admin', 'password': 'admin123'}, follow_redirects=True)
print('Admin login post:', r.status_code)

# Test admin dashboard
r = client.get('/admin/dashboard')
print('Admin dashboard (authed):', r.status_code)

# Test new routes
routes = ['/admin/rooms', '/admin/bookings', '/admin/guests', '/admin/seasonal-pricing', '/admin/reports', '/admin/calendar', '/admin/settings']
for route in routes:
    r = client.get(route)
    print(route, r.status_code)

# Test booking detail with first booking
from app import bookings_collection
booking = bookings_collection.find_one()
if booking:
    r = client.get(f'/admin/booking/{booking["_id"]}')
    print('Booking detail:', r.status_code)

print('All tests completed!')

