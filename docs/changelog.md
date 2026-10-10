# CHANGELOG

รูปแบบอ้างอิง [Keep a Changelog](https://keepachangelog.com/th/1.1.0/) และใช้ [Semantic Versioning](https://semver.org/th/)

## [1.9.8] — 2026-10-10

### การเปลี่ยนแปลง
- telemetry: แยก selftest ออกจากสถิติผู้ใช้จริง (origin tag) + เก็บ reason ของ download_error_line + ทดสอบ fallback ข้าม 6 patterns


## [1.9.7] — 2026-10-10

### การเปลี่ยนแปลง
- fallback retry: UX ใหม่ - ระหว่างลอง fallback แสดง badge 'ลองแหล่งอื่น' แทน error สีแดง (จาก field log จริง 2 session)


## [1.9.6] — 2026-10-10

### การเปลี่ยนแปลง
- Windows app: เพิ่ม selftest phase ตรวจปุ่ม 📋 บนแถบเครื่องมือจริงใน webview — ใส่ URL ในคลิปบอร์ด กดปุ่ม แล้วหน้าต้องไปถึง URL นั้นทันที (ปิดช่องที่ v1.9.4/1.9.5 มีแค่ unit-level)


## [1.9.5] — 2026-10-10

### การเปลี่ยนแปลง
- Windows app ปรับจาก feedback: ปุ่ม 📋 บนแถบเครื่องมือเปิด URL จากคลิปบอร์ดทันที ไม่ต้องรอกด Enter อีกต่อไป
- **ไฟล์ติดตั้ง Windows แนบใน release แล้ว**: `VDOGrabber-1.9.5-windows-x64.exe` (PyInstaller onefile, selftest ผ่าน) + `.sha256` แยกต่างหาก — หน้าดาวน์โหลด (Pages) ดึง assets ผ่าน GitHub API จึงโชว์ไฟล์นี้เองอัตโนมัติ


## [1.9.4] — 2026-10-10

### การเปลี่ยนแปลง
- Windows app: 📋 ปุ่มคลิปบอร์ดบนแถบเครื่องมือในหน้าเว็บ — กดแล้วดึง URL จากคลิปบอร์ดมาใส่ช่องที่อยู่ (กด Enter เพื่อไป) ไม่ต้องพิมพ์ใหม่
- ปุ่ม 📋 เปิดจากคลิปบอร์ดใน Control Center ทำงานเหมือนเดิม


## [1.9.3] — 2026-10-09

### การเปลี่ยนแปลง
- Android: ปุ่มลองใหม่แบบแตะครั้งเดียวสำหรับงานดาวน์โหลดที่ติดขัด (watchdog 1.9.2 ต่อยอด) — Snackbar ถามลองใหม่แล้วจัดคิวใหม่ผ่านเส้นทางเดิมทั้งหมด + event download_retry_offer/download_retry วัดผลใน field log
- เอกสารทดสอบเครื่องจริงเพิ่มวิธีวิเคราะห์ zip ที่ผู้ใช้ส่งมาโดยตรง


## [1.9.2] — 2026-10-09

### การเปลี่ยนแปลง
- Android: watchdog จับงานดาวน์โหลดตรงที่จัดคิวแล้วเงียบหาย (download_queued ไม่มีตามเลย) เขียน event download_stuck ให้เห็นใน field log
- สคริปต์วิเคราะห์ log อ่านไฟล์ zip ของ diagnostics ได้โดยตรงไม่ต้องแตกไฟล์


## [1.9.1] — 2026-10-09

### การเปลี่ยนแปลง
- Android: แก้แท็บไฟล์ที่ดาวน์โหลดแล้วในชีต 🎬 ว่างเปล่าเพราะ COLUMN_LOCAL_FILENAME โดน SecurityException — อ่านผ่าน COLUMN_LOCAL_URI แทน และกันให้แถวเดียวพังไม่ล้มทั้งลิสต์ (วิเคราะห์จาก field logs ผู้ใช้)


## [1.9.0] — 2026-10-08

### การเปลี่ยนแปลง
- ส่วนขยายเลือก variant คุณภาพสูงสุดของ HLS master เสมอ (แก้ไฟล์เล็กกว่าแอป Windows เมื่อ master ไม่มี BANDWIDTH)
- ปฏิเสธสตรีมที่เสียงแยกไฟล์ (HLS EXT-X-MEDIA / DASH AdaptationSet) ด้วยข้อความชัดเจน แทนการเซฟไฟล์เล็กและไม่มีเสียง
- E2E ส่วนขยาย 75 เคส (+5 ใหม่) ผ่านครบ


## [1.8.0] — 2026-10-07

### การเปลี่ยนแปลง
- แก้บั๊กดาวน์โหลดพร้อมกัน: แยกโฟลเดอร์ทำงานรายงาน (engine-work/<pid>) งานที่เสร็จก่อนไม่ไปแตะ/ลบไฟล์ชั่วคราวของงานที่ยังวิ่ง (สาเหตุ Errno 2 จาก field log)
- ปิดเสียง CanceledException ตอนกดพักไม่ให้เป็น error ลอยๆ
- ฝั่ง desktop เพิ่มพัก/ดาวน์โหลดต่อเทียบเท่า Android (ปุ่มใน Control Center)
- +3 เทส (desktop 28, Android JVM 64)


## [1.7.0] — 2026-10-07

### การเปลี่ยนแปลง
- หยุดพัก/ดาวน์โหลดต่อการดาวน์โหลดสตรีม: ปุ่มพักในแถวและใน notification เก็บไฟล์ชั่วคราวของ yt-dlp ไว้แล้วบันทึกงานลง paused_jobs.tsv กดดาวน์โหลดต่อแล้วต่อจาก fragment เดิม (ไม่เริ่มใหม่)
- หมวดหยุดพักไว้ในแท็บกำลังดาวน์โหลด พร้อมปุ่มลบเฉพาะไฟล์ชั่วคราว
- +10 เทส JVM (รวม 62)


## [1.6.1] — 2026-10-07

### การเปลี่ยนแปลง
- จับเวอร์ชัน yt-dlp จริงตอนเปิดแอป (แก้ engine_ready version=null บนเครื่องจริง)
- throttle log ความคืบหน้า 1 บรรทัด/วินาที ลด downloads.log จาก 1.2MB/4 งาน เหลือโครงสร้างสำคัญครบ
- เอกสารบทเรียน field log ใน docs/research.md หัวข้อ 7


## [1.6.0] — 2026-10-06

### การเปลี่ยนแปลง
- ปุ่ม ล้างทั้งหมด ในแท็บดาวน์โหลดเสร็จแล้ว ยืนยันครั้งเดียวลบทุกไฟล์
- แจ้งเตือนในแอปเมื่อดาวน์โหลดเสร็จ/ไม่สำเร็จ พร้อมปุ่มเปิดรายการสื่อ
- เอกสารสถาปัตยกรรมการดาวน์โหลด Android ใน docs/research.md


## [1.5.0] — 2026-10-06

### การเปลี่ยนแปลง
- แยกแท็บ กำลังดาวน์โหลด / ดาวน์โหลดเสร็จแล้ว ในหน้าสื่อ (ตามคำขอผู้ใช้)
- ยกเลิกได้ทีละรายการ พร้อมเปอร์เซ็นต์และแถบความคืบหน้ารายไฟล์
- รองรับดาวน์โหลดพร้อมกันหลายไฟล์ เปอร์เซ็นต์ไม่ปนกันแล้ว
- CI ทดสอบสคริปต์ release อัตโนมัติ (dry-run smoke)


## [1.4.0] — 2026-10-06

### การเปลี่ยนแปลง
- แสดงความคืบหน้าการดาวน์โหลดสด: chip 🎬 ขึ้นเปอร์เซ็นต์ทุกครึ่งวินาที + notification แบบมีแถบตัวเลข
- รีเซ็ตรายการวิดีโอที่เจอเมื่อเปลี่ยนไปหน้าเว็บต่างเว็บ (หน้าเดียวกัน/โดเมนเดียวกันยังเก็บไว้)
- ปุ่มแชร์ log เป็น zip ในหน้า Logs ส่งให้ผู้อื่นดูได้โดยไม่ต้องต่อ adb
- เพิ่ม scripts/release.py คำสั่งเดียวจบสำหรับ release


## [1.3.6] — 2026-10-06

### ตามคำแนะนำที่ค้างจาก v1.3.5 (ทำครบทุกข้อ)
- **เพิ่มแผนทดสอบบนเครื่องจริง** (`docs/real-device-test-plan.md`) — เช็คลิสต์ 6 เคสจากบทเรียน
  field log (merrylion2/MPD, blob:, fake .mp4, no-file, ความเสถียร WebView) พร้อมวิธีดึง log
  กลับมาวิเคราะห์ (adb pull / Device Explorer) และเกณฑ์ผ่าน
- **เครื่องมือวิเคราะห์ events.jsonl** (`scripts/analyze_events.py`) — สรุป event ตามลำดับ funnel,
  ชี้สัญญาณปัญหาที่รู้จัก (blob:/no-file/HTML ปลอม/engine_ready ไม่มีเวอร์ชัน) รองรับ `--event`
  และ `--json` · มีเทสหน่วย 3 เคสใน scripts/test_units.py (รวมเป็น 27 เคส)
- **Android CI ทน emulator flake มากขึ้น** — `emulator-boot-timeout: 600` ให้ทั้ง 3 attempt
  (ตรงสาเหตุ "API level=1" ครึ่งบูตที่ timeout เดิม 300s) + cache gradle/AVD + warm AVD ล่วงหน้า
- **ฝั่ง desktop ได้ protection เทียบเท่า Android v1.3.5** — กรอง blob: ทั้งชั้น UI/TOOLBAR_JS/API/engine
  (event `download_skipped_blob`), exit 0 แต่ไม่มีไฟล์ → probe `-F` + event `download_no_file_probe`
  (ไม่รายงานสำเร็จลอย ๆ), ไฟล์ที่ได้เป็นหน้า HTML → เก็บเป็นหลักฐาน `.html` + event
  `download_not_media` + ลอง page-fallback อีกครั้งอัตโนมัติ · เอนจินจริงยืนยันว่า `output.mpd`
  ของ merrylion2 มี formats ให้โหลด

## [1.3.5] — 2026-10-05

### Android (แก้ผลวิเคราะห์จาก log ผู้ใช้จริง)
- **แก้ "ดาวน์โหลดสตรีมเสร็จ" แต่ไม่มีไฟล์เกิดขึ้น (log: `stream download done: null`)** — สาเหตุ:
  เอนจิน yt-dlp เขียนไฟล์ลง Downloads/VDOGrabber โดยตรง ซึ่ง scoped storage ของ Android 10+
  (targetSdk 30+) ไม่ให้ไลบรารีที่ไม่ใช่มีเดียสร้างไฟล์ จึง exit 0 แต่ไม่มีไฟล์ตกดิสก์ — ตอนนี้เอนจิน
  เขียนลงโฟลเดอร์ app-private (`Android/data/<pkg>/files/engine-work`) แล้ว publishFile ย้ายเข้า
  Downloads/VDOGrabber ผ่าน MediaStore API ตามเดิม · ถ้า exit 0 แต่ยังไม่มีไฟล์ จะรัน probe `-F`
  แล้ว log สาเหตุจริง (DRM/geo/แผนผังไม่รู้จัก) เป็น event `download_no_file` แทนการรายงานสำเร็จลอย ๆ
- **แก้ spam `[sniff] intercept failed: A WebView method was called on thread 'ThreadPoolForeg'`** —
  shouldInterceptRequest เคยเรียก `web.title` จาก background thread ทำให้การดักจับ media ทาง native
  พังทั้งเส้นทาง — ย้ายการอ่าน page/title ไปทำบน main thread แล้วบันทึกต่อที่นั่น (การดักจับกลับมาทำงาน)
- **กรองลิงก์ `blob:` ไม่ให้เข้าเอนจิน** — yt-dlp เข้าถึง blob URL ไม่ได้ตามธรรมชาติ (มีอยู่แค่ในหน้า);
  แจ้ง toast แนะนำให้ใช้ลิงก์อื่นในรายการ 🎬 แทน (event `download_skipped_blob`) · `routeOf()` คืน null
  สำหรับ URL ที่ใช้ไม่ได้แล้ว · sync กับฝั่ง desktop (DETECT_JS ไม่เปลี่ยน — แต่ routing ทั้งสองฝั่งกรอง blob)
- **notification เมื่อดาวน์โหลดล้มแสดงสาเหตุจริงของ yt-dlp** (เช่น "Unsupported URL: …") แทน stack trace
  ของ exception (`ytDlpErrorLine` — ERROR ก่อน WARNING)
- **E2E ใหม่ 2 เคส** (`HtmlSafetyNetE2E`): หน้า HTML ผ่าน DownloadManager ต้องโดนตรวจ + ลบ + ได้ URL
  สำหรับ re-route (เคส .mp4 ปลอมจาก v1.3.4) และ MPD SegmentTemplate บน loopback ผ่านเอนจินจริงต้องได้
  ไฟล์ fMP4 ที่ไบต์ตรง init+seg1+seg2 (fixtures สร้างตอนรันเทส ไม่แตะ workflow) · ย้าย fixture ของ
  StreamEngineTest ไปใช้ work dir เดียวกับ service จริง
- **แก้ HTML safety net ของ v1.3.4 ที่ไม่เคยทำงานจริงบนเครื่องผู้ใช้** (E2E บน CI จับได้):
  ตัวตรวจ ".mp4 ที่ข้างในเป็น HTML" อ่านพาธไฟล์จาก `COLUMN_LOCAL_FILENAME` ซึ่ง Android ห้ามแอป
  targetSdk ≥ 24 ใช้ (SecurityException ถูกกลืนเงียบ ๆ) — เปลี่ยนมาอ่าน payload ผ่าน
  `dm.openDownloadedFile(id)` แล้วลบด้วย `dm.remove(id)` ทำให้ fake .mp4 โดนลบ + re-route
  ไปเอนจินได้จริงทุกเครื่อง (event `download_not_media` เริ่มเห็นได้ใน log)

## [1.3.4] — 2026-10-05

### Android (แก้ไฟล์ .mp4 ที่ข้างในเป็น HTML)
- **แก้ "กดดาวน์โหลดแล้วได้ไฟล์ .mp4 แต่เปิดไม่ได้ — ข้างในเป็น HTML ของหน้า player ไม่ใช่วิดีโอ"** —
  ลิงก์ที่ไม่มีนามสกุลไฟล์สื่อ (เช่น หน้า player/embed ที่เล่น DASH ผ่าน `output.mpd` ฝั่งใน) เคยถูกส่งเข้า
  DownloadManager แล้วเซฟ HTML ของหน้านั้นเป็น `ชื่อหน้า.mp4` — ตอนนี้จัดเส้นทางด้วย `routeOf()` ใหม่:
  ลิงก์ที่ไม่มีนามสกุลสื่อ = หน้าเว็บ → ส่งให้เอนจิน yt-dlp ในแอป (เปิดหน้า player แล้วดึงสตรีมจริงเอง)
  เหมือนกับลิงก์ m3u8/mpd โดยตรง · event log `download_rerouted`
- **เช็ก magic bytes หลังดาวน์โหลดตรงเสร็จ** (safety net): ถ้า payload เป็น HTML (มี BOM/ช่องว่าง/comment
  นำหน้า `<!doctype html` / `<html` / `<?xml`) จะลบไฟล์ปลอม + ลบแถว DownloadManager ทิ้ง บันทึก event
  `download_not_media` แล้ว**สลับไปดาวน์โหลดต่อด้วยเอนจิน yt-dlp ให้อัตโนมัติ** พร้อม toast แจ้งผู้ใช้
- เพิ่ม JVM unit tests (`HtmlDetectorTest`): routing ของ URL ทั้ง 3 ชนิด + ตัวตรวจ HTML ครอบ BOM,
  ช่องว่าง, comment นำหน้า, XML และไบต์จริงของ mp4/webm/ts/mp3

## [1.3.3] — 2026-10-05

### Android (แก้ดาวน์โหลดไม่สำเร็จ)
- **แก้ "ดาวน์โหลดไม่สำเร็จ: java.io.IOException: File name too long"** — แอปตั้งชื่อไฟล์ตามชื่อหน้าเว็บ
  โดยเดิมจำกัดแค่ 100 *ตัวอักษร* ทั้งที่ ext4 จำกัด 255 *ไบต์* ต่อชื่อไฟล์ — ภาษาไทยใช้ 3 ไบต์/ตัว อิโมจิ 4 ไบต์/ตัว
  หน้าโฆษณาที่ชื่อยาว ๆ เลยทำให้ DownloadManager ปฏิเสธทันที ตอนนี้จำกัดชื่อด้วยจำนวนไบต์ UTF-8 (stem ≤ 180 ไบต์)
  ตัดที่ขอบตัวอักษร/อิโมจิเป๊ะ ไม่ตัดกลางคู่ · ครอบคลุมทั้ง DownloadManager, เทมเพลต yt-dlp และการ publish ลง MediaStore
- CI: E2E บน emulator เพิ่ม attempt ที่ 3 รับมือ flake ที่โดนทั้ง 2 attempt บน tag v1.3.2 · แก้ checksums.txt
  ไม่ให้มีบรรทัด APK universal ซ้ำ (เขียนชื่อ universal ซ้ำใน sha256sum ตอน rename APKs)

## [1.3.2] — 2026-10-05

### Android (แก้ crash + จัด UI ใหม่)
- **แก้แอปปิดตัวเองเมื่อกดลิงก์/ปุ่มในหน้าเว็บ (เช่น ปุ่มโฆษณา overlay)** — สาเหตุ: หน้าเว็บที่มี
  สคริปต์โฆษณาหนัก ๆ ทำให้ renderer process ของ WebView ตาย และเมื่อไม่มี `onRenderProcessGone`
  Android จะปิดแอปทั้ง process ทันที — ตอนนี้จับ event นี้แล้ว **สร้าง WebView ใหม่แทนที่ตัวเก่า
  แล้วโหลดหน้าเดิมต่อ** (ถ้าหน้าเดิมทำล่มซ้ำจะขึ้นหน้าแจ้งเตือนแทน ไม่เข้าสู่ crash loop) ·
  event log `render_gone`
- กันชนเพิ่ม: try/catch รอบ `shouldInterceptRequest` (การดักจับ media บนหน้าโฆษณาแน่น ๆ) และ
  รอบ bridge `reportMedia` (exception บน JS thread ปิดแอปได้) · ลิงก์ scheme อื่น (intent://,
  market:// ฯลฯ) บันทึกเป็น event `external_link` และปล่อย `javascript:` ให้รันในหน้าเหมือน
  เบราว์เซอร์ทั่วไป · จับ error ของหน้าเป็น event `page_error`
- **UncaughtExceptionHandler เขียน `crash.log`** (เหมือนฝั่งเดสก์ท็อป) — ถ้ายังมี crash ที่ไหน
  อีก จะเก็บ stack trace ไว้อ่านได้ใน Logs (แท็บ crash.log เพิ่มใหม่) · event `app_crash`
- **ย้ายช่อง URL ลงด้านล่างของแอป** — แถบเครื่องมือทั้งแถว (ย้อนกลับ/ถัดไป/รีโหลด/ช่อง URL/
  คลิปบอร์ด/Logs) อยู่ใต้หน้าเว็บ ส่วนแถบ 🎬 พบวิดีโอ ยังอยู่ขอบล่างสุดเหมือนเดิม

## [1.3.1] — 2026-10-04

### Desktop v1.3.1
- ปุ่มใหม่ **"📋 เปิดจากคลิปบอร์ด"** ใน Control Center (แท็บดาวน์โหลด) — คัดลอกลิงก์จากที่ไหนก็ได้แล้ว
  กดปุ่มเดียว เปิดหน้านั้นในหน้าต่างเบราว์เซอร์ของแอปทันที · อ่านคลิปบอร์ดฝั่ง Python ด้วย ctypes
  จึงไม่ติด permission prompt ของ WebView2 · เลือก "บรรทัดแรกที่หน้าตาเป็น URL" (รับทั้งลิงก์เต็ม,
  โดเมนเปล่า ๆ ที่เติม https:// ให้, ข้อความที่มีลิงก์ปนอยู่ — แต่ไม่รับอีเมล/ข้อความธรรมดา) ·
  ใส่ URL ที่เปิดกลับลงช่องวางลิงก์ด้วย · event log `clipboard_open`

### Android v1.3.1 (versionCode 14)
- ปุ่มคลิปบอร์ด (ไอคอน 📋) เพิ่มในแถบเครื่องมือด้านบน ข้างช่อง URL — แตะแล้วอ่านลิงก์จากคลิปบอร์ด
  เปิดใน WebView ทันที ใช้กฎเดียวกับฝั่งเดสก์ท็อป · คลิปบอร์ดว่าง/ไม่มีลิงก์จะขึ้น toast บอกเหตุผล ·
  event log `clipboard_open` / `clipboard_open_error`

### Build (Android toolchain)
- อัปเกรด AGP 8.7.3 → **8.13.2** พร้อมไฟล์ `gradlew.bat` ใหม่ — และเพราะ AGP 8.13 ไม่ build บน Gradle 9.6+
  (internal Problems API ถูกถอดออก) wrapper จึงลดจาก 9.8.0 ลงเป็น **9.5.1** (เพดานที่รองรับ) · เพิ่ม
  `gradle-daemon-jvm.properties` ล็อก JDK ของ daemon ที่ 25 พร้อมลิงก์ดาวน์โหลด foojay ทุก OS
  (CI จะดาวน์โหลด JDK เองอัตโนมัติ) · ตรวจแล้วด้วย `:app:compileDebugKotlin` ผ่านในเครื่อง

## [1.3.0] — 2026-10-03

### ส่วนขยาย Chrome v1.1.10
- **ดาวน์โหลดสตรีม DASH (mpd) ได้ในตัวส่วนขยายแล้ว** — เคสที่ผู้ใช้แจ้ง: กดดาวน์โหลดที่ iframe player
  แล้วขึ้น "สตรีมนี้ต้องใช้ VDOGrabber เวอร์ชันเดสก์ท็อป (yt-dlp): HLS/DASH stream - use the VDOGrabber
  desktop app (yt-dlp)" เพราะ v1.1.9 ทำ HLS ได้แต่ยังส่งต่อ DASH ให้แอปเดสก์ท็อป — ตอนนี้ service worker
  **ดึง MPD + ชิ้นส่วนทั้งหมดเอง** เลือก Representation คุณภาพสูงสุด (ถ่วงน้ำหนัก bandwidth, ชอบ video)
  แล้วประกอบเป็นไฟล์ .mp4 (fMP4) เดียวผ่าน offscreen assembler เหมือนที่ทำกับ .ts ของ HLS พร้อม progress
  บนแผงลอย (`vg:dashProgress`)
- รองรับ SegmentTemplate ที่ประกาศบน MPD/Period/AdaptationSet/Representation (inherit ค่าตาม scope,
  template เจาะจงมากที่สุดชนะ, รองรับ $RepresentationID$/$Bandwidth$/$Number$ (มี padding)/$Time$) และ
  SegmentList/SegmentURL (อ่าน `sourceURL` แบบไม่สนตัวพิมพ์แล้ว)
- **เคสที่ผู้ใช้แจ้งเพิ่ม: MPD จริงจาก merrylion2.com ใช้ SegmentTemplate + SegmentTimeline (ไม่มี
  @duration) แต่ยังโดนปฏิเสธผิด ๆ ว่า "unsupported DASH layout (SegmentBase/indexed)"** — ตอนนี้นับ
  ชิ้นส่วนจาก `<S t d r>` ทุกรายการ (รวม @r ที่ซ้ำ, @t ที่ระบุเองหรือไต่ต่อจากก่อนหน้า, @r ติดลบ =
  นับถึงสุดสตรีมตาม mediaPresentationDuration) · รองรับ **SegmentBase/indexRange** (โปรไฟล์ on-demand)
  ด้วย: สื่อเป็น fMP4 ไฟล์เดียวที่ฝัง index ไว้ข้างใน → ดาวน์โหลดทั้งไฟล์จากข้อความใน `<BaseURL>` ของ
  Representation ที่เลือก · เหลือปฏิเสธอย่างชัดเจนเฉพาะเลย์เอาต์ที่ไม่รู้จักจริง ๆ หรือสตรีมสด
- E2E ขยายเป็น 68 เช็ค: ไอเทม mpd ตรงและเส้นทาง embed → html-scan → mpd → ประกอบไฟล์ ต้องได้ไบต์ตรงกับ
  init+seg1+seg2 · เพิ่ม mpd แบบ SegmentTimeline (ได้ init+seg00001..00003 ผ่าน $Number%05d$ ครบไบต์ตรง)
  และแบบ SegmentBase/index (ได้ไฟล์ `<BaseURL>` ของ representation bandwidth สูงสุดเป๊ะ — ไฟล์ของ rep อื่น
  ตั้งใจให้ 404 กันเลือกผิด) (fixtures ใหม่ `dashplayer.html`, `dash/stream.mpd`, `dash/timeline.mpd`,
  `dash/segmentbase.mpd`, `dash/init.mp4`, `dash/seg1.m4s`, `dash/seg2.m4s`, `dash/seg0000x.m4s`,
  `dash/single.mp4`)

### ส่วนขยาย Chrome v1.1.9
- **ดาวน์โหลดสตรีม HLS (m3u8) ได้ในตัวส่วนขยายแล้ว** — เคสที่ผู้ใช้แจ้ง: ไอเทม iframe player
  (เช่น merrylion2.com) "ไม่ดาวน์โหลดไฟล์" เพราะตัว resolve หาเจอสตรีม m3u8 แล้ว แต่ Chrome
  รวมสตรีม HLS เองไม่ได้ จึงบังคับไปใช้แอปเดสก์ท็อปเสมอ — ตอนนี้ service worker จะ**ดึง manifest
  + ชิ้นส่วนทั้งหมดเอง** (ข้ามโดเมนได้เพราะ host_permissions) แล้วประกอบเป็นไฟล์ .ts เดียวผ่าน
  offscreen assembler (pipeline เดียวกับที่ yt-dlp ทำให้แอปเดสก์ท็อป) พร้อม progress บนแผงลอย
- รองรับ master playlist (เลือกคุณภาพสูงสุด), `#EXT-X-MAP` (fMP4 init segment) และปฏิเสธอย่างชัดเจน
  เมื่อสตรีมเข้ารหัส AES-128 (ยังต้องใช้แอปเดสก์ท็อป) · DASH (mpd) ยังส่งต่อแอปเดสก์ท็อปเหมือนเดิม
- E2E ขยายเป็น 60 เช็ค: ไอเทม m3u8 ตรงและเส้นทาง embed → html-scan → m3u8 → ประกอบไฟล์
  ต้องได้ไบต์ตรงกับชิ้นส่วนต้นทาง (fixtures ใหม่ `hlsplayer.html`, `hls/stream.m3u8`, `hls/seg1.ts`, `hls/seg2.ts`)

### Desktop v1.3.0

- **แท็บใหม่ "🕸 Scrape (Sieve)" เชื่อมต่อ Sieve scrape API (scrape.usesieve.com)** — เครื่องมือสกัดข้อมูล
  หน้าเว็บเพิ่มเติมใน Control Center: เชื่อมต่อด้วย **Device Login** (แอปสร้างลิงก์ให้เปิดในเบราว์เซอร์
  แล้วอนุมัติรหัสอุปกรณ์ — ไม่ต้องคอปปี้ key เอง) หรือวาง API key (`dc_sk_...`) พร้อมปุ่มตรวจเครดิต
  รัน scrape job และดูสถานะ/ผลลัพธ์ย้อนหลังได้ในแท็บเดียวกัน · ไม่มี key = โมดูลทั้งหมด dormant
  (ไม่มี request ออกไปไหน แอปทำงานเหมือนเดิมทุกอย่าง)
- ความปลอดภัยตามหลักการเดิม: key เก็บใน settings.json เฉพาะเครื่อง (มี `SIEVE_API_KEY` env fallback)
  **ไม่ถูกเขียนลง log / ถูก redact ใน events.jsonl / ไม่ถูก export / ไม่คืนค่ากลับ UI** · ทุก run บันทึกลง
  `sieve_runs.json` **ก่อน**เริ่ม poll กัน run ซ้ำเมื่อแอปล้มกลางทาง (POST ใช้เครดิตทันทีที่ถูกยอมรับ)
- HTTP client บน stdlib urllib เดิม (ไม่เพิ่ม dependency) · transport inject ได้ — unit tests ใหม่
  จำลอง response/retry/status ได้โดยไม่ยิงเน็ตจริง

[1.3.0]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.3.0

## [1.2.2] — 2026-10-01

### ส่วนขยาย Chrome v1.1.8
- **แก้บั๊ก "iframe ดาวน์โหลดผิดไฟล์"** (เช่น แผงโชว์ `iframe player` ของ merrylion2.com แต่ได้ไฟล์อื่น):
  ตัว resolve ตัด fallback "หยิบสื่อใดก็ได้ในแท็บ" ทิ้ง — ตอนนี้ยอมรับเฉพาะ**สื่อที่หน้า player นั้นโหลดจริง**
  (webRequest จำต้นทาง document ของทุก request เป็นครั้งแรก) หรือ URL ตรงเป๊ะ หน้าเว็บที่ไม่ใช่ player
  จะไม่ถูกตอบแทน (กันโฆษณา/คลิปอื่นหลุดมา)
- **สแกน HTML ของหน้า player เองใน SW** — เลียนแบบ fallback ของแอปเดสก์ท็อป (`_page_fallback`):
  เมื่อไม่มีสื่อในแท็บ จะ fetch หน้า player มาหา URL สื่อ (รวมแบบซ่อนใน `atob("...")` base64 ของ player ที่ obfuscate)
  แล้วดาวน์โหลดจากลิงก์นั้นโดยตรง — ครอบเคส player ที่ซ่อนสตรีมใน JS ทั้งที่เปิด/ไม่เปิดหน้า
- E2E ขยายเป็น 56 เช็ค: หน้าที่มีทั้งคลิปหลอก + iframe player ต้องได้คลิปของ player, player-obf (atob) และ player-hidden
  (สร้าง `<video>` ด้วย JS) ผ่านครบ (fixtures ใหม่ `wrongfile.html`, `player-obf.html`, `player-hidden.html`, `video/clip2.mp4`, `video/clip3.mp4`)

### Desktop v1.2.2
- **☁️ ซิงค์รายการเว็บที่ยกเว้นผ่าน GitHub Gist** — ใส่ GitHub token (สิทธิ์ gist) ครั้งเดียวต่อเครื่อง/เบราว์เซอร์
  แล้วกดอัปโหลด/ดาวน์โหลดเพื่อใช้รายการเดียวกันทุกที่ ทั้งแอปเดสก์ท็อปและส่วนขยาย (จะสร้าง secret gist ให้เองครั้งแรก)
  token เก็บในเครื่องเท่านั้น (settings.json / chrome.storage.local) **ไม่ถูกเขียนลง log** และถูก redact ใน events.jsonl เสมอ

### ส่วนขยาย Chrome v1.1.6
- **แก้ปุ่มดาวน์โหลดของ iframe embed ที่ได้ไฟล์ HTML** — ตอนนี้ระบบจะ resolve หา**ไฟล์วิดีโอที่หน้า player นั้น
  เล่นอยู่จริง** (จากสื่อที่ตรวจพบในแท็บ → แหล่ง `<video>` สด) แล้วดาวน์โหลดไฟล์วิดีโอแทนหน้าเว็บ
- หน้า player ที่**ไม่มีสื่อให้ดาวน์โหลด**จะถูกปฏิเสธพร้อมข้อความแนะนำ (แทนการเซฟ HTML ปลอม ๆ)
- ลิงก์ที่ resolve แล้วเป็นสตรีม HLS/DASH จะแนะนำให้ใช้แอปเดสก์ท็อป (Chrome รวมไฟล์สตรีมไม่ได้)
- Safety net: ถ้าดาวน์โหลดตรงแล้วได้เนื้อหา HTML (content-type) ไฟล์จะถูกลบทิ้งและแจ้งเตือนทันที

[1.2.2]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.2.2

## [1.2.1] — 2026-10-01

### ส่วนขยาย Chrome v1.1.5
- **ส่งออก/นำเข้ารายการเว็บที่ยกเว้นใน popup** — ไฟล์ `vdograbber-exclusions.json` ใช้ร่วมกับ
  แอปเดสก์ท็อปได้ การนำเข้าเป็นแบบผสานและกรองรายการที่ไม่ถูกต้องอัตโนมัติ

### Desktop
- **ส่งออก/นำเข้ารายการเว็บที่ยกเว้น** — ปุ่มในการ์ด "🚫 ยกเว้นเว็บไซต์" ไฟล์ `vdograbber-exclusions.json`
  ใช้ร่วมกับส่วนขยาย Chrome ได้ (ฟอร์แมตเดียวกันทั้งสองแพลตฟอร์ม) การนำเข้าเป็นแบบผสาน (merge) ไม่ซ้ำ
  และกรองรายการที่ไม่ถูกต้องทิ้งก่อนบันทึก
- selftest เฟส exclusions เพิ่ม export→import round-trip (re-import แล้วรายการไม่ซ้ำ + ปฏิเสธรูปแบบเสีย)
  และ unit test ใหม่ `test_exclusion_transfer` (รวม 13 ข้อ) · E2E ส่วนขยาย 40 เช็ค (เฟส bridge export/import)

## [1.2.0] — 2026-10-01

### ส่วนขยาย Chrome v1.1.4
- **ปุ่ม "🚫 ยกเว้นเว็บนี้" บนแผงลอยในหน้าเว็บโดยตรง** — ไม่ต้องเปิด popup อีกต่อไป กดหนึ่งครั้งเพื่อยืนยัน
  (ยืนยันซ้ำภายใน 4 วินาที) แล้วโดเมนปัจจุบันถูกเพิ่มเป็นรายการยกเว้นและแผงหายจากหน้าทันที
- **แก้ matcher รายการยกเว้นให้ตรงกันทั้งสามฝั่ง** (content script / service worker / desktop Python):
  พอร์ตใน URL ไม่มีผลต่อการจับคู่ (`127.0.0.1` ครอบทุกพอร์ต) · ปุ่มยกเว้นใช้ hostname ไม่รวมพอร์ต ·
  `*.domain` ครอบโดเมนหลักด้วย (เหมือนกฎโดเมนเปล่า) — sync กันแบบ 1:1 ทุกฝั่ง

### Desktop v1.2.0
- **ฟีเจอร์ยกเว้นเว็บไซต์บนแอปเดสก์ท็อป** (มาบรรจบกับส่วนขยาย): ตั้งค่าใน Control Center แท็บ ⚙ ตั้งค่า →
  การ์ด "🚫 ยกเว้นเว็บไซต์" — เพิ่ม/ลบรายการได้ เก็บถาวรใน settings.json หน้าที่ยกเว้นจะ**ไม่ถูกฉีดตัวตรวจจับ
 และไม่มีแถบเครื่องมือ** (gate ทั้ง _on_loaded / watchdog / report_media) พร้อม selftest เพิ่มเกณฑ์ใหม่
  (exclusion matcher + Api round-trip + gate) และ unit tests ครอบ matcher 12 ข้อ

## [1.1.9] — 2026-09-30

### เพิ่มใหม่ (Added)
- **E2E บน emulator สำหรับการลบไฟล์** (`DownloadCleanerE2E`) — สร้างไฟล์แบบที่แอปสร้างจริง 3 เส้นทาง:
  publish เข้า MediaStore Downloads/VDOGrabber (เส้นทางเอนจิน yt-dlp), ไฟล์ดิบในโฟลเดอร์ (leftover),
  และงาน DownloadManager จริงที่ดาวน์โหลดจาก loopback HTTP → ลบผ่าน DownloadCleaner →
  ยืนยันไฟล์หายจากดิสก์ + รายการหาย รันอัตโนมัติใน `connectedDebugAndroidTest` ของ CI
- **หน้า changelog.html มีแถบ "release ล่าสุด" แบบ dynamic** — ดึง `/releases/latest` จาก GitHub API
  เหมือน index.html แสดง tag/วันที่/จำนวนไฟล์ + ลิงก์ดาวน์โหลด (timeline เขียนมือยังคงเดิม)

### บำรุงรักษา (Maintenance)
- **Pin CI runner images** — `ubuntu-24.04` (android) และ `windows-2025` (desktop) แทน `-latest`:
  GitHub ประกาศว่า `ubuntu-latest` จะย้ายไป Ubuntu 26 เมื่อ 19 ตุลาคม 2026
  (actions/runner-images#14748) — ปักหมุดภาพที่พิสูจน์แล้วไว้ก่อน แล้วค่อยอัปเกรดเป็นชุด ๆ ที่ทดสอบแล้ว
- **Refactor: `DownloadCleaner`** — ตรรกะ list/ลบของส่วน "ไฟล์ที่ดาวน์โหลดแล้ว" ถูกดึงออกจาก
  MainActivity เป็น object เดียว แยกจุดแตะระบบ (`systemList` / `systemDelete`) ออกจากตรรกะบริสุทธิ์
  (identity priority, dedupe, `identities` count) ทดสอบได้บน JVM — เพิ่ม `DownloadCleanerTest`

[1.1.9]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.9
## [1.1.8] — 2026-09-30

### เพิ่มใหม่ (Added) — จัดการไฟล์ที่ดาวน์โหลดแล้วจาก UI โดยตรง
- **Android: ส่วน "ไฟล์ที่ดาวน์โหลดแล้ว" ในชีต 🎬 พบวิดีโอ** — รวมไฟล์จาก DownloadManager (direct),
  รายการ MediaStore ที่เผยแพร่แล้ว (Q+) และไฟล์จริงใน Downloads/VDOGrabber (dedupe ตามชื่อ,
  เรียงตามวันแก้ไขล่าสุด) ทุกแถวมีปุ่ม "ลบไฟล์" พร้อม dialog ยืนยัน — ลบผ่าน
  DownloadManager.remove() / MediaStore delete (Q+) / File.delete() แล้วแถวหายจากชีตทันที
  (event: `download_deleted` / `download_delete_error`)
- **Desktop: selftest ครอบการลบไฟล์แล้ว** — หลังดาวน์โหลดจริงสำเร็จ ทดสอบ `Api.download_delete_file`
  ต่อทันที: ไฟล์ต้องหายจากดิสก์ + การ์ดต้องหลุดจากรายการ (ขั้น `delete_file`, ไม่ผ่าน = selftest FAIL)

### บำรุงรักษา (Maintenance)
- **CI guard ปิด draft release ถาวร** — ท้าย step "Attach to release" ของทั้ง desktop.yml และ android.yml
  เพิ่ม `gh release edit --draft=false` กันกรณีแท็กถูกลบ/สร้างใหม่แล้ว release กลายเป็น draft อัตโนมัติ
  (เหตุการณ์เดียวกับ v1.1.7)
- **เอกสาร changelog.html** อัปเดตรายการ v1.1.5–v1.1.7 (ก่อนหน้านี้ค้างที่ v1.0.1) พร้อม footer เวอร์ชัน 1.1.8
- ตรวจยืนยันหน้า Pages ดึง release ถูกต้อง — `/releases/latest` คืน tag `v1.1.7` (published, Latest)
  หน้าเว็บจึงแสดงไฟล์ + checksum ถูกต้องโดยไม่ต้องแก้โค้ด
- Downloader เพิ่ม helper `humanSize()` (pure, JVM-tested) สำหรับแสดงขนาดไฟล์ในชีต

[1.1.8]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.8
## [1.1.7] — 2026-09-30

### เพิ่มใหม่ (Added) — ลบไฟล์จากการ์ด done ได้เลย (ทั้งสองแพลตฟอร์ม)
- **Desktop**: ปุ่ม "🗑 ลบไฟล์" บนการ์ดที่เสร็จแล้ว — ยืนยันด้วย dialog ในแอป (pywebview ไม่รองรับ
  window.confirm) แล้วลบไฟล์ออกจากดิสก์จริง พร้อมการ์ดหายจากรายการทันที (Api `download_delete_file`)
- **Android**: แจ้งเตือนเมื่อดาวน์โหลดสตรีมเสร็จ มีปุ่ม "ลบไฟล์" — ลบจาก MediaStore ผ่าน
  DeleteFileActivity (trampoline) พร้อม Toast ยืนยัน
- แก้ Android <10: ตอน publish เข้า MediaStore ต้องใส่ `MediaColumns.DATA` (เส้นทางเต็ม) ด้วย
  มิฉะนั้นไฟล์จะไม่ปรากฏใน Downloads/VDOGrabber

### แก้ไข (Fixed)
- **CI**: E2E emulator step รันใหม่ได้ 1 ครั้งด้วย emulator ใหม่ (fresh boot + hardening options
  `-no-snapshot -camera-back none` ฯลฯ) เพราะบน runner บางครั้ง ADB shell ไม่ตอบสนองตั้งแต่บูต
  (เจอจริงบน tag v1.1.6)
- Downloader (direct downloads บน Android) ตั้งชื่อไฟล์จาก title ของหน้าเว็บแบบเดียวกับเอนจิน
  yt-dlp + desktop แล้ว (sanitize + ใส่นามสกุลให้ครบ) พร้อม JVM unit tests ใหม่
- release v1.1.6: เติมไฟล์ `VDOGrabber-chrome-extension-1.1.2.zip.sha256` ที่ตกหล่น
  และตรวจแล้วว่า checksum ทุกไฟล์ตรงกับของจริง

[1.1.7]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.7
## [1.1.6] — 2026-09-30

### เพิ่มใหม่ (Added) — Desktop: ตั้งชื่อไฟล์ตามหน้าเว็บ + กันชื่อซ้ำ + ล้างรายการ
- **ตั้งชื่อไฟล์ตาม title ของเว็บ** — ปุ่มดาวน์โหลดบนแถบเครื่องมือส่ง `document.title` ให้ backend
  แล้วไฟล์ที่เซฟจะใช้ชื่อหน้าเว็บ (sanitize อักขระต้องห้ามของ Windows แล้ว) แทน `output [output].mp4` —
  ใช้ได้กับลิงก์สตรีมโดยตรง, m3u8/mpd และกรณี fallback จากหน้า player ด้วย
- **ตรวจชื่อไฟล์ซ้ำ** — ถ้ามีไฟล์ชื่อเดียวกันอยู่แล้ว เซฟเป็น `ชื่อ (2).mp4`, `(3)`, … แบบเดียวกับเบราว์เซอร์
  (yt-dlp `--no-overwrites` จะคงอยู่ แต่เราคำนวณชื่อว่างให้ก่อนเริ่มงาน)
- **ปุ่ม "🧹 ล้างรายการ"** ในแท็บดาวน์โหลดของ Control Center — ลบการ์ดที่เสร็จ/ผิดพลาด/ยกเลิกแล้วออกจากรายการ
  (งานที่กำลังดาวน์โหลดไม่ถูกแตะ, ไฟล์บนดิสก์ไม่ถูกลบ) และแท็บ "วิดีโอที่ตรวจพบ" ล้างได้จริงทั้งสองหน้าต่างแล้ว
- **ปุ่ม "📂 โฟลเดอร์"** บนการ์ด done — เปิดโฟลเดอร์ปลายทางของงานนั้น ๆ (คู่กับปุ่ม "เปิดไฟล์")

### Android v1.1.6 (versionCode 6)
- ไฟล์จากเอนจิน yt-dlp ตั้งชื่อตามหน้าเว็บ + dedupe `name (2).mp4` เช่นเดียวกับ desktop (parity)
- แจ้งเตือนเมื่อดาวน์โหลดสตรีมเสร็จ **แตะเปิดไฟล์ได้เลย** (มีปุ่ม "เปิด") ผ่าน content URI จาก MediaStore
- ปุ่ม "ล้างรายการ" ในชีต 🎬 พบวิดีโอ — เคลียร์รายการวิดีโอที่ตรวจพบของเซสชัน

[1.1.6]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.6
## [1.1.5] — 2026-09-29

### แก้ไข (Fixed) — Desktop: การ์ด "done" แสดง 0.0%
- งานที่โหลดผ่าน HLS/DASH/manifest บางตัวไม่เคยรายงานขนาดรวม — เมื่อจบแล้วแอปจะ**อ่านขนาดไฟล์
  จริงจากดิสก์** มาเติมทั้ง `total`/`downloaded` และตั้ง percent = 100% ทำให้การ์ด done
  แสดงความคืบหน้าและขนาดไฟล์จริง (แทน 0.0% · — / —)

### เพิ่มใหม่ (Added)
- **fallback รองรับ player แบบ obfuscated** — สแกน `atob("...")` / base64 blob ในหน้าเว็บ
  ถอดรหัสแล้วหา media URL ข้างใน (unit test `page_fallback_obfuscated`)
- **Android v1.1.5 (versionCode 5)**: เมื่อเอนจินคืน "Unsupported URL" แอปจะ**เปิดหน้านั้นใน WebView
  ให้อัตโนมัติ** — เล่นวิดีโอรอสักครู่แล้วแตะ 🎬 พบวิดีโอ เพื่อดาวน์โหลด (parity กับ desktop v1.1.4)

[1.1.5]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.5
## [1.1.4] — 2026-09-29

### เพิ่มใหม่ (Added) — Desktop: fallback ชั้นที่ 3
- **ปุ่ม "🌐 เปิดหน้านี้ในเบราว์เซอร์"** ในการ์ดดาวน์โหลดที่ error เป็น Unsupported URL —
  คลิกเดียวเปิดหน้านั้นในหน้าต่างเบราว์เซอร์ของแอป เล่นวิดีโอรอสักครู่ แล้วกดปุ่มดาวน์โหลด
  บนแถบเครื่องมือ (ตัวตรวจจับ 4 ชั้นจะคว้า URL จริงขณะวิดีโอเล่น — แม่นกว่าการสแกน HTML
  แบบ static เมื่อหน้าโหลดสตรีมด้วย JS หลายชั้น)
- ปรับข้อความ error ให้สอดคล้องกับ flow ใหม่ (fallback อัตโนมัติ → ปุ่มเปิดหน้า)

[1.1.4]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.4
## [1.1.3] — 2026-09-29

### แก้ไข (Fixed) — Desktop
- **ffprobe หายจาก engine bundle** — ตอนนี้ `ensure_ffmpeg` ดึง **ffmpeg + ffprobe** มาพร้อมกัน
  และ `ffmpeg_path()` นับเฉพาะเมื่อมีครบทั้งคู่ (yt-dlp ใช้ ffprobe ตอน HLS/DASH ผ่าน generic extractor —
  ไม่มีแล้วเจอ "Postprocessing: Conversion failed!") ผู้ใช้เดิมที่มี ffmpeg ค้างไว้จะได้ ffprobe เติมให้อัตโนมัติ
- **fallback ครอบ "Postprocessing" error ด้วย** — หน้า player ที่ชี้ m3u8/mpd จะ retry กับ candidate ใหม่
  แทนการจบด้วย error (ประสบการณ์จริงจาก merrylion2.com player.html)
- **ยืนยัน end-to-end กับสถานการณ์จริง**: หน้า player ที่สร้างสตรีมใน JS → fallback จับ m3u8 →
  โหลด HLS + merge ด้วย ffmpeg/ffprobe → ได้ไฟล์ mp4 สมบูรณ์
- **unit test ใหม่**: `page_fallback_scanner` — ทดสอบ regex ตามโครงสร้างหน้า player จริง (รวมอยู่ใน CI)

[1.1.3]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.3
## [1.1.2] — 2026-09-29

### แก้ไข (Fixed) — Desktop: "Unsupported URL" กับหน้า player
- **วางลิงก์หน้า player (เช่น `player.html`) แล้วโหลดไม่ได้** — ตอนนี้เมื่อ yt-dlp ไม่รู้จัก URL
  แอปจะ**ดึงหน้า HTML มาสแกนเอง**: เจอลิงก์ media (m3u8/mpd/mp4/webm) ซ่อนใน script/attribute →
  ดาวน์โหลดต่อทันที (พร้อม Referer ของหน้าเดิม ช่วยเรื่อง hotlink protection), ถ้าไม่เจอแต่มี
  `<iframe>/<embed>` → ลองกับ URL ของ embed แทน
- **error แบบเข้าใจง่ายใน Control Center** — "Unsupported URL" ถูกแปลงเป็นคำแนะนำภาษาไทย
  (เปิดหน้าในเบราว์เซอร์ของแอปแล้วกดปุ่มดาวน์โหลดบนแถบเครื่องมือ) พร้อม hover ดู error ดิบได้
- ทดสอบ end-to-end จริง: หน้า player จำลองที่ซ่อน mp4 ใน script → fallback ดาวน์โหลดสำเร็จ

[1.1.2]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.2
## [1.1.1] — 2026-09-29

### เพิ่มใหม่ (Added)
- **แยก APK ตามสถาปัตยกรรม (ABI splits)** — arm64-v8a / armeabi-v7a / x86_64 + universal
  ลดขนาดไฟล์ที่ผู้ใช้ดาวน์โหลดเหลือ ~1/3 ของ universal (yt-dlp + ffmpeg เป็น native lib ใหญ่)
  CI แนบทุกตัวลง release พร้อม checksum รวม
- **E2E HLS บน emulator ใน CI** — ffmpeg ใน runner สร้าง m3u8 จริง → เสิร์ฟผ่าน loopback HTTP →
  yt-dlp บนอุปกรณ์ดาวน์โหลด + รวมไฟล์จริง (`StreamEngineTest`) ปิดขั้น 5 ของแผนแบบอัตโนมัติ

### เพิ่มใหม่ (Added) — รวมจาก dependabot
- androidx.appcompat 1.8.0 (#4) · gradle-wrapper 9.8.0 (#10)

[1.1.1]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.1
## [1.1.0] — 2026-09-29

### เพิ่มใหม่ (Added) — Android v1.1.0 (versionCode 3): HLS/DASH ในตัว
- **เอนจิน yt-dlp บนอุปกรณ์** — `io.github.junkfood02.youtubedl-android` (library + ffmpeg) v0.17.3
  (พิกัด/เวอร์ชันเดียวกับที่ Seal ใช้, ตรวจ artifacts บน Maven Central แล้ว)
- **StreamDownloadService** — foreground service (dataSync) ดาวน์โหลด m3u8/mpd/หน้าเว็บที่ yt-dlp รองรับ
  พร้อม notification แสดง % + ปุ่มยกเลิก (destroyProcessById) และ publish ไฟล์เข้า MediaStore Downloads/VDOGrabber
- **tryDownload เลือกเอนจินอัตโนมัติ** — ไฟล์ตรง → DownloadManager เดิม, สตรีม/หน้าเว็บ → yt-dlp engine
  (ลบ dialog "ใช้เดสก์ท็อป" เดิม)
- เพิ่มสิทธิ์ `FOREGROUND_SERVICE` + `FOREGROUND_SERVICE_DATA_SYNC` และประกาศ service ใน manifest
- JVM tests ใหม่: `StreamArgsTest` (output template เดียวกับเดสก์ท็อป, MIME mapping, newest-file)
- **Engine init ตอนเปิดแอป** + **ปุ่มบำรุงรักษาเอนจิน** ในหน้า Logs (ตรวจเวอร์ชัน / อัปเดต yt-dlp ผ่าน `updateYoutubeDL`) — ขั้น 6 ของแผน
- CI: gradle-wrapper 9.8.0 (dependabot #10, update-branch แล้ว CI ผ่าน + merge)

### เดสก์ท็อป v1.1.0 (รีลีสเดียวกัน)
- เลขเวอร์ชัน desktop ขยับเป็น 1.1.0 ให้ตรงกับ Android (APP_VERSION + Windows version resource)
  — ไม่มีการเปลี่ยนพฤติกรรม; rebuild ด้วย dependencies ใหม่ตาม dependabot floors

### เอกสาร
- ตรวจทาน plan-android-hls.md: ยืนยันขั้น 1–3 + 6 ทำแล้วบนโค้ด, เหลือขั้น 5 (ทดสอบบนอุปกรณ์จริง) เป็นลำดับถัดไป

### เพิ่มใหม่ (Added)
- ไดอะแกรม pipeline การตรวจจับ 4 ชั้น (`docs/assets/detection-pipeline.svg`) พร้อมหัวข้อ
  "ตรวจจับวิดีโออย่างไร" ใน README หลักของ repo
- แผนฟีเจอร์ HLS/DASH บน Android ด้วย youtubedl-android ([docs/plan-android-hls.md](plan-android-hls.md))

### บำรุงรักษา (Maintenance) — CI / Repo
- **Branch protection บน main**: ต้องผ่าน check `selftest` (desktop) และ `build` (Android) ก่อน merge เสมอ (strict)
- **Dependabot** สำหรับ pip / gradle / github-actions (รายสัปดาห์) — actions จะไม่ค้างเก่าอีก

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

[1.1.0]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.1.0
[1.0.1]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.0.1
[1.0.0]: https://github.com/NarDecH/VDO_Download_APK/releases/tag/v1.0.0
