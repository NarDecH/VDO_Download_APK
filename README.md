# VDO Grabber

**เปิดหน้าเว็บที่คุณต้องการ → กดปุ่มดาวน์โหลดวิดีโอที่กำลังแสดงอยู่**

แอปนี้เกิดจากการวิเคราะห์ส่วนขยาย Chrome "Video Download Helper" (โฟลเดอร์ `code/`) แล้วออกแบบใหม่
เป็นแอปสแตนด์อโลน 2 แพลตฟอร์ม:

| แพลตฟอร์ม | รูปแบบ | เอนจิน |
|---|---|---|
| Windows | `VDOGrabber.exe` ไฟล์เดียว (PyInstaller) ไม่ต้องติดตั้ง Python | pywebview (WebView2) + yt-dlp + ffmpeg |
| Android | `VDOGrabber-android.apk` (GitHub Actions build) | Kotlin WebView + DownloadManager |

## ลิงก์ด่วน

- ⬇ **[หน้าดาวน์โหลด (GitHub Pages)](https://nardech.github.io/VDO_Download_APK/)** — ดึง release ล่าสุดอัตโนมัติ + checksum
- 📖 เอกสารฉบับเต็ม: [docs/readme.md](docs/readme.md) · วิเคราะห์ต้นแบบ: [docs/research.md](docs/research.md) · [docs/changelog.md](docs/changelog.md)
- 🤖 คู่มือ AI/ผู้ดูแล: [AGENT.md](AGENT.md)

## เริ่มใช้งานใน 30 วินาที

1. ดาวน์โหลด `VDOGrabber-windows-x64.exe` จาก [Releases](https://github.com/NarDecH/VDO_Download_APK/releases/latest) (หรือหน้า Pages ข้างบน)
2. ดับเบิลคลิก → พิมพ์ URL → เล่นวิดีโอ → กด **⬇ ดาวน์โหลด** บนแถบเครื่องมือ
3. ไฟล์อยู่ที่ `Downloads\VDOGrabber` — ถ้าเจอปัญหา เปิดแท็บ 📜 Logs แล้วกด Export diagnostics

> ซอร์สโค้ดต้นแบบใน `code/` ใช้เพื่อการศึกษาเท่านั้น และไม่ได้ถูกเผยแพร่ใน repo นี้ (ดู `.gitignore`) — ทุกโค้ดที่ publish เขียนขึ้นใหม่ (MIT)

## สร้างเอง

```bash
pip install -r requirements.txt
run.bat                           # (Windows) รันจากซอร์ส: เช็ค Python → ติดตั้ง deps อัตโนมัติ → เปิดแอป
python app/main.py --selftest     # ทดสอบ
python scripts/build_exe.py       # แพ็ก exe
cd android && ./gradlew assembleDebug   # หรือปล่อยให้ CI ทำ
```

เอกสารฉบับสวย (HTML): [readme.html](docs/readme.html) · [research.html](docs/research.html) · [changelog.html](docs/changelog.html) · [หน้าดาวน์โหลด](https://nardech.github.io/VDO_Download_APK/)

License: MIT
