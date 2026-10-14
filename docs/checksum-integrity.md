# ความสมบูรณ์ของ checksum — ระบบ 3 ชั้น

เอกสารอธิบายกลไกที่รับประกันว่า `checksums.txt` บน GitHub Releases ของโปรเจกต์นี้
"ตรงไฟล์จริงทุกแถว ทุกรุ่น" เสมอ — และเรียนรู้จากเคสจริง (issue [#19](https://github.com/NarDecH/VDO_Download_APK/issues/19))

## ทำไมต้องมีระบบนี้

`checksums.txt` คือสิ่งที่ผู้ใช้ (และ updater ในแอป) ใช้ยืนยันว่า "ไฟล์ที่โหลด = ไฟล์ที่ทีม build"。
บั๊กชนิดหนึ่งที่พบจริง: เมื่อ asset ถูกอัปโหลดทับ (`--clobber`) **นอก** ขั้นตอน CI
ไฟล์ `checksums.txt` ที่ CI เคย merge ไว้ยังเก็บ **ค่าแฮชของ build เก่า** อยู่เงียบ ๆ
— ผู้ใช้ที่ตรวจตาม checksums.txt จะได้ "ไม่ตรง" ทั้งที่ไฟล์จริงถูกต้อง

เคสจริงที่พบครั้งแรก (v1.9.15 audit): แถว exe ของ release 1.9.13/14/15 เป็นค่าเก่า
ทั้งหมด ต่อมา audit รอบแรก (issue #19) เผิงเจอรุ่นเก่ากว่านั้นอีก 4 รุ่น (1.9.5/10/11/12)

## ชั้นที่ 1 — Guard ตอน release (ป้องกันไม่ให้เกิด)

[desktop.yml](../.github/workflows/desktop.yml) / [android.yml](../.github/workflows/android.yml)
มี step **"Verify checksums.txt lines (tags only)"** หลังอัปโหลด asset ทุกครั้ง:
เทียบแถวที่ job นั้นเพิ่ง merge กับ `digest` ฝั่ง server ของ asset (GitHub คำนวณจากไบต์จริงที่เก็บ)
— ไม่ตรง = fail งานทันที ก่อนมีใครได้ใช้ checksums.txt ผิด

*พิสูจน์ in-flight แล้วบน tag v1.9.17: log แสดง `checksums.txt line OK` ครบ 5 แถว (exe + APK 4 ตัว)*

## ชั้นที่ 2 — Audit รายวัน (จับสิ่งที่หลุดผ่านชั้น 1)

[checksum-audit.yml](../.github/workflows/checksum-audit.yml) รันทุกวัน 02:20 UTC
(หรือกดเองผ่าน workflow_dispatch): ไล่ทุก release ที่ published, ดึง `checksums.txt`
แล้วเทียบทุกแถวกับ digest ฝั่ง server ของ asset ทุกไฟล์ (ไม่ต้องดาวน์โหลด asset จริง)

- เจอแถว STALE / MISSING / checksums.txt หาย = **เปิด issue พร้อมรายละเอียด + fail run**
- ถ้ามี issue ของ audit เปิดอยู่แล้ว จะ **comment ต่อใน issue เดิม** (ไม่สร้างซ้ำทุกวัน)
- ไฟล์ `.sha256` (sidecar รายไฟล์) ถูกยกเว้นโดยเจตนา — มันไม่ได้อยู่ใน checksums.txt ตาม convention

## ชั้นที่ 3 — เครื่องมือแก้ย้อนหลัง (regen)

[scripts/regen_checksums.py](../scripts/regen_checksums.py):

```bash
python scripts/regen_checksums.py v1.9.15 --verify          # ตรวจอย่างเดียว
python scripts/regen_checksums.py v1.9.15                    # ดาวน์โหลด asset จริงมาแฮช → อัปโหลดทับ
python scripts/regen_checksums.py v1.9.5 --from-digests      # fast path: ใช้ digest ฝั่ง server ไม่โหลด asset
```

`--from-digests` ใช้ได้เมื่อ asset นั้นมี `digest` ฝั่ง API (asset ที่อัปโหลดยุคหลัง) —
เร็วมากเพราะไม่ต้องโหลด APK 40–120 MB; ถ้า asset ไหนไม่มี digest จะ raise ให้ใช้ slow path แทน

## Case study: issue #19 (2026-10-14)

รอบแรกของ audit พบ **58 findings** ใน 30 releases — แยกได้เป็น:

| ประเภท | จำนวน | สาเหตุ | การแก้ |
|---|---|---|---|
| แถว exe STALE จริง (1.9.5/10/11/12) | 4 | asset ถูก re-upload นอก CI ตอนอดีต | regen ใหม่จาก digest จริง |
| "`.sha256` has no line" | 54 | blind spot ของ audit script เอง (sidecar ไม่ควรมีแถว) | filter ในสคริปต์ |

หลังแก้ทั้งสองด้าน — audit รันใหม่เขียว 0 finding และปิด issue

## บทเรียนที่ฝังอยู่ในระบบปัจจุบัน

1. **อย่าเชื่อ checksums.txt ที่ merge ครั้งเดียว** — ต้อง verify เทียบ digest ตอน release (ชั้น 1)
2. **อย่าเชื่อว่า "ครั้งเดียวจบ"** — audit รายวันจับเคสรุ่นเก่าที่แก้ไม่ครบ (ชั้น 2)
3. **การแก้ต้องจากแหล่งความจริงเดียว** — digest ฝั่ง server ของไบต์ที่เก็บจริง (ชั้น 3)
4. **audit script ก็พลาดได้** — ผล negative ต้องอ่านตรวจ reason ก่อนเชื่อ (noise 54 รายการจาก sidecar)
