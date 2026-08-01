# Roblox Discord Verification Bot

บอทยืนยันตัวตน Discord ที่รวมทุกอย่างไว้ในไฟล์เดียว รองรับการรันบน Railway หรือเครื่องส่วนตัว

## 📁 โครงสร้างไฟล์
- `bot.py`: โค้ดหลัก (Discord Bot + API + Database)
- `requirements.txt`: รายการ Library ที่ต้องใช้
- `README.md`: คู่มือการใช้งาน

## 🚀 วิธีการใช้งาน
1. อัปโหลดไฟล์ทั้งหมดขึ้น GitHub
2. นำไป Deploy บน Railway.app
3. ตั้งค่า `DISCORD_TOKEN` ในส่วนของ Variables
4. นำ URL ที่ได้จาก Railway ไปใส่ในสคริปต์ Roblox

## ⚙️ คำสั่งใน Discord
- `!setup_verify`: สร้างปุ่มยืนยันตัวตน (สำหรับ Admin)
