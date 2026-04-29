from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask.json.provider import DefaultJSONProvider
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import bcrypt
import os
import json
from functools import wraps

class MongoJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles MongoDB ObjectId and datetime."""
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)

class MongoJSONProvider(DefaultJSONProvider):
    """Custom JSON provider using MongoJSONEncoder."""
    def dumps(self, obj, **kwargs):
        kwargs.setdefault("default", MongoJSONEncoder().default)
        return json.dumps(obj, **kwargs)

app = Flask(__name__)
app.json_provider_class = MongoJSONProvider
app.json = MongoJSONProvider(app)
app.config.from_object('config.Config')

# ==================== IN-MEMORY FALLBACK ====================
class InMemoryCursor:
    """Cursor-like wrapper for in-memory results to support method chaining"""
    def __init__(self, results):
        self._results = results
    
    def limit(self, n):
        self._results = self._results[:n]
        return self
    
    def sort(self, *args):
        if len(args) == 1 and isinstance(args[0], list):
            for key, direction in reversed(args[0]):
                self._results.sort(key=lambda x: x.get(key, ''), reverse=(direction < 0))
        elif len(args) == 2:
            self._results.sort(key=lambda x: x.get(args[0], ''), reverse=(args[1] < 0))
        return self
    
    def __iter__(self):
        return iter(self._results)
    
    def __len__(self):
        return len(self._results)
    
    def __getitem__(self, index):
        return self._results[index]

class InMemoryCollection:
    """In-memory collection fallback when MongoDB is unavailable"""
    def __init__(self, name):
        self.name = name
        self._data = []
        self._id_counter = 1
    
    def _make_id(self):
        oid = ObjectId()
        return oid
    
    def find_one(self, query=None, **kwargs):
        query = query or {}
        for item in self._data:
            if self._matches(item, query):
                return dict(item)
        return None
    
    def find(self, query=None, **kwargs):
        query = query or {}
        results = []
        for item in self._data:
            if self._matches(item, query):
                results.append(dict(item))
        
        cursor = InMemoryCursor(results)
        
        if 'sort' in kwargs:
            sort_spec = kwargs['sort']
            if isinstance(sort_spec, list):
                cursor.sort(sort_spec)
            else:
                cursor.sort(sort_spec[0], sort_spec[1])
        
        if 'limit' in kwargs:
            cursor.limit(kwargs['limit'])
        
        return cursor
    
    def insert_one(self, document):
        doc = dict(document)
        if '_id' not in doc:
            doc['_id'] = self._make_id()
        self._data.append(doc)
        class Result:
            def __init__(self, inserted_id): self.inserted_id = inserted_id
        return Result(doc['_id'])
    
    def insert_many(self, documents):
        ids = []
        for doc in documents:
            result = self.insert_one(doc)
            ids.append(result.inserted_id)
        class Result:
            def __init__(self, inserted_ids): self.inserted_ids = inserted_ids
        return Result(ids)
    
    def update_one(self, query, update):
        for item in self._data:
            if self._matches(item, query):
                if '$set' in update:
                    item.update(update['$set'])
                return type('Result', (), {'modified_count': 1})()
        return type('Result', (), {'modified_count': 0})()
    
    def delete_one(self, query):
        for i, item in enumerate(self._data):
            if self._matches(item, query):
                del self._data[i]
                return type('Result', (), {'deleted_count': 1})()
        return type('Result', (), {'deleted_count': 0})()
    
    def delete_many(self, query):
        original_len = len(self._data)
        self._data = [item for item in self._data if not self._matches(item, query)]
        return type('Result', (), {'deleted_count': original_len - len(self._data)})()
    
    def count_documents(self, query=None):
        query = query or {}
        return sum(1 for item in self._data if self._matches(item, query))
    
    def distinct(self, field, query=None):
        query = query or {}
        results = set()
        for item in self._data:
            if self._matches(item, query) and field in item:
                val = item[field]
                if isinstance(val, list):
                    results.update(val)
                else:
                    results.add(val)
        return list(results)
    
    def _matches(self, item, query):
        if not query:
            return True
        for key, condition in query.items():
            if key == '$or':
                if not any(self._matches(item, c) for c in condition):
                    return False
                continue
            if key not in item:
                if key == '_id' and '_id' in item:
                    str_id = str(item['_id'])
                    if isinstance(condition, dict):
                        for op, opval in condition.items():
                            if op == '$ne':
                                if str_id == str(opval): return False
                    elif str_id != str(condition):
                        return False
                    continue
                return False
            val = item[key]
            if isinstance(condition, dict):
                for op, opval in condition.items():
                    if op == '$gte':
                        if not (val >= opval): return False
                    elif op == '$lte':
                        if not (val <= opval): return False
                    elif op == '$gt':
                        if not (val > opval): return False
                    elif op == '$lt':
                        if not (val < opval): return False
                    elif op == '$ne':
                        if val == opval: return False
                    elif op == '$in':
                        if val not in opval: return False
                    elif op == '$nin':
                        if val in opval: return False
            elif str(condition) != str(val):
                return False
        return True


# ==================== DATABASE CONNECTION ====================
# Smart fallback: Local MongoDB → Atlas → In-Memory

def connect_mongodb():
    """Try multiple MongoDB connections, return (client, db, source_name) or None"""
    
    # Try 1: Local MongoDB
    local_uri = app.config.get('MONGO_URI', 'mongodb://localhost:27017/hotel_management')
    try:
        client = MongoClient(local_uri, serverSelectionTimeoutMS=2000)
        client.admin.command('ping')
        # Extract DB name from URI, fallback to hotel_management
        try:
            db = client.get_default_database()
        except Exception:
            db_name = local_uri.rstrip('/').split('/')[-1].split('?')[0] or 'hotel_management'
            db = client[db_name]
        print("[OK] Connected to LOCAL MongoDB: {}".format(local_uri))
        return client, db, 'local'
    except Exception as e:
        print("[FAIL] Local MongoDB failed: {}".format(e))
    
    # Try 2: MongoDB Atlas
    atlas_uri = app.config.get('MONGO_ATLAS')
    if atlas_uri:
        try:
            client = MongoClient(
                atlas_uri,
                serverSelectionTimeoutMS=5000,
                tls=True,
                tlsAllowInvalidCertificates=True,
                tlsAllowInvalidHostnames=True
            )
            client.admin.command('ping')
            db = client.get_default_database()
            print("[OK] Connected to ATLAS MongoDB")
            return client, db, 'atlas'
        except Exception as e:
            print("[FAIL] Atlas MongoDB failed: {}".format(e))
    
    return None, None, None

# Attempt connection
connection_result = connect_mongodb()

if connection_result[0] is not None:
    client, db, db_source = connection_result
    rooms_collection = db.rooms
    bookings_collection = db.bookings
    users_collection = db.users
    seasonal_pricing_collection = db.seasonal_pricing
    guests_collection = db.guests
    print("Using database: {} (source: {})".format(db.name, db_source))
else:
    print("[WARN] All MongoDB connections failed. Using IN-MEMORY fallback.")
    print("       Data will NOT persist after restart!")
    print("       To enable persistence, start MongoDB locally or check Atlas credentials.")
    rooms_collection = InMemoryCollection('rooms')
    bookings_collection = InMemoryCollection('bookings')
    users_collection = InMemoryCollection('users')
    seasonal_pricing_collection = InMemoryCollection('seasonal_pricing')
    guests_collection = InMemoryCollection('guests')

# ==================== HELPERS ====================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login first.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin' not in session:
            flash('Admin access required.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def get_seasonal_price(room_id, date):
    """Get seasonal price for a room on a specific date"""
    date_obj = datetime.strptime(date, '%Y-%m-%d') if isinstance(date, str) else date
    month = date_obj.month
    
    seasonal = seasonal_pricing_collection.find_one({
        'room_id': str(room_id),
        'month': month,
        'is_active': True
    })
    
    if seasonal:
        return seasonal['price_multiplier']
    return 1.0

def calculate_total_price(room, check_in, check_out):
    """Calculate total price considering seasonal pricing"""
    check_in_date = datetime.strptime(check_in, '%Y-%m-%d')
    check_out_date = datetime.strptime(check_out, '%Y-%m-%d')
    nights = (check_out_date - check_in_date).days
    
    total = 0
    current = check_in_date
    for _ in range(nights):
        multiplier = get_seasonal_price(room['_id'], current.strftime('%Y-%m-%d'))
        total += room['base_price'] * multiplier
        current += timedelta(days=1)
    
    return round(total, 2), nights

def is_room_available(room_id, check_in, check_out, exclude_booking_id=None):
    """Check if room is available for given dates"""
    check_in_date = datetime.strptime(check_in, '%Y-%m-%d')
    check_out_date = datetime.strptime(check_out, '%Y-%m-%d')
    
    query = {
        'room_id': str(room_id),
        'status': {'$in': ['confirmed', 'pending']},
        '$or': [
            {'check_in': {'$lt': check_out_date.strftime('%Y-%m-%d')}, 
             'check_out': {'$gt': check_in_date.strftime('%Y-%m-%d')}}
        ]
    }
    
    if exclude_booking_id:
        query['_id'] = {'$ne': ObjectId(exclude_booking_id)}
    
    existing = bookings_collection.find_one(query)
    return existing is None

def get_room_availability(room_id, year, month):
    """Get availability calendar for a room"""
    first_day = datetime(year, month, 1)
    last_day = (first_day + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    
    bookings = list(bookings_collection.find({
        'room_id': str(room_id),
        'status': {'$in': ['confirmed', 'pending']},
        '$or': [
            {'check_in': {'$gte': first_day.strftime('%Y-%m-%d'), '$lte': last_day.strftime('%Y-%m-%d')}},
            {'check_out': {'$gte': first_day.strftime('%Y-%m-%d'), '$lte': last_day.strftime('%Y-%m-%d')}},
            {'check_in': {'$lte': first_day.strftime('%Y-%m-%d')}, 
             'check_out': {'$gte': last_day.strftime('%Y-%m-%d')}}
        ]
    }))
    
    unavailable_dates = set()
    for booking in bookings:
        ci = datetime.strptime(booking['check_in'], '%Y-%m-%d')
        co = datetime.strptime(booking['check_out'], '%Y-%m-%d')
        current = ci
        while current < co:
            unavailable_dates.add(current.strftime('%Y-%m-%d'))
            current += timedelta(days=1)
    
    return unavailable_dates

# ==================== USER ROUTES ====================

@app.route('/')
def index():
    featured_rooms = list(rooms_collection.find({'is_active': True}).limit(6))
    stats = {
        'total_rooms': rooms_collection.count_documents({'is_active': True}),
        'total_bookings': bookings_collection.count_documents({'status': 'confirmed'}),
        'happy_guests': guests_collection.count_documents({})
    }
    return render_template('index.html', rooms=featured_rooms, stats=stats)

@app.route('/rooms')
def rooms():
    room_type = request.args.get('type', '')
    min_price = request.args.get('min_price', '')
    max_price = request.args.get('max_price', '')
    guests = request.args.get('guests', '')
    
    query = {'is_active': True}
    if room_type:
        query['room_type'] = room_type
    if guests:
        query['max_guests'] = {'$gte': int(guests)}
    if min_price or max_price:
        query['base_price'] = {}
        if min_price:
            query['base_price']['$gte'] = float(min_price)
        if max_price:
            query['base_price']['$lte'] = float(max_price)
    
    rooms_list = list(rooms_collection.find(query))
    room_types = rooms_collection.distinct('room_type', {'is_active': True})
    
    return render_template('rooms.html', rooms=rooms_list, room_types=room_types, 
                         filters={'type': room_type, 'min_price': min_price, 
                                'max_price': max_price, 'guests': guests})

@app.route('/room/<room_id>')
def room_detail(room_id):
    room = rooms_collection.find_one({'_id': ObjectId(room_id)})
    if not room:
        flash('Room not found.', 'danger')
        return redirect(url_for('rooms'))
    
    # Get current month availability
    now = datetime.now()
    unavailable_dates = get_room_availability(room_id, now.year, now.month)
    
    # Get next month availability too
    next_month = now.month + 1 if now.month < 12 else 1
    next_year = now.year if now.month < 12 else now.year + 1
    unavailable_dates_next = get_room_availability(room_id, next_year, next_month)
    unavailable_dates.update(unavailable_dates_next)
    
    # Get seasonal pricing info
    seasonal = list(seasonal_pricing_collection.find({
        'room_id': room_id,
        'is_active': True
    }).sort('month', 1))
    
    return render_template('room_detail.html', room=room, 
                         unavailable_dates=list(unavailable_dates),
                         seasonal_pricing=seasonal)

@app.route('/book/<room_id>', methods=['GET', 'POST'])
@login_required
def book_room(room_id):
    room = rooms_collection.find_one({'_id': ObjectId(room_id)})
    if not room:
        flash('Room not found.', 'danger')
        return redirect(url_for('rooms'))
    
    if request.method == 'POST':
        check_in = request.form.get('check_in')
        check_out = request.form.get('check_out')
        guests = int(request.form.get('guests', 1))
        special_requests = request.form.get('special_requests', '')
        
        if not check_in or not check_out:
            flash('Please select check-in and check-out dates.', 'warning')
            return redirect(url_for('book_room', room_id=room_id))
        
        # Validate mandatory guest info
        id_type = request.form.get('id_type', '').strip()
        id_number = request.form.get('id_number', '').strip()
        location = request.form.get('location', '').strip()
        guest_phone = request.form.get('guest_phone', '').strip()
        
        if not id_type or not id_number:
            flash('ID Type and ID Number are mandatory.', 'warning')
            return redirect(url_for('book_room', room_id=room_id))
        
        if not location:
            flash('Location / Country is mandatory.', 'warning')
            return redirect(url_for('book_room', room_id=room_id))
        
        if not guest_phone:
            flash('Phone number is mandatory.', 'warning')
            return redirect(url_for('book_room', room_id=room_id))
        
        if not is_room_available(room_id, check_in, check_out):
            flash('Room is not available for selected dates.', 'danger')
            return redirect(url_for('room_detail', room_id=room_id))
        
        total_price, nights = calculate_total_price(room, check_in, check_out)
        
        # Payment details
        advance_paid = float(request.form.get('advance_paid', 0) or 0)
        remaining_balance = max(0, total_price - advance_paid)
        payment_method = request.form.get('payment_method', '')
        
        if advance_paid >= total_price:
            payment_status = 'paid'
        elif advance_paid > 0:
            payment_status = 'partial'
        else:
            payment_status = 'pending'
        
        booking = {
            'room_id': str(room_id),
            'user_id': session['user_id'],
            'guest_name': request.form.get('guest_name', session.get('user_name', '')),
            'guest_email': request.form.get('guest_email', session.get('user_email', '')),
            'guest_phone': guest_phone,
            'id_type': id_type,
            'id_number': id_number,
            'location': location,
            'check_in': check_in,
            'check_out': check_out,
            'nights': nights,
            'guests': guests,
            'total_price': total_price,
            'advance_paid': advance_paid,
            'remaining_balance': remaining_balance,
            'payment_method': payment_method,
            'special_requests': special_requests,
            'status': 'confirmed',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'payment_status': payment_status,
            # Store room details snapshot at time of booking
            'room_name': room.get('name', ''),
            'room_type': room.get('room_type', ''),
            'room_image': room.get('images', [''])[0] if room.get('images') else '',
            'room_base_price': room.get('base_price', 0),
            'room_bed_type': room.get('bed_type', ''),
            'room_size': room.get('size', ''),
            'room_amenities': room.get('amenities', [])
        }
        
        result = bookings_collection.insert_one(booking)
        
        # Add to guests collection with room details and identification
        guest_data = {
            'booking_id': str(result.inserted_id),
            'name': booking['guest_name'],
            'email': booking['guest_email'],
            'phone': booking['guest_phone'],
            'id_type': booking['id_type'],
            'id_number': booking['id_number'],
            'location': booking['location'],
            'check_in': check_in,
            'check_out': check_out,
            'room_id': str(room_id),
            'room_name': room.get('name', ''),
            'room_type': room.get('room_type', ''),
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        guests_collection.insert_one(guest_data)
        
        flash('Booking request submitted successfully! Awaiting confirmation.', 'success')
        return redirect(url_for('my_bookings'))
    
    check_in = request.args.get('check_in', '')
    check_out = request.args.get('check_out', '')
    
    total_price = 0
    nights = 0
    if check_in and check_out:
        total_price, nights = calculate_total_price(room, check_in, check_out)
    
    return render_template('booking.html', room=room, check_in=check_in, 
                         check_out=check_out, total_price=total_price, nights=nights)

@app.route('/my-bookings')
@login_required
def my_bookings():
    bookings = list(bookings_collection.find({
        'user_id': session['user_id']
    }).sort('created_at', -1))
    
    for booking in bookings:
        room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
        booking['room'] = room
    
    return render_template('my_bookings.html', bookings=bookings)

@app.route('/cancel-booking/<booking_id>', methods=['POST'])
@login_required
def cancel_booking(booking_id):
    booking = bookings_collection.find_one({
        '_id': ObjectId(booking_id),
        'user_id': session['user_id']
    })
    
    if not booking:
        flash('Booking not found.', 'danger')
        return redirect(url_for('my_bookings'))
    
    bookings_collection.update_one(
        {'_id': ObjectId(booking_id)},
        {'$set': {'status': 'cancelled', 'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')}}
    )
    
    flash('Booking cancelled successfully.', 'success')
    return redirect(url_for('my_bookings'))

# ==================== AUTH ROUTES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        if not email or not password:
            flash('Please enter both email and password.', 'warning')
            return render_template('login.html')
        
        user = users_collection.find_one({'email': email})
        if user:
            try:
                stored_pw = user['password']
                # Normalize stored password to bytes (MongoDB may store as Binary or string)
                if isinstance(stored_pw, str):
                    stored_pw = stored_pw.encode('utf-8')
                elif hasattr(stored_pw, 'encode'):
                    stored_pw = stored_pw.encode('utf-8')
                elif not isinstance(stored_pw, bytes):
                    stored_pw = bytes(stored_pw)
                
                if bcrypt.checkpw(password.encode('utf-8'), stored_pw):
                    session.clear()  # Clear any previous session (including admin keys)
                    session['user_id'] = str(user['_id'])
                    session['user_name'] = user['name']
                    session['user_email'] = user['email']
                    flash('Login successful!', 'success')
                    return redirect(url_for('index'))
            except Exception as e:
                print(f"[LOGIN ERROR] Password check failed for {email}: {e}")
        
        flash('Invalid email or password.', 'danger')
    
    return render_template('login.html')

@app.route('/register', methods=['POST'])
def register():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    phone = request.form.get('phone', '').strip()
    
    if not name or not email or not password:
        flash('Please fill in all required fields.', 'warning')
        return redirect(url_for('login'))
    
    if len(password) < 4:
        flash('Password must be at least 4 characters long.', 'warning')
        return redirect(url_for('login'))
    
    # Check for existing user (case-insensitive)
    if users_collection.find_one({'email': email}):
        flash('Email already registered.', 'warning')
        return redirect(url_for('login'))
    
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    
    user = {
        'name': name,
        'email': email,
        'password': hashed_password,
        'phone': phone,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    users_collection.insert_one(user)
    flash('Registration successful! Please login.', 'success')
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

# ==================== ADMIN ROUTES ====================

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username == app.config['ADMIN_USERNAME'] and password == app.config['ADMIN_PASSWORD']:
            session.clear()  # Clear any previous session (including user keys)
            session['admin'] = True
            flash('Admin login successful!', 'success')
            return redirect(url_for('admin_dashboard'))
        else:
            flash('Invalid admin credentials.', 'danger')
    
    return render_template('admin/login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    flash('Admin logged out.', 'info')
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    stats = {
        'active_rooms': rooms_collection.count_documents({'is_active': True}),
        'pending_bookings': bookings_collection.count_documents({'status': 'pending'}),
        'confirmed_bookings': bookings_collection.count_documents({'status': 'confirmed'}),
        'total_guests': guests_collection.count_documents({}),
        'total_revenue': sum(
            b.get('total_price', 0)
            for b in bookings_collection.find({'status': 'confirmed'})
        ),
        'occupancy_rate': get_occupancy_rate()
    }

    recent_bookings = list(bookings_collection.find().sort('created_at', -1).limit(10))
    for booking in recent_bookings:
        room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
        booking['room'] = room

    return render_template('admin/dashboard.html',
                         stats=stats,
                         recent_bookings=recent_bookings,
                         revenue_trend=get_revenue_trend(),
                         checkins_today=get_today_bookings('check_in'),
                         checkouts_today=get_today_bookings('check_out'))

@app.route('/admin/rooms')
@admin_required
def admin_rooms():
    rooms_list = list(rooms_collection.find())
    return render_template('admin/rooms.html', rooms=rooms_list)

@app.route('/admin/rooms/add', methods=['POST'])
@admin_required
def add_room():
    room = {
        'name': request.form.get('name'),
        'room_type': request.form.get('room_type'),
        'description': request.form.get('description'),
        'base_price': float(request.form.get('base_price', 0)),
        'max_guests': int(request.form.get('max_guests', 1)),
        'bed_type': request.form.get('bed_type'),
        'size': request.form.get('size', ''),
        'amenities': [a.strip() for a in request.form.get('amenities', '').split(',') if a.strip()],
        'images': [i.strip() for i in request.form.get('images', '').split(',') if i.strip()],
        'is_active': True,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    rooms_collection.insert_one(room)
    flash('Room added successfully!', 'success')
    return redirect(url_for('admin_rooms'))

@app.route('/admin/rooms/edit/<room_id>', methods=['POST'])
@admin_required
def edit_room(room_id):
    rooms_collection.update_one(
        {'_id': ObjectId(room_id)},
        {'$set': {
            'name': request.form.get('name'),
            'room_type': request.form.get('room_type'),
            'description': request.form.get('description'),
            'base_price': float(request.form.get('base_price', 0)),
            'max_guests': int(request.form.get('max_guests', 1)),
            'bed_type': request.form.get('bed_type'),
            'size': request.form.get('size', ''),
            'amenities': [a.strip() for a in request.form.get('amenities', '').split(',') if a.strip()],
            'images': [i.strip() for i in request.form.get('images', '').split(',') if i.strip()],
            'is_active': request.form.get('is_active') == 'on',
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }}
    )
    flash('Room updated successfully!', 'success')
    return redirect(url_for('admin_rooms'))

@app.route('/admin/rooms/delete/<room_id>')
@admin_required
def delete_room(room_id):
    rooms_collection.delete_one({'_id': ObjectId(room_id)})
    bookings_collection.delete_many({'room_id': room_id})
    flash('Room deleted successfully!', 'success')
    return redirect(url_for('admin_rooms'))

@app.route('/admin/bookings')
@admin_required
def admin_bookings():
    import calendar as cal_module
    status_filter = request.args.get('status', '')
    query = {}
    if status_filter:
        query['status'] = status_filter
    
    bookings_list = list(bookings_collection.find(query).sort('created_at', -1))
    for booking in bookings_list:
        room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
        booking['room'] = room
    
    # Calendar data for availability view
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))
    
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    month_name = datetime(year, month, 1).strftime('%B %Y')
    
    # Build calendar grid
    cal = cal_module.Calendar(firstweekday=6)  # Sunday first
    calendar_days = []
    today_str = datetime.now().strftime('%Y-%m-%d')
    
    # Get all bookings for this month
    first_day = datetime(year, month, 1)
    last_day = (first_day + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    
    month_bookings = list(bookings_collection.find({
        'status': {'$in': ['confirmed', 'pending']},
        'check_in': {'$lte': last_day.strftime('%Y-%m-%d')},
        'check_out': {'$gte': first_day.strftime('%Y-%m-%d')}
    }))
    
    # Build date → bookings mapping
    date_bookings = {}
    for bk in month_bookings:
        ci = datetime.strptime(bk['check_in'], '%Y-%m-%d')
        co = datetime.strptime(bk['check_out'], '%Y-%m-%d')
        current = max(ci, first_day)
        end = min(co, last_day + timedelta(days=1))
        while current < end:
            ds = current.strftime('%Y-%m-%d')
            if ds not in date_bookings:
                date_bookings[ds] = []
            room = rooms_collection.find_one({'_id': ObjectId(bk['room_id'])})
            room_num = str(bk.get('room_id', ''))[-3:] if bk.get('room_id') else ''
            date_bookings[ds].append({
                'guest': bk.get('guest_name', 'Guest').split()[0],
                'room_num': '#' + room_num,
                'status': bk.get('status', 'pending')
            })
            current += timedelta(days=1)
    
    for week in cal.monthdayscalendar(year, month):
        for day in week:
            if day == 0:
                calendar_days.append({'day': '', 'is_today': False, 'bookings': []})
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                calendar_days.append({
                    'day': day,
                    'is_today': date_str == today_str,
                    'bookings': date_bookings.get(date_str, [])
                })
    
    return render_template('admin/bookings.html', 
                         bookings=bookings_list, 
                         status_filter=status_filter,
                         calendar_days=calendar_days,
                         month_name=month_name,
                         year=year, month=month,
                         prev_year=prev_year, prev_month=prev_month,
                         next_year=next_year, next_month=next_month)

@app.route('/admin/bookings/update/<booking_id>', methods=['POST'])
@admin_required
def update_booking_status(booking_id):
    status = request.form.get('status')
    bookings_collection.update_one(
        {'_id': ObjectId(booking_id)},
        {'$set': {
            'status': status,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }}
    )
    flash('Booking status updated!', 'success')
    return redirect(url_for('admin_bookings'))

@app.route('/admin/bookings/edit/<booking_id>', methods=['POST'])
@admin_required
def edit_booking(booking_id):
    """Edit booking details"""
    update_data = {
        'guest_name': request.form.get('guest_name'),
        'guest_email': request.form.get('guest_email'),
        'guest_phone': request.form.get('guest_phone'),
        'check_in': request.form.get('check_in'),
        'check_out': request.form.get('check_out'),
        'guests': int(request.form.get('guests', 1)),
        'special_requests': request.form.get('special_requests', ''),
        'status': request.form.get('status'),
        'payment_method': request.form.get('payment_method', ''),
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # Recalculate nights and total price
    if update_data['check_in'] and update_data['check_out']:
        booking = bookings_collection.find_one({'_id': ObjectId(booking_id)})
        room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
        if room:
            total_price, nights = calculate_total_price(room, update_data['check_in'], update_data['check_out'])
            update_data['nights'] = nights
            update_data['total_price'] = total_price
    else:
        booking = bookings_collection.find_one({'_id': ObjectId(booking_id)})
        total_price = booking.get('total_price', 0)
    
    # Payment calculations
    advance_paid = float(request.form.get('advance_paid', 0) or 0)
    update_data['advance_paid'] = advance_paid
    update_data['remaining_balance'] = max(0, total_price - advance_paid)
    
    if advance_paid >= total_price:
        update_data['payment_status'] = 'paid'
    elif advance_paid > 0:
        update_data['payment_status'] = 'partial'
    else:
        update_data['payment_status'] = 'pending'
    
    bookings_collection.update_one(
        {'_id': ObjectId(booking_id)},
        {'$set': update_data}
    )
    flash('Booking updated successfully!', 'success')
    return redirect(url_for('admin_booking_detail', booking_id=booking_id))

@app.route('/admin/guests')
@admin_required
def admin_guests():
    guests_list = list(guests_collection.find().sort('created_at', -1))
    
    # Deduplicate by email and aggregate booking count
    seen_emails = {}
    unique_guests = []
    for guest in guests_list:
        email = guest.get('email', '')
        if email and email in seen_emails:
            seen_emails[email]['booking_count'] += 1
        else:
            room = rooms_collection.find_one({'_id': ObjectId(guest.get('room_id'))}) if guest.get('room_id') else None
            guest['room'] = room
            booking = bookings_collection.find_one({'_id': ObjectId(guest.get('booking_id'))}) if guest.get('booking_id') else None
            guest['booking'] = booking
            guest['booking_count'] = bookings_collection.count_documents({'guest_email': email}) if email else 1
            guest['member_since'] = guest.get('created_at', '')
            unique_guests.append(guest)
            if email:
                seen_emails[email] = guest
    
    return render_template('admin/guests.html', guests=unique_guests)

@app.route('/admin/guest/<guest_id>')
@admin_required
def admin_guest_detail(guest_id):
    guest = guests_collection.find_one({'_id': ObjectId(guest_id)})
    if not guest:
        flash('Guest not found.', 'danger')
        return redirect(url_for('admin_guests'))
    
    # Get all bookings for this guest
    guest_bookings = list(bookings_collection.find({
        'guest_email': guest.get('email', '')
    }).sort('created_at', -1))
    
    for booking in guest_bookings:
        room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
        booking['room'] = room
    
    return render_template('admin/guest_detail.html', guest=guest, bookings=guest_bookings)


@app.route('/admin/seasonal-pricing')
@admin_required
def admin_seasonal_pricing():
    room_id = request.args.get('room_id', '')
    query = {}
    if room_id:
        query['room_id'] = room_id
    
    pricing = list(seasonal_pricing_collection.find(query).sort('month', 1))
    rooms_list = list(rooms_collection.find({'is_active': True}))
    
    return render_template('admin/seasonal_pricing.html', 
                         pricing=pricing, rooms=rooms_list, selected_room=room_id)

@app.route('/admin/seasonal-pricing/add', methods=['POST'])
@admin_required
def add_seasonal_pricing():
    pricing = {
        'room_id': request.form.get('room_id'),
        'month': int(request.form.get('month')),
        'month_name': request.form.get('month_name'),
        'price_multiplier': float(request.form.get('price_multiplier')),
        'reason': request.form.get('reason', ''),
        'is_active': True,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    # Check if pricing already exists for this room and month
    existing = seasonal_pricing_collection.find_one({
        'room_id': pricing['room_id'],
        'month': pricing['month']
    })
    
    if existing:
        seasonal_pricing_collection.update_one(
            {'_id': existing['_id']},
            {'$set': pricing}
        )
        flash('Seasonal pricing updated!', 'success')
    else:
        seasonal_pricing_collection.insert_one(pricing)
        flash('Seasonal pricing added!', 'success')
    
    return redirect(url_for('admin_seasonal_pricing'))

@app.route('/admin/seasonal-pricing/delete/<pricing_id>')
@admin_required
def delete_seasonal_pricing(pricing_id):
    seasonal_pricing_collection.delete_one({'_id': ObjectId(pricing_id)})
    flash('Seasonal pricing deleted!', 'success')
    return redirect(url_for('admin_seasonal_pricing'))

@app.route('/admin/booking/<booking_id>')
@admin_required
def admin_booking_detail(booking_id):
    booking = bookings_collection.find_one({'_id': ObjectId(booking_id)})
    if not booking:
        flash('Booking not found.', 'danger')
        return redirect(url_for('admin_bookings'))
    room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
    return render_template('admin/booking_detail.html', booking=booking, room=room)

@app.route('/admin/reports')
@admin_required
def admin_reports():
    total_bookings = bookings_collection.count_documents({})
    total_revenue = sum(b.get('total_price', 0) for b in bookings_collection.find({'status': 'confirmed'}))
    confirmed = bookings_collection.count_documents({'status': 'confirmed'})
    avg_value = total_revenue / confirmed if confirmed > 0 else 0

    stats = {
        'total_revenue': total_revenue,
        'total_bookings': total_bookings,
        'avg_occupancy': get_occupancy_rate(),
        'avg_booking_value': avg_value
    }

    return render_template('admin/reports.html',
                         stats=stats,
                         monthly_data=get_monthly_report_data(),
                         room_performance=get_room_performance(),
                         status_breakdown=get_status_breakdown())

@app.route('/admin/calendar')
@admin_required
def admin_calendar():
    room_id = request.args.get('room_id', '')
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))

    rooms_list = list(rooms_collection.find({'is_active': True}))

    # Calculate prev/next month
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    calendar_days = []
    month_bookings = []

    if room_id:
        # Get bookings for this room in this month
        first_day = datetime(year, month, 1)
        last_day = (first_day + timedelta(days=32)).replace(day=1) - timedelta(days=1)

        month_bookings = list(bookings_collection.find({
            'room_id': room_id,
            'status': {'$in': ['confirmed', 'pending']},
            '$or': [
                {'check_in': {'$gte': first_day.strftime('%Y-%m-%d'), '$lte': last_day.strftime('%Y-%m-%d')}},
                {'check_out': {'$gte': first_day.strftime('%Y-%m-%d'), '$lte': last_day.strftime('%Y-%m-%d')}},
                {'check_in': {'$lte': first_day.strftime('%Y-%m-%d')}, 'check_out': {'$gte': last_day.strftime('%Y-%m-%d')}}
            ]
        }))

        # Build unavailable dates set
        unavailable_dates = set()
        for booking in month_bookings:
            ci = datetime.strptime(booking['check_in'], '%Y-%m-%d')
            co = datetime.strptime(booking['check_out'], '%Y-%m-%d')
            current = ci
            while current < co:
                unavailable_dates.add(current.strftime('%Y-%m-%d'))
                current += timedelta(days=1)

        # Get room for pricing
        room = rooms_collection.find_one({'_id': ObjectId(room_id)})
        base_price = room['base_price'] if room else 0

        # Build calendar days
        import calendar
        cal = calendar.Calendar()
        today_str = datetime.now().strftime('%Y-%m-%d')

        for week in cal.monthdayscalendar(year, month):
            for day in week:
                if day == 0:
                    calendar_days.append({'day': '', 'is_booked': False, 'is_past': False, 'price': 0})
                else:
                    date_str = f"{year}-{month:02d}-{day:02d}"
                    is_past = date_str < today_str
                    is_booked = date_str in unavailable_dates
                    multiplier = get_seasonal_price(room_id, date_str)
                    price = base_price * multiplier
                    calendar_days.append({
                        'day': day,
                        'is_booked': is_booked,
                        'is_past': is_past,
                        'price': price
                    })

    month_name = datetime(year, month, 1).strftime('%B %Y')

    return render_template('admin/calendar.html',
                         rooms=rooms_list,
                         selected_room=room_id,
                         year=year,
                         month=month,
                         month_name=month_name,
                         prev_year=prev_year,
                         prev_month=prev_month,
                         next_year=next_year,
                         next_month=next_month,
                         calendar_days=calendar_days,
                         month_bookings=month_bookings,
                         now=datetime.now())

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'change_password':
            current = request.form.get('current_password')
            new_pass = request.form.get('new_password')
            confirm = request.form.get('confirm_password')

            if current != app.config['ADMIN_PASSWORD']:
                flash('Current password is incorrect.', 'danger')
            elif new_pass != confirm:
                flash('New passwords do not match.', 'danger')
            elif len(new_pass) < 6:
                flash('Password must be at least 6 characters.', 'warning')
            else:
                app.config['ADMIN_PASSWORD'] = new_pass
                flash('Admin password updated successfully!', 'success')

        return redirect(url_for('admin_settings'))

    stats = {
        'total_rooms': rooms_collection.count_documents({}),
        'total_bookings': bookings_collection.count_documents({}),
        'total_guests': guests_collection.count_documents({}),
        'total_revenue': sum(b.get('total_price', 0) for b in bookings_collection.find({'status': 'confirmed'}))
    }

    return render_template('admin/settings.html', stats=stats, db_source=globals().get('db_source', 'memory'))

# ==================== API ROUTES ====================

@app.route('/api/check-availability')
def api_check_availability():
    room_id = request.args.get('room_id')
    check_in = request.args.get('check_in')
    check_out = request.args.get('check_out')
    
    if not all([room_id, check_in, check_out]):
        return jsonify({'error': 'Missing parameters'}), 400
    
    available = is_room_available(room_id, check_in, check_out)
    room = rooms_collection.find_one({'_id': ObjectId(room_id)})
    
    if room and available:
        total_price, nights = calculate_total_price(room, check_in, check_out)
        return jsonify({
            'available': True,
            'total_price': total_price,
            'nights': nights,
            'base_price': room['base_price']
        })
    
    return jsonify({'available': False})

@app.route('/api/room-calendar/<room_id>')
def api_room_calendar(room_id):
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month))
    
    unavailable = get_room_availability(room_id, year, month)
    room = rooms_collection.find_one({'_id': ObjectId(room_id)})
    
    # Calculate prices for each day
    days_in_month = (datetime(year, month % 12 + 1, 1) - timedelta(days=1)).day
    prices = {}
    for day in range(1, days_in_month + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        multiplier = get_seasonal_price(room_id, date_str)
        prices[date_str] = round(room['base_price'] * multiplier, 2) if room else 0
    
    return jsonify({
        'unavailable_dates': list(unavailable),
        'prices': prices
    })

# ==================== ADMIN HELPERS ====================

def get_occupancy_rate():
    """Calculate current occupancy rate based on today's bookings"""
    today = datetime.now().strftime('%Y-%m-%d')
    total_rooms = rooms_collection.count_documents({'is_active': True})
    if total_rooms == 0:
        return 0
    
    occupied = bookings_collection.count_documents({
        'status': 'confirmed',
        'check_in': {'$lte': today},
        'check_out': {'$gte': today}
    })
    return round((occupied / total_rooms) * 100)

