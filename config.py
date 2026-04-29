import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'hotel-management-secret-key-2024'
    
    # Local MongoDB (primary) - run 'mongod --dbpath ./mongodb/data' to start
    MONGO_URI = os.environ.get('MONGO_URI') or 'mongodb://localhost:27017/hotel_management'
    
    # MongoDB Atlas fallback (cloud)
    MONGO_ATLAS = 'mongodb+srv://sahilsatale_db_user:Sahil%407071@cluster0.kkzit1u.mongodb.net/hotel_management?retryWrites=true&w=majority&appName=Cluster0&tlsAllowInvalidCertificates=true'
    
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') or 'admin'
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or 'admin123'
    SESSION_TYPE = 'filesystem'
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours
