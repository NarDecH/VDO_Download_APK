# แผนฟีเจอร์: ดาวน์โหลด HLS/DASH ในตัวบน Android (youtubedl-android)

> สถานะ: **Draft / roadmap** — ยังไม่ได้ลงมือ implement (ฝั่ง Android ปัจจุบันรองรับเฉพาะไฟล์ตรง
> mp4/webm/mp3 ผ่าน DownloadManager; m3u8/mpd แนะนำให้ใช้เดสก์ท็อป — ดู `Downloader.isStream()`)

## ทำไมต้องทำ

ตอนนี้ Android เจอสตรีม HLS/DASH แล้วบล็อกพร้อมข้อความ "ใช้เดสก์ท็อป" — เป็นข้อจำกัดที่ผู้ใช้
มือถือล้วนเจอบ่อยสุด ตัวเลือกเอนจินมี 3 ทาง:

| ทางเลือก | ข้อดี | ข้อเสีย | คำตัดสิน |
|---|---|---|---|
| **youtubedl-android** (yausername/JunkFood02) | สกัดทุกไซต์เหมือนเดสก์ท็อป (ตัวเดียวกับ yt-dlp) + ffmpeg รวมในไลบรารี, มี UI library สำเร็จ | APK โตขึ้น ~50–80 MB, ต้อง split APK สถาปัตยกรรม | ✅ เลือกใช้ |
| ExoPlayer + custom DataSource บันทึกเอง | ควบคุมละเอียด, ไม่พึ่ง python runtime | ต้องเขียน demux/merge เอง, รองรับแค่ HLS ที่ไม่เข้ารหัส, บำรุงรักษาหนัก | ❌ |
| ส่งงานไปเดสก์ท็อป (สถานะเดิม) | ต้นทุนศูนย์ | ไม่ใช่ฟีเจอร์ | ❌ |

## สถาปัตยกรรมเป้าหมาย

```
Detector.INJECT_JS (ไม่แตะ — คู่กับ DETECT_JS ตาม pair_sync)
        │  media_found (kind = m3u8/mpd)
        ▼
MainActivity.tryDownload()
        │  isStream(url) == true เดิม → แทนที่ dialog "ใช้เดสก์ท็อป"
        ▼
StreamDownloadService (Foreground service, ใหม่)
        │  youtubedl-android: YoutubeDl.getInstance().updateMe +
        │  requestBuilder(...)
        ▼
  progress → notification (เหมือน DownloadManager ปัจจุบัน)
  เสร็จ → MediaScanner.scanFile → โฟลเดอร์ Downloads/VDOGrabber
        │
        └─ FileLog.event("download_start/done/error") — รูปแบบเดิม
```

ข้อดีคือ **ไม่แตะตัวตรวจจับเลย** — ทุกอย่างเกิดหลังจุด `tryDownload()` ทำให้ pair_sync guard
ยังคุ้มครอบ detector คู่เหมือนเดิม

## ขั้นตอน implement

1. **เพิ่ม dependency** (gradle): `io.github.junkfood02.youtubedl-android:library:<latest>` +
   `:ffmpeg` + `:dialog` (version จะ pin หลังเช็ค release ล่าสุด — dependabot จะช่วยติดตามต่อ)
2. **`StreamDownloadService`** — foreground service ชนิด `dataSync`, notification มี progress bar
   + ปุ่มยกเลิก (ยกเลิก = `YoutubeDL.getInstance().destroyProcessById()`)
3. **Map จาก `MediaStore.Item` → request**: ใช้ `url`, `title` ที่มีอยู่แล้ว; `kind` m3u8/mpd ผ่านตรง ๆ
4. **สิทธิ์**: ไม่เพิ่ม — ยังเขียนลง `Downloads/` ผ่าน MediaStore API เดิม (targetSdk 35)
5. **ทดสอบ**:
   - JVM: unit test การ map item → command args (ไม่ต้องเครือข่าย)
   - หน้าทดสอบจริง: เสิร์ฟ m3u8 จาก local server เหมือน `app/tests/testpage.html`
   - E2E: ดาวน์โหลด m3u8 จริงบน emulator แล้วเทียบขนาดไฟล์
6. **ไซส์ APK**: เช็ค diff ขนาดก่อน/หลัง ถ้าโตเกิน ~60 MB พิจารณาเปิด ABI splits
   (`splits { abi { enable true; universalApk false } }`)

## ความเสี่ยง

- **ไลบรารีอัปเดตตาม yt-dlp**: เวอร์ชัน yt-dlp ฝังในไลบรารีอาจเก่ากว่า exe เดสก์ท็อป — ต้องมี
  ปุ่ม "อัปเดตเอนจิน" ใน LogsActivity (มี API `updateMe` ให้พร้อม)
- **APK โต**: ดูข้อ 6
- **DRM**: ยังไม่รองรับเหมือนเดิม (Widevine ไม่อยู่ในขอบเขต)

## ประมาณความพยายาม

~2–3 วันทำงาน สำหรับ MVP (ดาวน์โหลด m3u8 + progress + ยกเลิก), อีก 1 วันสำหรับ DASH/mpd
และการจัดการ error ครบ
