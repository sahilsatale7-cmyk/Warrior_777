"""
Local MongoDB Setup Script
Downloads and configures MongoDB Community Server (portable)
No admin privileges required - avoids Device Guard issues
"""

import os
import sys
import urllib.request
import zipfile
import shutil

MONGODB_VERSION = "7.0.14"
MONGODB_URL = f"https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-{MONGODB_VERSION}.zip"
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mongodb")
DATA_DIR = os.path.join(DOWNLOAD_DIR, "data")


def download_file(url, dest):
    """Download file with progress"""
    print(f"Downloading MongoDB {MONGODB_VERSION}...")
    print(f"URL: {url}")
    print(f"Dest: {dest}")
    
    def report_progress(block_num, block_size, total_size):
        downloaded = block_num * block_size
        percent = min(100, int(downloaded * 100 / total_size))
        sys.stdout.write(f"\rProgress: {percent}% ({downloaded // 1024 // 1024} MB / {total_size // 1024 // 1024} MB)")
        sys.stdout.flush()
    
    urllib.request.urlretrieve(url, dest, reporthook=report_progress)
    print("\nDownload complete!")


def setup_mongodb():
    """Download and extract MongoDB"""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    
    zip_path = os.path.join(DOWNLOAD_DIR, f"mongodb-{MONGODB_VERSION}.zip")
    
    # Check if already downloaded
    mongod_path = os.path.join(DOWNLOAD_DIR, f"mongodb-win32-x86_64-windows-{MONGODB_VERSION}", "bin", "mongod.exe")
    if os.path.exists(mongod_path):
        print(f"MongoDB already exists at: {mongod_path}")
        return mongod_path
    
    # Download
    if not os.path.exists(zip_path):
        try:
            download_file(MONGODB_URL, zip_path)
        except Exception as e:
            print(f"\n[ERROR] Download failed: {e}")
            print("This might be due to corporate firewall restrictions.")
            print("\nAlternative: Download manually from:")
            print(f"  {MONGODB_URL}")
            print(f"  Extract to: {DOWNLOAD_DIR}")
            return None
    
    # Extract
    print(f"Extracting to {DOWNLOAD_DIR}...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(DOWNLOAD_DIR)
    
    print("Extraction complete!")
    
    if os.path.exists(mongod_path):
        print(f"MongoDB ready at: {mongod_path}")
        return mongod_path
    
    return None


def create_start_script(mongod_path):
    """Create a batch file to start MongoDB"""
    batch_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "start_mongodb.bat")
    
    with open(batch_path, 'w') as f:
        f.write('@echo off\n')
        f.write('echo Starting MongoDB...\n')
        f.write(f'"{mongod_path}" --dbpath "{DATA_DIR}" --bind_ip 127.0.0.1 --port 27017\n')
        f.write('pause\n')
    
    print(f"Start script created: {batch_path}")
    return batch_path


if __name__ == '__main__':
    print("=" * 60)
    print("MongoDB Local Setup")
    print("=" * 60)
    
    mongod = setup_mongodb()
    
    if mongod:
        batch = create_start_script(mongod)
        print("\n" + "=" * 60)
        print("SETUP COMPLETE!")
        print("=" * 60)
        print(f"\nTo start MongoDB, run:")
        print(f"  {batch}")
        print(f"\nOr run directly:")
        print(f'  "{mongod}" --dbpath "{DATA_DIR}"')
        print("\nThen your Flask app will connect to local MongoDB automatically!")
    else:
        print("\n[SETUP FAILED]")
        print("Please download MongoDB manually or use MongoDB Atlas.")

