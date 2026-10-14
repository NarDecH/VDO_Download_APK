# Release report — v1.9.16 (สรุปข้อเท็จจริง 2026-10-14)

เอกสารปิดหลังปล่อย v1.9.16 และทดสอบสายทางอัปเดตจริงผ่านครบวงจร
ที่มา: โจทย์เริ่มต้นคือทดสอบ flow อัปเดตจริง "เปิด 1.9.12 กดปุ่มตรวจอัปเดต ติดตั้งรุ่นใหม่" — การทดสอบเปิดเผยบั๊ก restart ซ้อนหลายชั้น จึงแก้และปล่อยรุ่นต่อถึง 1.9.16 (รุ่นคั่นเพื่อให้ 1.9.15 ที่แก้แล้วเป็นผู้สร้าง `.bat` เอง)

## 1) Asset ของ release v1.9.16 และ checksums ตรงจริง

ดึงไฟล์กลับจาก release มาแฮชซ้ำ (`sha256sum`) — ตรงทั้งหมดเทียบ `checksums.txt`:

| ไฟล์ | sha256 (จากไฟล์จริง = ค่าใน checksums.txt) |
|---|---|
| VDOGrabber-1.9.16-windows-x64.exe (29 MB) | `2ba5c81ceee4a9d1bea628c5e622412b52b83f56f29edef0aa30ce0da8d1d119` |
| VDOGrabber-1.9.16-android-arm64.apk | `d13b1d93b3e13a5effc9b819b95406c8bd56bb18c3b767244fd4f47914dad4f7` |
| VDOGrabber-1.9.16-android-armv7.apk | `a2eb2f7709226b3be9f3bf1c63f6ed1aaca88dbdea36b82f7e7f30276a4eb7256358` |
| VDOGrabber-1.9.16-android-universal.apk | `ac9dd2e9442759384a1c91ac701c18b1f757a08b4255bfa047af8035e9b42ae3` |
| VDOGrabber-1.9.16-android-x86_64.apk | `d2879584bcbc712070ae4d2a9e223148f24f32b889378dae3aae4c6a80cdb19c` |

ตรวจสอบอัตโนมัติได้ด้วย `python scripts/regen_checksums.py v1.9.16 --verify`

## 2) สาเหตุจากการตรวจ checksums รุ่นเก่า (ตารางเทียบไฟล์จริง)

`.sha256` รายไฟล์ (ตัวที่ updater ใช้ verify) ตรงทุกรุ่น — แต่ `checksums.txt` แถว exe ของ 3 รุ่นแรก **ค่าค้างจาก build เก่า** เพราะมีการอัปโหลด exe ทับด้วย build ใหม่ภายหลัง CI merge ครั้งเดียว แล้วไม่มีใครรวมใหม่อีก:

| รุ่น | exe ที่เปิดได้จาก release | checksums.txt เดิม | แก้แล้ว (2026-10-14) |
|---|---|---|---|
| 1.9.13 | `70905b28…205475d` | `a8f5d84e…` ❌ | ✅ อัปโหลดใหม่ตรงไฟล์จริง |
| 1.9.14 | `c02ed2bb…dbf18e81` | `2b8cd3df…` ❌ | ✅ อัปโหลดใหม่ตรงไฟล์จริง |
| 1.9.15 | `02c979de…32e654c4` | `216861de…` ❌ | ✅ อัปโหลดใหม่ตรงไฟล์จริง |
| 1.9.16 | `2ba5c81c…3b3b50cb` | `2ba5c81c…` ✅ (CI ปั่นเองครบ) | — |

- เครื่องมือใหม่: [scripts/regen_checksums.py](../scripts/regen_checksums.py) — สร้าง checksums.txt จาก asset จริงทุกไฟล์ มีโหมด `--verify`}
- แก้ต้นเหตุรอบหน้า: CI (desktop.yml/android.yml) เพิ่ม step ตรวจแถวที่ตัวเองอัปโหลดเทียบ `digest` ฝั่ง server หลัง merge — แถว stale = fail งาน

## 3) E2E สายอัปเดตทั้งหมดผ่าน (round 4 ชี้ขาด)

| รอบ | สายทาง | ผล |
|---|---|---|
| 1–2 | 1.9.12→1.9.13 (กดปุ่มจริง) / 1.9.13→1.9.14 | swap ทำงาน แต่ restart ติด "Failed to load Python DLL" → รากเหง้า: `.bat` ที่ copy ทับ running exe + ลงและ `_MEIPASS2`/`_PYI_*` รั่วทำ bootloader ใหม่ข้าม extract |
| 3 | 1.9.14→1.9.15 | ยังพังเชิงกลไก: `.bat` ตัวจริงยังมาจาก 1.9.14 (รุ่นเก่า) — จึงปล่อย 1.9.16 คั่นเพื่อให้ 1.9.15 สร้าง `.bat` ที่แก้แล้ว |
| 4 | 1.9.15→1.9.16 | ✅ **ผ่านครบ**: download 29 MB verified sha → apply → app_exit → `app_start version="1.9.16"` ใหม่เปิดสะอาด ไม่มี update_error |

log events ตอน round 4 (events.jsonl): `update_check → update_available (1.9.15→1.9.16) → update_downloading → update_downloaded (sha256=2ba5c81c… verified) → update_apply + update_apply_script → app_exit → app_start 1.9.16 → page_loaded → engine_ready`

## 4) ข้อจำกัดที่ยืนยันไม่ได้จากเครื่องนี้

- CI guard ที่เพิ่ม (Verify checksums.txt lines) จะเดินจริงในงาน tag ถัดไป — ยังไม่มี run จริงให้ชี้ตาม
- CDN ของ GitHub อาจเสิร์ฟไบต์ checksums.txt เก่าอีกช่วงสั้น ๆ หลัง `--clobber` (เจอขณะ re-verify ทันที ได้ผลในรอบสอง)
