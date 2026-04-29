from pymongo import MongoClient
client = MongoClient('mongodb://localhost:27017/hotel_management', serverSelectionTimeoutMS=2000)
db = client.get_database('hotel_management')

# Fix all user emails to lowercase
users = list(db.users.find({}))
fixed = 0
for u in users:
    email = u.get('email', '')
    lower_email = email.lower()
    if email != lower_email:
        db.users.update_one({'_id': u['_id']}, {'$set': {'email': lower_email}})
        print(f"  Fixed: {email} -> {lower_email}")
        fixed += 1

print(f"Fixed {fixed} user emails to lowercase")

# Verify
users = list(db.users.find({}, {'email': 1, 'name': 1}))
for u in users:
    print(f"  {u.get('name','?')} | {u.get('email','?')}")