def get_revenue_trend():
    """Get monthly revenue for the last 6 months"""
    trend = []
    now = datetime.now()
    for i in range(5, -1, -1):
        month_date = now - timedelta(days=i*30)
        month_start = month_date.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        
        month_bookings = list(bookings_collection.find({
            'status': 'confirmed',
            'check_in': {'$gte': month_start.strftime('%Y-%m-%d'), '$lte': month_end.strftime('%Y-%m-%d')}
        }))
        
        revenue = sum(b.get('total_price', 0) for b in month_bookings)
        trend.append({
            'month': month_start.strftime('%b %Y'),
            'revenue': revenue,
            'bookings': len(month_bookings)
        })
    return trend

def get_today_bookings(check_type='check_in'):
    """Get bookings checking in or out today"""
    today = datetime.now().strftime('%Y-%m-%d')
    bookings = list(bookings_collection.find({
        check_type: today,
        'status': {'$in': ['confirmed', 'pending']}
    }).sort('created_at', -1))
    
    for booking in bookings:
        room = rooms_collection.find_one({'_id': ObjectId(booking['room_id'])})
        booking['room'] = room
    return bookings

def get_monthly_report_data():
    """Get detailed monthly report data for the last 12 months"""
    data = []
    now = datetime.now()
    for i in range(11, -1, -1):
        month_date = now - timedelta(days=i*30)
        month_start = month_date.replace(day=1)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        month_str = month_start.strftime('%b %Y')
        
        month_bookings = list(bookings_collection.find({
            'check_in': {'$gte': month_start.strftime('%Y-%m-%d'), '$lte': month_end.strftime('%Y-%m-%d')}
        }))
        
        confirmed = [b for b in month_bookings if b['status'] == 'confirmed']
        cancelled = [b for b in month_bookings if b['status'] == 'cancelled']
        revenue = sum(b.get('total_price', 0) for b in confirmed)
        total_nights = sum(b.get('nights', 0) for b in confirmed)
        avg_night = revenue / total_nights if total_nights > 0 else 0
        
        data.append({
            'month': month_str,
            'bookings': len(month_bookings),
            'confirmed': len(confirmed),
            'cancelled': len(cancelled),
            'revenue': revenue,
            'avg_night': avg_night
        })
    return data

