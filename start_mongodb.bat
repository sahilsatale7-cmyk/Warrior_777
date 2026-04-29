@echo off
echo Starting MongoDB...
"mongodb\mongodb-win32-x86_64-windows-7.0.14\bin\mongod.exe" --dbpath "D:\Minor\mongodb\data" --bind_ip 127.0.0.1 --port 27017
pause
