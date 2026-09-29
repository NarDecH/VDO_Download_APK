# Session Notes — 2026-09-29

## เริ่มจาก: "ตรวจสอบโปรเจกต์นี้" → จบด้วย release v1.0.1 + Android v1.1.0 (HLS ในตัว)

## เพิ่มเติมรอบหลัง (v1.1.1 → v1.1.4)

- **v1.1.1**: ABI splits (arm64 = 37MB จาก 126MB, -69%) + E2E HLS บน emulator ใน CI
  (StreamEngineTest) — gotchas: emulator-runner script ต้องใช้ absolute path,
  `com.sun.net.httpserver` ไม่มีบน Android (เขียน raw ServerSocket แทน),
  และ `jniLibs.useLegacyPackaging = true` **จำเป็น** ไม่งั้น YoutubeDL.init พัง
- **v1.1.2–v1.1.4 (desktop)**: แก้ "Unsupported URL" จากรายงานผู้ใช้ (merrylion2 player.html):
  ① fallback สแกน HTML หา media URL ซ่อนใน script → ดาวน์โหลดต่อ (พร้อม Referer)
  ② **ffprobe** ต้องมาคู่ ffmpeg (yt-dlp ใช้ probe ตอน generic extractor) — `ffmpeg_path()`
  นับเฉพาะเมื่อมีครบคู่ + ensure_ffmpeg เติม ffprobe ให้เครื่องเดิม
  ③ ปุ่ม "เปิดหน้านี้ในเบราว์เซอร์" ในการ์ด error — flow ผู้ใช้ปิดที่ตัวตรวจจับ 4 ชั้น
  ④ unit test `page_fallback_scanner` ตามโครงสร้างหน้าจริง
