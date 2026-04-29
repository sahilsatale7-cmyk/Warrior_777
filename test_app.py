from app import app

with app.test_client() as client:
    r = client.get('/')
    print('Homepage status:', r.status_code)
    r = client.get('/rooms')
    print('Rooms page status:', r.status_code)
    r = client.get('/admin/login')
    print('Admin login status:', r.status_code)
    print('All pages working!')
