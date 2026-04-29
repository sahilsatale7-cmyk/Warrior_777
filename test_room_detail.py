from app import app, rooms_collection

# Test template parsing
with app.app_context():
    tmpl = app.jinja_env.get_template('room_detail.html')
    print('room_detail.html template parses OK!')

# Test actual route by finding a room ID
room = rooms_collection.find_one({})
if room:
    print(f'Found room: {room["name"]} (ID: {room["_id"]})')
    with app.test_client() as client:
        resp = client.get(f'/room/{room["_id"]}')
        print(f'Route /room/{room["_id"]} status: {resp.status_code}')
        if resp.status_code == 200:
            print('SUCCESS: Room detail page renders without TemplateSyntaxError!')
        else:
            print(f'Response: {resp.data[:300]}')
else:
    print('No rooms found in database')
