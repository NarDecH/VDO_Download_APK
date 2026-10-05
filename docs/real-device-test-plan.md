# แผนทดสอบบนเครื่องจริง — v1.3.6 (ต่อยอดจาก field log ของ v1.3.4/1.3.5)

> ที่มา: log จริงจากผู้ใช้ (Android 16, 23113RKC6G) — 3 ปัญหาคือ engine `done: null`,
> WebView `[sniff] intercept failed` spam และ blob: หลุดเข้าเอนจิน
> เอกสารนี้คือเช็คลิสต์เพื่อยืนยันว่าการแก้ทำงานจริงบนฮาร์ดแวร์ ไม่ใช่แค่ emulator

## 0. เตรียมเครื่อง

1. ติดตั้ง APK จาก release (แนะนำ `VDOGrabber-1.3.6-android-arm64.apk`; universal ก็ได้)
   จากหน้า [Downloads](../index.html) หรือ GitHub Releases
2. ยืนยันเวอร์ชัน: ตั้งค่า → แอป → VDO Grabber → เวอร์ชัน 1.3.6 (versionCode 19)
3. เปิดแอปครั้งแรกแล้วรอ "yt-dlp ready" (log เหตุการณ์ `engine_ready`)

## 1. เก็บ log กลับมาวิเคราะห์

ทำหลังทดสอบทุกเคส (หรือกลางทางถ้ามีข้อสงสัย):

- **ง่ายสุด (v1.4.0):** ในแอปกดปุ่ม Logs (⚙) → **แชร์ log (zip)** → ส่งไฟล์เข้าเมล/แชตตัวเอง
  ได้ทั้ง app.log / downloads.log / events.jsonl / crash.log ในไฟล์เดียว ไม่ต้องต่อสาย
- หรือ **Android Studio → Device Explorer** → `/data/data/com.vdograbber.app/files/logs/`
- หรือผ่าน adb: `adb pull /data/data/com.vdograbber.app/files/logs/ ./logs/`
- วิเคราะห์ events.jsonl ด้วยเครื่องมือใหม่ (ฝั่ง desktop):
  `python scripts/analyze_events.py path/to/events.jsonl`
- กรองเฉพาะเหตุการณ์ที่สนใจ:
  `python scripts/analyze_events.py events.jsonl --event download_no_file --event download_not_media`

## 2. เคสทดสอบ (เรียงตามบทเรียนจาก field log)

### เคส A — หน้า merrylion2 แบบเปิดผ่านแอป (MPD)
1. กดปุ่มคลิปบอร์ด → วาง `https://merrylion2.com/ohudo36m3d/player.html`
2. รอหน้าโหลด → กด 🎬 บนแถบเครื่องมือ
3. **เลือกลิงก์ `…/output.mpd` หรือไฟล์ .mp4 จริง — อย่าเลือก `player.html` หรือ `blob:`**
4. คาดหวัง: toast "ส่งให้เอนจิน" → notification ความคืบหน้า → ไฟล์จริงใน `Downloads/VDOGrabber`
5. ยืนยันใน log: `download_rerouted` → `download_start` → `download_done` (มี filepath)

### เคส B — กดจาก player แล้วได้ blob: (ต้องถูกกรอง)
1. เล่นวิดีโอในหน้าแล้วกด 🎬 → เลือกรายการที่ขึ้นต้น `blob:`
2. คาดหวัง: toast "blob: ใช้ดาวน์โหลดไม่ได้" + ไม่มี notification ดาวน์โหลดหลุดออกไป
3. ยืนยันใน log: event `download_skipped_blob` (และ **ไม่มี** `download_start`)

### เคส C — ลิงก์ .mp4 ปลอมที่เป็น HTML (safety net)
1. ถ้ามีลิงก์ `.mp4` ที่เซิร์ฟคืนหน้า player (เคสรายงานเดิมของ v1.3.4) → กดดาวน์โหลด
2. คาดหวัง: ไฟล์ปลอมถูกตรวจว่าเป็น HTML → ลบทิ้ง → ส่ง URL เดิมให้เอนจินต่ออัตโนมัติ + toast แจ้ง
3. ยืนยันใน log: event `download_not_media` (สังเกต: ก่อน v1.3.5 อีเวนต์นี้ไม่เคยขึ้นเพราะ
   `COLUMN_LOCAL_FILENAME` โดนแพลตฟอร์มห้าม — เคสนี้ยืนยันว่า safety net ทำงานจริงแล้ว)

### เคส D — MPD ตรง ๆ (เอนจิน + work dir)
1. กด 🎬 → เลือกลิงก์ `.mpd` ตรง ๆ จากรายการ
2. คาดหวัง: เอนจินโหลดผ่าน (yt-dlp เจอ formats video+audio ของไซต์นี้แล้ว — ยืนยันจากเดสก์ท็อป)
3. ถ้าล้ม: notification ต้องแสดง**สาเหตุจริง**ของ yt-dlp (เช่น `ERROR: Unsupported URL`)
   ไม่ใช่ stack trace และ log มี event `download_failed`

### เคส E — หน้าที่เอนจิน exit 0 แต่ไม่มีไฟล์ (ถ้ามีโอกาส)
1. หน้า DRM/geo-block หรือโครงสร้างแปลก → กดดาวน์โหลด
2. คาดหวัง: ไม่มี "สำเร็จลอย ๆ" — มี notification/error ระบุสาเหตุ + event
   `download_no_file` และ log มีคำตอบ probe `-F` (`download_no_file_probe` ฝั่ง desktop,
   บรรทัด `[probe]` ใน downloads.log ฝั่ง Android)

### เคส F — ความเสถียรหน้าเว็บทั่วไป
1. เปิดหน้าเสิร์ฟโฆษณาหนัก ๆ / เปลี่ยนหน้าบ่อย ๆ 30 วินาที
2. คาดหวัง: **ไม่มี** spam `[sniff] intercept failed: A WebView method was called on
   thread 'ThreadPoolForeg'` ใน app.log (แก้ด้วยการย้ายการอ่าน title/url ขึ้น main thread)

## 3. เกณฑ์ผ่านทั้งชุด

- เคส A/D ได้ไฟล์วิดีโอเปิดเล่นได้จริง, เคส B/C ถูกจับที่ event ที่กำหนด
- events.jsonl ไม่มี `download_start` ที่ไม่มี `download_done`/`download_failed` ตามมา
- ไม่มี stack trace ของ WebView ใน app.log ระหว่างทดสอบ
