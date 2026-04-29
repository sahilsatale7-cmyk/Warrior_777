from pymongo import MongoClient

# Test 1: MongoDB Atlas (Cloud) - Standard
uri_atlas = 'mongodb+srv://sahilsatale_db_user:yes@cluster0.kkzit1u.mongodb.net/hotel_management?retryWrites=true&w=majority&appName=Cluster0'

print('=== Test 1: MongoDB Atlas (Standard SSL) ===')
try:
    client = MongoClient(uri_atlas, serverSelectionTimeoutMS=10000)
    client.admin.command('ping')
    db = client.get_default_database()
    print('SUCCESS: Connected to MongoDB Atlas!')
    print('Database name:', db.name)
    print('Collections:', db.list_collection_names())
except Exception as e:
    print('FAILED:', str(e)[:200])

print()

# Test 2: MongoDB Atlas with relaxed TLS (bypasses corporate SSL inspection)
uri_atlas_relaxed = 'mongodb+srv://sahilsatale_db_user:yes@cluster0.kkzit1u.mongodb.net/hotel_management?retryWrites=true&w=majority&appName=Cluster0&tlsAllowInvalidCertificates=true'

print('=== Test 2: MongoDB Atlas (Relaxed TLS) ===')
try:
    client = MongoClient(uri_atlas_relaxed, serverSelectionTimeoutMS=10000)
    client.admin.command('ping')
    db = client.get_default_database()
    print('SUCCESS: Connected with relaxed TLS!')
    print('Database name:', db.name)
    print('Collections:', db.list_collection_names())
except Exception as e:
    print('FAILED:', str(e)[:200])

print()

# Test 3: Local MongoDB
uri_local = 'mongodb://localhost:27017/hotel_management'

print('=== Test 3: Local MongoDB (localhost:27017) ===')
try:
    client = MongoClient(uri_local, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.get_default_database()
    print('SUCCESS: Connected to Local MongoDB!')
    print('Database name:', db.name)
except Exception as e:
    print('FAILED:', str(e)[:200])
    print('NOTE: Local MongoDB is not running.')

