# CHANGELOG

รูปแบบอ้างอิง [Keep a Changelog](https://keepachangelog.com/th/1.1.0/) และใช้ [Semantic Versioning](https://semver.org/th/)

## [Unreleased]

## [1.0.1] — 2026-09-29

### เพิ่มใหม่ (Added) — Android v1.0.1 (versionCode 2)
- **ตรวจหาวิดีโอใน iframe แบบเดียวกับเดสก์ท็อป** — Detector.kt เพิ่ม `scanIframes()`:
  same-origin/srcdoc ลงไปสแกนข้างในจริง, cross-origin embed player รายงาน URL เป็น kind `embed`
- **ปุ่ม "🌐 เปิดหน้า embed"** ใน bottom sheet ของรายการ embed — เปิดหน้า embed เป็นหน้าหลัก
  แล้วตัวตรวจจับสแกนวิดีโอในหน้านั้นต่ออัตโนมัติ (แบบเดียวกับ toolbar เดสก์ท็อป)
- รายการ embed เพิ่มจาก `<embed>` / `<object>` player

### เพิ่มใหม่ (Added) — CI เดสก์ท็อป
- Workflow ใหม่ `.github/workflows/desktop.yml`: รัน `python app/main.py --selftest` บน Windows runner
  ทุก push/PR ที่แตะ `app/**` หรือ `scripts/build_exe.py` — และแนบ `VDOGrabber.exe` + SHA256 ลง release เมื่อ tag `v*` (คู่กับ workflow ฝั่ง Android)

### แก้ไข (Fixed) — ส่วนขยาย Chrome v1.1.1
- **content.js  hardened กัน uncaught error บนเว็บจริง** (รายงาน `content.js:180 (anonymous function)`):
  1. ทุก `chrome.runtime.*` call ถูก guard ด้วย `extAlive()` — กรณี **reload/อัปเดต extension แล้วสคริปต์เก่ายังรันค้าง**
     ในแท็บที่เปิดอยู่ ("Extension context invalidated") จะแจ้งเตือนให้กด F5 แทนที่จะพัง
  2. **UI mount ทนเอกสารพิเศษ** (XML/PDF/doctype แปลก) ที่ `attachShadow` ทำไม่ได้ — detection ยังทำงาน
     แม้แผงไม่ขึ้น และ `ensureUi()` พยายาม mount ใหม่ทุก 3 วินาที (กัน SPA ลบ host ทิ้ง)
  3. **คัดลอกลิงก์บนเว็บ http** ที่ไม่มี `navigator.clipboard` — fallback ไป `execCommand("copy")`

### เพิ่มใหม่ (Added) — Extension 1.1.2
- **ปุ่ม "🌐 เปิดหน้าเว็บ" บนรายการ iframe embed** (ทั้ง extension panel และ desktop toolbar) —
  เปิดหน้า embed นั้นเป็นหน้าหลักแล้วระบบสแกนวิดีโอในหน้าต่ออัตโนมัติ
  (navigate แบบ in-page ไม่ผ่าน callback จึงไม่มี TypeError)

### เพิ่มใหม่ (Added) — Desktop 1.0.1 + Extension 1.1.0
- **ตรวจหาวิดีโอจาก iframe** (ปุ่ม 🔍 ตรวจหาวิดีโอ):
  - iframe ต้นกำเนิดเดียวกัน / srcdoc → ลงไปสแกนข้างในจริง (`<video>`, ลิงก์สื่อ, resource timing ของ frame)
  - iframe ต่าง origin (embed player อย่าง player2u/dood/streamtape) → เสนอ **URL ของ embed** เป็นตัวเลือกดาวน์โหลด
    แล้วให้ yt-dlp สกัดวิดีโอ (desktop) — ข้อจำกัดของ same-origin policy ทำให้เข้าถึงข้างในไม่ได้โดยตรง
  - Extension: แผงของหน้าแม่รวมรายการจาก **ทุก frame** + native sniffer เข้าด้วยกัน
  - ทดสอบ: selftest เพิ่ม iframe cross-origin + srcdoc (ผ่านครบ 4 เช็ค) · E2E extension เพิ่มเป็น 18 ข้อ
    (คลิกระบุด้วย URL + รอไฟล์ด้วย SHA256 ที่คาดหวัง กันรายการข้ามเฟสปนกัน)

### แก้ไข (Fixed) — Desktop v1.0.1
- **TypeError spam ตอนพิมพ์ URL / กดย้อน-รีโหลดในแถบเครื่องมือ** (`returnValuesCallbacks … is not a function`) —
  สาเหตุ: pywebview ส่งค่ากลับให้ JS callback หลัง method จบ แต่ `api.navigate` เองก็ navigate หน้านั้นทันที
  ทำให้หน้าเว็บ (และ callback) หายไปก่อนการส่งมอบ แก้ 2 ชั้น:
  1. แถบเครื่องมือ navigate ในหน้าเองด้วย JS (`location.href` / `history`) ไม่ผ่าน Python อีกต่อไป
  2. `api.navigate` (สำหรับผู้เรียกอื่น) หน่วงโหลด 80ms ให้ callback ส่งมอบก่อน
- ปุ่ม ⚙️ ตอนนี้ `restore()` + `show()` Control Center จริงๆ — หน้าต่างที่เปิดค้างไว้จะโผล่มาหน้าสุด ไม่ต้องกดรัว
- แก้ Windows version resource (`filevers`/`prodvers` ใน `scripts/version_info.txt`) ให้ตรงกับ 1.0.1
- ทดสอบ regression ใหม่: `scripts/test_navigate_fix.py` (ซ่อนหน้าต่าง, ยิงทั้งสอง call style,
  ตรวจว่า stderr สะอาด) — ผ่านทั้งหมด, `--selftest` ยังผ่าน, exe 1.0.1 ผ่าน selftest ใน frozen mode

### แก้ไข (Fixed) — ส่วนขยาย Chrome v1.1.0
- **"Failed - Network error" ที่เกิดกับวิดีโอแบบ blob: ได้รับการแก้ครบวงจร** —
  พบสาเหตุ 3 ชั้นจากการทดสอบ E2E และแก้ทั้งหมด:
  1. `chrome.downloads.download` **ห้ามส่ง header `Referer`** ("Unsafe request header name") — เดิมใส่ไปทำให้ดาวน์โหลดตรงพังทุกเส้นทาง
  2. เส้นทางสำรองเดิมส่งไฟล์เป็น **data: URL ยักษ์** ซึ่ง Chrome ดาวน์โหลดไม่ได้เกินขนาดหนึ่ง — เปลี่ยนเป็นประกอบไฟล์เป็น Blob ใน **offscreen document** (reason: `BLOBS`) แล้วดาวน์โหลดผ่าน `blob:` URL ของ extension — สตรีม chunk ทีละ 4 MB ประหยัดหน่วยความจำ พร้อม cap 800 MB
  3. ดาวน์โหลดที่ "เริ่มแล้วแต่โดน kill" (MSE blob → `NETWORK_FAILED`) ตอนนี้ถูกเฝ้าดูด้วย `downloads.onChanged` แล้ว**สลับไป fallback อัตโนมัติ** — ไม่ต้องกดซ้ำ
- **player2u.com ใช้ MSE stream** (ไม่ใช่ File blob) — โค้ดอ่าน blob ตรงไม่ผ่าน → ตอนนี้ fallback ไป **MSE capture**: hook `MediaSource.addSourceBuffer/appendBuffer` เก็บทุก chunk ที่เล่นแล้วประกอบเป็นไฟล์ใหม่ (ทดสอบกับ fMP4 จริง — bytes ตรงต้นฉบับ)
- **ล้างรายการวิดีโอเก่าเมื่อ navigate ใหม่** — blob: URL ตายเมื่อเปลี่ยนหน้า แผงไม่ควรเสนอลิงก์ตายอีก
- `readBlob` ใช้ XHR fallback เมื่อ fetch ไม่ตอบสนอง + รายงาน error ที่มีรายละเอียด (ชนิด response/สาเหตุ)

### ทดสอบ (Tests)
- E2E ขยายเป็น **20 ข้อ** (headless Chrome for Testing + CDP): direct blob/http,
  force-fallback (page blob read + page fetch) ผ่าน offscreen assembler, และ MSE capture
  ด้วย fMP4 จริง (init+segment ตัดจาก shaka-demo-assets พร้อม parse sidx) — ทุกข้อตรวจ
  SHA256 เทียบไบต์ต้นฉบับ
- เพิ่ม debug bridge (page ↔ content ↔ SW) สำหรับอ่าน logs/downloads ระหว่างทดสอบ

### บำรุงรักษา (Maintenance) — Repo
- ลบ `watch_fixed.py` ที่ถูก track ไว้ทั้งที่มีเนื้อหาเพียงบรรทัดเดียว (ขยะจาก redirect ผิด)
- ลบ `WATCHDOG_JS` ที่ไม่มีที่ใช้แล้วออกจาก `app/core/detector.py` (watchdog ทำงานฝั่ง Python)
- **แก้ memory growth ในเซสชันยาว**: MediaStore ทั้ง desktop และ Android ขยับเป็น LRU จริง
  (รายการเดิมที่พบซ้ำถูก touch ไปท้ายแทนการนิ่ง), `DownloadManager` เริ่ม prune ประวัติงานเก่า
  (เหลือ 200 รายการล่าสุด และคืน Job ที่จบแล้วออกจากหน่วยความจำ)
- **selftest เพิ่มขั้น `pair_sync`** — ตรวจว่า `DETECT_JS` (desktop) และ `Detector.INJECT_JS` (Android)
  สังเคราะห์ JS ที่ถูกต้อง (`node --check` เมื่อมี Node.js / structural checks เมื่อไม่มี) และมี
  feature markers ตรงกัน (scanIframes, EMBED_RE, MEDIA_RE, embed kind) ทำให้กติกา "detector เป็นคู่"
  ใน AGENTS.md ถูกบังคับอัตโนมัติ ไม่ต้องรอล้ำ CI ฝั่ง Android มาเจอทีหลัง

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

[1.0.1]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.0.1
[1.0.0]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.0.0
