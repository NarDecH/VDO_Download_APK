# AGENT.md — คู่มือสำหรับ AI Agent / ผู้ดูแลที่ทำงานใน repo นี้

> ไฟล์นี้คือ context pack สำหรับ AI coding agent (ZCode/Copilot/Claude ฯลฯ) และมนุษย์ใหม่
> อ่านจบแล้วควรทำงานต่อได้ทันทีโดยไม่ต้องถามซ้ำ

## โปรเจกต์นี้คืออะไร

**VDO Grabber** — แอปเปิดหน้าเว็บแล้วมีปุ่มดาวน์โหลดวิดีโอที่กำลังแสดงอยู่ มี 2 เป้าหมาย build:

1. **Windows Desktop** (`app/`) — Python + pywebview (Edge WebView2) + yt-dlp/ffmpeg → แพ็กไฟล์เดียวด้วย PyInstaller
2. **Android** (`android/`) — Kotlin + WebView + DownloadManager → build ผ่าน GitHub Actions (`.github/workflows/android.yml`)

ต้นแบบทางเทคนิคคือส่วนขยาย Chrome "Video Download Helper" ที่เก็บไว้ใน `code/` (minified, ใช้ศึกษาเท่านั้น,
**ถูก .gitignore — ห้าม commit**) วิเคราะห์อย่างละเอียดแล้วใน `docs/research.md`

## แผนที่ repo

```
app/
  main.py            จุดเข้า: App + Api bridge (js_api) + selftest mode (--selftest)
  core/detector.py   DETECT_JS/TOOLBAR_JS — สคริปต์ฉีดในหน้าเว็บ (หัวใจของการตรวจจับ)
  core/downloader.py DownloadManager — คิว/ความคืบหน้า/ยกเลิก + parser ของ yt-dlp
  core/ytdlp_mgr.py  จัดการ yt-dlp.exe/ffmpeg.exe (bundle → %APPDATA%, อัปเดตได้)
  core/logger.py     LogManager: app.log / downloads.log / events.jsonl + export diagnostics
  core/settings.py   settings.json (%APPDATA%/VDOGrabber)
  ui/index.html      Control Center (มี ?demo=1 สำหรับถ่ายภาพหน้าจอเอกสาร)
  tests/testpage.html หน้าทดสอบ detection (เสิร์ฟผ่าน local server ใน --selftest)
android/             โปรเจกต์ Gradle มาตรฐาน (AGP 8.7.3, Kotlin 2.0.21, minSdk 26, target 35)
  .../MainActivity.kt   WebView + Bridge + shouldInterceptRequest + bottom sheet
  .../Detector.kt       INJECT_JS — ฉบับ Android ของตัวตรวจจับ
  .../FileLog.kt        ระบบ log สามไฟล์เดียวกับฝั่ง desktop
scripts/             make_icon.py (Pillow) · build_exe.py (PyInstaller) · version_info.txt
docs/                readme/research/changelog (md + html) · index.html = หน้าดาวน์โหลด (Pages)
                     assets/ (สกรีนช็อต, SVG ไดอะแกรม, demo pages)
.github/workflows/android.yml   ปั่น APK และแนบ release + checksum
code/                (gitignored) ซอร์สส่วนขยายต้นแบบ
```

## คำสั่งสำคัญ

```bash
python app/main.py --selftest     # ต้องผ่าน (exit 0) ก่อน build/commit ที่แตะ app/
python scripts/build_exe.py       # dist/VDOGrabber.exe (~32MB) แล้วทดสอบ exe: ./dist/VDOGrabber.exe --selftest
python scripts/make_icon.py       # สร้าง assets/icon.ico + docs/assets/logo.png
cd android && ./gradlew assembleDebug   # หรือปล่อยให้ CI ทำ
```

## กติกาการแก้โค้ด

- **Api (app/main.py) ต้องเป็น method-only class** — ห้ามเก็บ object ที่อ้างกลับถึง App/webview Window
  เป็น attribute เพราะ pywebview 6 เดิน attribute graph แบบ recursive แล้วชน RecursionError (ใช้ global `_APP`)
- ตัวตรวจจับ (DETECT_JS และ Detector.INJECT_JS) ต้อง maintain เป็นคู่ — แก้ฝั่งหนึ่งให้ sync อีกฝั่ง
- ทุกฟีเจอร์ใหม่ต้อง log ผ่าน LogManager (`logm.log(..., event=...)` / `FileLog.event(...)` บน Android)
  ใช้ชื่อ event แบบ snake_case: `media_found`, `download_start`, `download_done`, `page_loaded` …
- pywebview: `webview.start()` บล็อกจน windows ถูก destroy — โค้ดทดสอบต้อง destroy เอง (ดู selftest)
- ห้ามแตะ `code/` เพียงแต่อ่าน; ห้าม commit เนื้อหาของมัน
- exe: ไฟล์ data ใหม่ต้องเพิ่ม `--add-data` ใน `scripts/build_exe.py` และเข้าถึงผ่าน `sys._MEIPASS` (ดู `bundled_dir()`)
- UI เป็นภาษาไทยหลัก, โค้ด/comment เป็นอังกฤษ, log เป็นอังกฤษ

## การ release (vX.Y.Z)

1. แก้เวอร์ชัน: `app/core/logger.py` (APP_VERSION), `scripts/version_info.txt`, `android/app/build.gradle.kts` (versionName/versionCode), `docs/changelog.md`
2. `python scripts/build_exe.py` → ทดสอบ `./dist/VDOGrabber.exe --selftest` (ต้อง PASS)
3. `git tag vX.Y.Z && git push origin vX.Y.Z` → CI ปั่น APK แล้วแนบ release อัตโนมัติ (พร้อมอัปเดต checksums.txt)
4. อัปโหลด `dist/VDOGrabber-X.Y.Z-windows-x64.exe` + `checksums.txt` (sha256sum) ด้วย `gh release upload`
5. หน้า Pages (docs/index.html) จะดึง release ใหม่เองผ่าน GitHub API

## เคล็ดลับดีบัก

- โครงสร้าง log: `%APPDATA%\VDOGrabber\logs\{app.log, downloads.log, events.jsonl}` (+ `crash.log` เมื่อ selftest ล้ม)
- Android: Android Studio → Device Explorer → `/data/data/com.vdograbber.app/files/logs/`
- หน้าจอเอกสาร: เสิร์ฟ repo ด้วย `python -m http.server` แล้วเปิด `app/ui/index.html?demo=1`,
  `docs/assets/demo-toolbar.html`, `docs/assets/demo-android.html`
- events.jsonl อ่านด้วย `jq -c 'select(.event=="media_found")' events.jsonl`