- E2E พิสูจน์แล้ว: player.html → fallback m3u8 → HLS merge → mp4 done
- dependabot: merged #3 #4 #7 #10 (pillow/websocket/appcompat/gradle-wrapper),
  ปิด major PRs (#1 #2 #9 → issue #11 roadmap)
- ค้าง: emulator E2E ใช้ fixture HLS ที่สร้างใน CI ก่อน build — ต้องรัน ffmpeg step ก่อน
  connectedAndroidTest เสมอ (ตาม workflow ปัจจุบันแล้วถูกต้อง)

## รอบ v1.1.5 (รายงานผู้ใช้: การ์ด done แสดง 0.0%)
- การ์ด done แสดง `0.0% · — / —` เพราะ HLS/DASH fragment downloads ไม่เคยรายงาน total
  → เมื่อจบแล้วอ่านขนาดจากดิสก์เติม total/downloaded + percent=100 (downloader.py)
- fallback เพิ่มชั้นถอด `atob("...")`/base64 obfuscated player URLs (test: page_fallback_obfuscated)
- Android parity: engine คืน Unsupported URL → broadcast → MainActivity เปิดหน้าใน WebView
  (registerReceiver ต้องใส่ RECEIVER_NOT_EXPORTED บน targetSdk 34+)
- releases v1.1.2→v1.1.5 ทั้งหมดแนบ extension zip 1.1.2 พร้อม checksum รวม (แฮชเดียวกันทุก release)

## รอบ v1.1.6 (คำขอผู้ใช้: ชื่อไฟล์ตามเว็บ + กันซ้ำ + ปุ่มเคลียร์รายการ)
- **title-based filenames**: TOOLBAR_JS ส่ง `document.title` เข้า `download_start` (ทั้งปุ่มดาวน์โหลด
  และปุ่มเลือก format) → `_decide_title_base()` เลือกใช้เฉพาะลิงก์ media/manifest (hls/dash);
  งานชนิด page/embed คง naming ของ yt-dlp เพราะ title ที่ได้เป็นชื่อเว็บ ไม่ใช่ชื่อวิดีโอ
- `_out_template()` ใช้ literal stem (escape `%`→`%%`) + `--no-overwrites`; dedupe คำนวณชื่อว่าง
  ล่วงหน้าด้วย `unique_stem()` (`name (2).ext`) — yt-dlp เขียนลงไฟล์ว่างเสมอ จึงไม่เจอ "has already been downloaded"
- `_guess_output_file` เพิ่มชั้น `_newest_match()` หาไฟล์ `stem.ext`/`stem (n).ext` ใหม่สุดใน out_dir
  (ครอบกรณี yt-dlp ไม่พิมพ์ path ตรงรูปที่ parse ได้)
- **clear list**: Api ใหม่ `downloads_clear()` (ลบเฉพาะ job done/error/canceled ออกจาก history+jobs —
  งานที่รันอยู่ไม่แตะ, ไฟล์ไม่ลบ) + `media_clear()` (MediaStore.clear() จริง); UI มีปุ่ม 🧹 ทั้งสองแท็บ
  + event sync `downloads_cleared`/`media_cleared` ให้ Control Center อัปเดตทันที
- คำแนะนำค้างจากรอบก่อนที่ทำแล้วในรอบนี้: ปุ่ม "📂 โฟลเดอร์" ต่อการ์ด done (desktop — Api มี
  `download_open_folder` อยู่แล้วแต่ UI ไม่เคยมีปุ่ม), Android notification เสร็จแตะเปิดไฟล์ได้
  (content URI จาก MediaStore + action "เปิด"; ไม่ใช้ FileProvider เพราะไฟล์ raw โดน delete หลัง publish)
- Android parity: `StreamArgs.sanitizeFilename/uniqueFileName/outputTemplate(dir,titleBase)` (JVM-testable)
- unit tests 7→11 (sanitize/unique_stem/title_base_policy/out_template) + JVM 4 เคสใหม่;
  **gotcha**: console บนเครื่องนี้เป็น cp1252 — รัน test ต้อง `PYTHONIOENCODING=utf-8` ไม่งั้น
  UnicodeEncodeError ตอนพิมพ์ชื่อไฟล์ไทย (ตัว test เองไม่ผิด)
- CI: Android run แรกบน tag พังที่ E2E emulator (`ShellCommandUnresponsiveException` — infra flake)
  → `gh run rerun --failed` ผ่านปกติ; Desktop ผ่านครั้งแรก

### สิ่งที่ตรวจพบและแก้ในรอบแรก (v1.0.1)
| ปัญหา | การแก้ |
|---|---|
| `watch_fixed.py` ไฟล์ขยะติดใน git | ลบ (`exe_stderr.log` ด้วย) |
| เวอร์ชันไม่ตรง (desktop 1.0.1 vs Android 1.0.0, filevers=1.0.0) | sync ทุกจุด → 1.0.1 (Android versionCode 2) |
| Detector.kt ไม่มี iframe scan (คู่ detector เหลื่อมกัน) | เพิ่ม scanIframes + ปุ่มเปิดหน้า embed |
| `WATCHDOG_JS` โค้ดตาย | ลบออกจาก detector.py |
| MediaStore FIFO / jobs ไม่ prune | LRU จริงทั้งสองแพลตฟอร์ม + `_prune_history_locked` |

### Release v1.0.1 (commit 80ff447, tag v1.0.1)
- APK + exe + extension zip ครบพร้อม `.sha256` และ `checksums.txt` รวม 3 ไฟล์
- Release notes แต่งเต็มรูปแบบ, หน้า Pages แสดงถูกต้อง (ตรวจด้วย browser จริง)
- ตัวเลือกเวอร์ชัน: ผู้ใช้เลือก v1.0.1 เพราะ v1.0.1 เดิม**ไม่เคยถูก tag** บน remote

### กลไกป้องกัน regression ที่เพิ่มขึ้น
- **pair_sync ใน selftest** (app/core/detector.py): parse `INJECT_JS` จาก Detector.kt ตรง ๆ,
  ตรวจ JS syntax (`node --check` / structural fallback) + feature markers ให้ตรง desktop
  — ข้ามอัตโนมัติเมื่อรันจาก frozen exe — **จับบั๊กได้จริง 2 ครั้งตอนแก้ไฟล์ครั้งแรก**
- **Unit tests**: `scripts/test_units.py` (desktop, 5 เคส) + `MediaStoreTest.kt` / `StreamArgsTest.kt` (Android JVM)
- **CI**: desktop.yml (selftest + unit tests + แนบ exe เมื่อ tag), android.yml (JVM tests ก่อน build APK)
- **Actions อัปเกรด**: checkout@v7, setup-python@v7, setup-java@v6, setup-gradle@v6, upload-artifact@v7
- **Branch protection**: main ต้องผ่าน `selftest` + `build` (strict) — ตั้งผ่าน gh api
- **Dependabot**: pip + gradle + github-actions รายสัปดาห์

### Android v1.1.0 — HLS/DASH ในตัว (docs/plan-android-hls.md ขั้น 1–3)
- `io.github.junkfood02.youtubedl-android` library+ffmpeg **0.17.3** (พิกัดจาก Seal, เช็ค artifacts บน Maven Central แล้ว — JitPack สำหรับ yausername 0.18.1 ไม่มี artifact, Maven Central ของ fork มีแค่ 0.16.0-alpha)
- `StreamDownloadService`: foreground dataSync + progress notification + ยกเลิก (pid สร้างเอง → `destroyProcessById`) + publish เข้า MediaStore Downloads/VDOGrabber
- `tryDownload` เลือกเอนจินอัตโนมัติ (ไฟล์ตรง → DownloadManager, สตรีม → yt-dlp)
- **บั๊กที่เจอระหว่างทาง (CI จับหมด)**: ① package จริงคือ `com.yausername.youtubedl_android` ไม่ใช่ `youtubedl` ② callback line เป็น nullable ③ assertion ใน StreamArgsTest เขียนผิดเอง 2 เคส — แก้ทั้งหมด, CI เขียว

### ข้อควรระวังที่พบ (ไว้อ่านก่อนทำต่อ)
- `$m.url` ใน Kotlin string template interpolate แค่ `m` — ต้อง `${m.url}`
- Escape `${'$'}` ใน raw string Kotlin หายได้ถ้าเขียนไฟล์ใหม่ทั้งไฟล์ — เช็คหลังเขียนเสมอ
- pywebview 6: อย่าเก็บ back-ref ของ App ใน Api (ดู AGENTS.md)
- dependabot squash-merge ทำให้ push local ชน → rebase แล้วเก็บ floor สูงสุด

### สถานะ PR dependabot (ปลายเซสชัน)
- ✅ merged: #3 (Pillow 12.3), #7 (websocket 1.9.2) · floor แทน: #5 (pywebview 6.2.1), #6 (pyinstaller 6.22.3) — commit 69d312b
- ❌ closed: #8 (upload-artifact — เราอัปเกรด v7 เองแล้ว)
- ⏳ เปิดค้าง (major jump ทั้งชุด ต้องแยก branch ทดสอบ): #1 core-ktx 1.19, #2 AGP 9.4, #4 appcompat 1.8, #9 Kotlin 2.4.20, #10 gradle-wrapper 9.8

### ค้างสำหรับเซสชันหน้า
- Android versionName ยังเป็น 1.0.1 ใน build.gradle.kts (โค้ด HLS คือ "1.1.0" ตาม changelog — bump เมื่อ release จริง + versionCode 3)
- HLS MVP ยังไม่ทดสอบบนอุปกรณ์จริง (ดาวน์โหลด m3u8 จริง + updateMe ปุ่มอัปเดตเอนจิน ตาม plan ขั้น 5–6)
- Strikethrough หมายเหตุ: `stream_title`/`stream_msg` strings เดิมยังอยู่ใน strings.xml (ไม่ถูกเรียกแล้ว — ลบได้)

### คำสั่งเดิมที่ใช้บ่อย
```bash
python app/main.py --selftest          # ต้อง PASS ก่อน commit ที่แตะ app/
python scripts/test_units.py           # unit tests desktop (headless)
python scripts/build_exe.py && ./dist/VDOGrabber.exe --selftest
cd android && ./gradlew testDebugUnitTest assembleDebug   # บนเครื่องที่มี SDK
```
