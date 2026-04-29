# Setup MongoDB Locally on Windows

Since your corporate network blocks MongoDB Atlas (SSL handshake error), install MongoDB locally.

## Option A: Quick Start with MongoDB ZIP (No Admin Required)

1. **Download MongoDB Community Server ZIP**
   - Go to: https://www.mongodb.com/try/download/community
   - Select: Version = `7.0.x`, Platform = `Windows`, Package = `zip`
   - Download and extract to: `D:\Minor\mongodb\`

2. **Create data folder**
   ```cmd
   mkdir D:\Minor\mongodb\data
   ```

3. **Start MongoDB** (run this in a separate terminal)
   ```cmd
   D:\Minor\mongodb\bin\mongod.exe --dbpath D:\Minor\mongodb\data
   ```

4. **Test connection** (in another terminal)
   ```cmd
   python test_mongodb.py
   ```

## Option B: Installer (Requires Admin)

1. Download the `.msi` installer from the same link above
2. Run installer → Choose "Complete" setup
3. MongoDB will run automatically as a Windows Service
4. Test with: `python test_mongodb.py`

## Using Local MongoDB in Your Flask App

If you want to force local MongoDB instead of Atlas, edit `config.py`:

```python
MONGO_URI = 'mongodb://localhost:27017/hotel_management'
```

Or set environment variable before running:
```cmd
set MONGO_URI=mongodb://localhost:27017/hotel_management
python app.py
```

## MongoDB Compass Connection

Once local MongoDB is running, connect Compass with:
```
mongodb://localhost:27017
```