def get_room_performance():
    """Get revenue performance by room"""
    rooms_list = list(rooms_collection.find({'is_active': True}))
    performance = []
    for room in rooms_list:
        room_bookings = list(bookings_collection.find({
            'room_id': str(room['_id']),
            'status': 'confirmed'
        }))
        revenue = sum(b.get('total_price', 0) for b in room_bookings)
        performance.append({
            'name': room['name'],
            'revenue': revenue,
            'bookings': len(room_bookings)
        })
    return sorted(performance, key=lambda x: x['revenue'], reverse=True)[:6]

def get_status_breakdown():
    """Get booking status breakdown"""
    statuses = ['pending', 'confirmed', 'cancelled']
    breakdown = {}
    for status in statuses:
        count = bookings_collection.count_documents({'status': status})
        if count > 0:
            breakdown[status] = count
    return breakdown

# ==================== SEED DATA ====================

def seed_data():
    """Seed initial data if collections are empty"""
    if rooms_collection.count_documents({}) == 0:
        sample_rooms = [
            {
                'name': 'Deluxe Ocean View',
                'room_type': 'Deluxe',
                'description': 'Spacious room with panoramic ocean views, king-size bed, and private balcony. Wake up to breathtaking sunrises.',
                'base_price': 8500.00,
                'max_guests': 2,
                'bed_type': 'King',
                'size': '45 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'Mini Bar', 'TV', 'Ocean View', 'Balcony'],
                'images': ['https://images.unsplash.com/photo-1582719478250-c89cae4dc85b?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Premium Suite',
                'room_type': 'Suite',
                'description': 'Luxurious suite with separate living area, premium amenities, and stunning city views.',
                'base_price': 15000.00,
                'max_guests': 4,
                'bed_type': 'King + Sofa Bed',
                'size': '75 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'Mini Bar', 'TV', 'City View', 'Living Room', 'Kitchenette'],
                'images': ['https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Standard Double',
                'room_type': 'Standard',
                'description': 'Comfortable double room with modern amenities and serene garden views.',
                'base_price': 4500.00,
                'max_guests': 2,
                'bed_type': 'Queen',
                'size': '32 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'TV', 'Garden View'],
                'images': ['https://images.unsplash.com/photo-1566665797739-1674de7a421a?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Family Room',
                'room_type': 'Family',
                'description': 'Perfect for families with connecting rooms, kid-friendly amenities, and extra space.',
                'base_price': 12000.00,
                'max_guests': 6,
                'bed_type': '2 Queen Beds',
                'size': '60 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'Mini Bar', 'TV', 'Garden View', 'Kids Area'],
                'images': ['https://images.unsplash.com/photo-1590490360182-c33d57733427?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Presidential Penthouse',
                'room_type': 'Penthouse',
                'description': 'Ultimate luxury with 360° views, private terrace, jacuzzi, and butler service.',
                'base_price': 45000.00,
                'max_guests': 4,
                'bed_type': 'King',
                'size': '150 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'Mini Bar', 'TV', 'Ocean View', 'Private Terrace', 'Jacuzzi', 'Butler Service'],
                'images': ['https://images.unsplash.com/photo-1591088398332-8a7791972843?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Cozy Single',
                'room_type': 'Single',
                'description': 'Compact and comfortable room perfect for solo travelers on a budget.',
                'base_price': 3000.00,
                'max_guests': 1,
                'bed_type': 'Single',
                'size': '22 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'TV'],
                'images': ['https://images.unsplash.com/photo-1505693416388-ac5ce068fe85?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Honeymoon Villa',
                'room_type': 'Villa',
                'description': 'Romantic private villa with plunge pool, candlelit dining area, and couples spa access.',
                'base_price': 25000.00,
                'max_guests': 2,
                'bed_type': 'King',
                'size': '90 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'Mini Bar', 'Private Pool', 'Spa Access', 'Room Service'],
                'images': ['https://images.unsplash.com/photo-1602002418082-a4443e081dd1?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            },
            {
                'name': 'Executive Business Room',
                'room_type': 'Executive',
                'description': 'Designed for business travelers with workspace, high-speed WiFi, and meeting room access.',
                'base_price': 7000.00,
                'max_guests': 2,
                'bed_type': 'Queen',
                'size': '40 sqm',
                'amenities': ['WiFi', 'Air Conditioning', 'TV', 'Work Desk', 'Meeting Room', 'Printer Access'],
                'images': ['https://images.unsplash.com/photo-1564078516393-cf04bd966897?w=800'],
                'is_active': True,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        ]
        rooms_collection.insert_many(sample_rooms)
        print("Sample rooms seeded (INR pricing).")

    # Seed seasonal pricing
    if seasonal_pricing_collection.count_documents({}) == 0:
        rooms = list(rooms_collection.find())
        seasonal_data = []
        for room in rooms:
            for month in [12, 1, 2]:
                seasonal_data.append({
                    'room_id': str(room['_id']),
                    'month': month,
                    'month_name': datetime(2024, month, 1).strftime('%B'),
                    'price_multiplier': 1.5,
                    'reason': 'Peak Season',
                    'is_active': True,
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
            for month in [3, 4, 10, 11]:
                seasonal_data.append({
                    'room_id': str(room['_id']),
                    'month': month,
                    'month_name': datetime(2024, month, 1).strftime('%B'),
                    'price_multiplier': 1.2,
                    'reason': 'Shoulder Season',
                    'is_active': True,
                    'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })

        if seasonal_data:
            seasonal_pricing_collection.insert_many(seasonal_data)
            print("Seasonal pricing seeded.")

    # Seed sample bookings
    if bookings_collection.count_documents({}) == 0:
        rooms = list(rooms_collection.find({'is_active': True}))
        if rooms:
            sample_bookings = [
                {
                    'room_id': str(rooms[0]['_id']),
                    'user_id': 'sample_user_1',
                    'guest_name': 'Rahul Sharma',
                    'guest_email': 'rahul.sharma@email.com',
                    'guest_phone': '+91 98765 43210',
                    'check_in': (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d'),
                    'check_out': (datetime.now() + timedelta(days=5)).strftime('%Y-%m-%d'),
                    'nights': 3,
                    'guests': 2,
                    'total_price': rooms[0]['base_price'] * 3,
                    'special_requests': 'Early check-in if possible',
                    'status': 'confirmed',
                    'created_at': (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S'),
                    'payment_status': 'paid'
                },
                {
                    'room_id': str(rooms[1]['_id']),
                    'user_id': 'sample_user_2',
                    'guest_name': 'Priya Patel',
                    'guest_email': 'priya.patel@email.com',
                    'guest_phone': '+91 87654 32109',
                    'check_in': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
                    'check_out': (datetime.now() + timedelta(days=4)).strftime('%Y-%m-%d'),
                    'nights': 3,
                    'guests': 3,
                    'total_price': rooms[1]['base_price'] * 3,
                    'special_requests': 'Extra bed for child',
                    'status': 'pending',
                    'created_at': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
                    'payment_status': 'pending'
                },
                {
                    'room_id': str(rooms[2]['_id']),
                    'user_id': 'sample_user_3',
                    'guest_name': 'Amit Verma',
                    'guest_email': 'amit.verma@email.com',
                    'guest_phone': '+91 76543 21098',
                    'check_in': (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d'),
                    'check_out': (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
                    'nights': 3,
                    'guests': 2,
                    'total_price': rooms[2]['base_price'] * 3,
                    'special_requests': '',
                    'status': 'confirmed',
                    'created_at': (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d %H:%M:%S'),
                    'payment_status': 'paid'
                }
            ]
            bookings_collection.insert_many(sample_bookings)
            print("Sample bookings seeded.")

    # Seed sample guests
    if guests_collection.count_documents({}) == 0:
        sample_guests = [
            {'name': 'Rahul Sharma', 'email': 'rahul.sharma@email.com', 'phone': '+91 98765 43210', 'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            {'name': 'Priya Patel', 'email': 'priya.patel@email.com', 'phone': '+91 87654 32109', 'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
            {'name': 'Amit Verma', 'email': 'amit.verma@email.com', 'phone': '+91 76543 21098', 'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')},
        ]
        guests_collection.insert_many(sample_guests)
        print("Sample guests seeded.")

# Run seed data on startup
with app.app_context():
    seed_data()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

