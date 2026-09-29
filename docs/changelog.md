# CHANGELOG

รูปแบบอ้างอิง [Keep a Changelog](https://keepachangelog.com/th/1.1.0/) และใช้ [Semantic Versioning](https://semver.org/th/)

## [Unreleased]

### เพิ่มใหม่ (Added)
- **ส่วนขยาย Chrome (MV3)** ในโฟลเดอร์ `extension/` — ปุ่มลอย + แผงรายการวิดีโอในทุกหน้าเว็บ,
  ตรวจจับ 4 ชั้น (DOM scan, hook fetch/XHR ใน MAIN world, resource timing, webRequest observer),
  popup แสดงรายการต่อแท็บพร้อม logs, และท่อดาวน์โหลดหลายชั้น:
  1. direct http(s) ผ่าน `chrome.downloads` (แนบ Referer จากหน้า)
  2. `blob:` URL → ดาวน์โหลดตรงผ่าน downloads API (ไฟล์ blob ต้องยังมีชีวิตอยู่ในหน้า)
  3. fallback: อ่าน bytes ในหน้า (fetch blob → base64 chunks → data URL)
  4. MSE streams: ดัก `MediaSource.appendBuffer` เก็บชิ้นส่วนแล้วประกอบไฟล์ใหม่
- **ทดสอบ E2E ด้วย Chrome จริง** (`scripts/test_extension.py` + Chrome for Testing + CDP):
  หน้าทดสอบจำลองกลไก `blob:` ของ player2u.com (fetch → Blob → video.src) — ผ่าน 12/12
  รวมถึงพิสูจน์ว่าไฟล์ที่ดาวน์โหลดจาก blob มี SHA256 ตรงกับต้นฉบับทุกไบต์
- แนบ `VDOGrabber-chrome-extension-1.0.0.zip` เข้า release พร้อม checksum

## [1.0.0] — 2026-09-29

### เพิ่มใหม่ (Added)
- **Windows Desktop app (VDOGrabber.exe)** — เบราว์เซอร์ในตัว (Edge WebView2) เปิดหน้าเว็บใดก็ได้
  พร้อมแถบเครื่องมือฉีดในหน้า: ปุ่มตรวจหาวิดีโอ, รายการวิดีโอที่พบ, ปุ่มดาวน์โหลด
- **ตรวจจับวิดีโอ 3 ชั้น** — DOM scan (`<video>/<source>`, og:video, JSON-LD, ลิงก์สื่อ),
  hook `fetch`/`XMLHttpRequest` จับ m3u8/mpd/mp4, และ performance resource-timing
- **เอนจินดาวน์โหลด yt-dlp** (bundle ใน exe, อัปเดตตัวเองได้) + **ffmpeg** ดาวน์โหลดอัตโนมัติเมื่อเจอ HLS/DASH
- **เลือกรูปแบบ/คุณภาพ** — probe ด้วย `yt-dlp -J` แสดง resolution/ขนาด/tbr ต่อรายการ
- **ระบบ log ละเอียด** — `app.log` (RotatingFileHandler 5MB×5), `downloads.log` (yt-dlp ทุกบรรทัด),
  `events.jsonl` (structured), viewer ใน Control Center, เปิดโฟลเดอร์ log, Export diagnostics (.zip)
- **Control Center** — แท็บดาวน์โหลด (คิว/ความคืบหน้า/ยกเลิก), วิดีโอที่ตรวจพบ, Logs, ตั้งค่า, เกี่ยวกับ
- **โหมด --selftest** — ทดสอบ headless ครบวงจร (engine + detection JS + ดาวน์โหลดจริงผ่าน local server)
- **แพ็กไฟล์เดียวด้วย PyInstaller** — พร้อมไอคอนและ version resource, ไม่ต้องติดตั้ง Python
- **แอป Android (Kotlin)** — WebView browser, ตรวจจับวิดีโอแบบเดียวกัน + sniff ระดับ native
  (`shouldInterceptRequest`), ดาวน์โหลดผ่าน DownloadManager, ตัวอ่าน log ในแอป
- **GitHub Actions workflow** ปั่น APK อัตโนมัติ และแนบ release พร้อม SHA256
- **หน้าเว็บดาวน์โหลดบน GitHub Pages** — ดึง release ล่าสุดจาก GitHub API อัตโนมัติ
  พร้อมปุ่มดาวน์โหลดและ checksum ที่ก๊อปได้
- เอกสารครบชุด md + html: README, RESEARCH (วิเคราะห์ส่วนขยายต้นแบบ), CHANGELOG พร้อมไดอะแกรมและสกรีนช็อต
- AGENT.md สำหรับผู้ดูแล/AI ที่มาทำงานต่อใน repo

### ข้อจำกัดที่ทราบ (Known issues)
- ไม่รองรับ DRM; เนื้อหาหลังล็อกอินบางประเภทใช้ไม่ได้ (ไม่มี cookie profile)
- Android ยังไม่รวมไฟล์ HLS/DASH — แนะนำใช้เดสก์ท็อป (roadmap: ฝัง youtubedl-android)
- ไฟล์ `code/` (ส่วนขยายต้นแบบที่ minify) ถูก `.gitignore` — มีแต่ในเครื่องผู้พัฒนา

[1.0.0]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.0.0
