/**
 * VDO Grabber — ISOLATED-world content script.
 * DOM scan + floating panel UI (Shadow DOM) + bridge between the MAIN world
 * injector and the service worker. Bytes from the page are streamed to the
 * SW in ordered 4 MB chunks (assembled by the offscreen document).
 */
(() => {
  if (window.__vgContent) return;
  window.__vgContent = true;

  const MEDIA_RE = /\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i;
  const items = new Map();          // url -> {url, kind, label, via}
  const streams = new Map();        // assembly id -> {name, kind, mime, total, received, queue}
  let seq = 0;

  function report(url, kind, label, via) {
    try {
      if (!url || !/^(https?|blob)/.test(url)) return;
      let k = kind;
      if (!k || k === "media") {
        if (url.startsWith("blob:")) k = "blob";
        else {
          const ext = (url.match(MEDIA_RE) || [])[1];
          k = ext ? ext.toLowerCase() : "media";
        }
      }
      const key = url.slice(0, 400);
      if (items.has(key)) return;
      items.set(key, { url, kind: k, label: label || "", via: via || "" });
      try { chrome.runtime.sendMessage({ type: "vg:media", item: { url, kind: k, label: label || "", via: via || "", page: location.href, title: document.title } }); } catch (e) {}
      updateBadge();
      renderList();
    } catch (e) {}
  }

  function scanDom() {
    try {
      document.querySelectorAll("video, audio").forEach((v) => {
        const src = v.currentSrc || v.src || (v.querySelector("source") && v.querySelector("source").src);
        if (src) report(src, null, (v.tagName.toLowerCase() + " element") + (v.videoWidth ? ` — ${v.videoWidth}×${v.videoHeight}` : ""), "DOM scan");
      });
      document.querySelectorAll("video source, audio source").forEach((s) => { if (s.src) report(s.src, null, "<source>", "DOM scan"); });
      ["og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"].forEach((p) => {
        const m = document.querySelector(`meta[property="${p}"], meta[name="${p}"]`);
        if (m && m.content) report(m.content, null, "meta " + p, "DOM scan");
      });
      document.querySelectorAll('script[type="application/ld+json"]').forEach((s) => {
        try {
          const data = JSON.parse(s.textContent);
          (Array.isArray(data) ? data : [data]).forEach((d) => {
            if (d && d["@type"] === "VideoObject" && d.contentUrl) report(d.contentUrl, null, "JSON-LD", "DOM scan");
          });
        } catch (e) {}
      });
      document.querySelectorAll("a[href]").forEach((a) => {
        if (MEDIA_RE.test(a.href)) report(a.href, null, "link", "DOM scan");
      });
      performance.getEntriesByType("resource").forEach((r) => {
        if (MEDIA_RE.test(r.name)) report(r.name, null, "network (timing)", "resource timing");
      });
    } catch (e) {}
  }

  // ---------------- messages from MAIN world (streaming) -------------------
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || !e.data.__vg) return;
    const d = e.data;

    // debug bridge: page (E2E tests) asks for SW state through the content script
    if (d.__vg === "ui" && (d.type === "debugLogs" || d.type === "debugDownloads" || d.type === "debugSetFlag")) {
      const type = { debugLogs: "vg:logs", debugDownloads: "vg:downloads", debugSetFlag: "vg:setFlag" }[d.type];
      const out = { type };
      if (d.type === "debugSetFlag") out.value = d.value;
      chrome.runtime.sendMessage(out, (resp) => {
        window.postMessage({ __vg: "main", type: d.type, reqId: d.reqId, resp: resp || { err: chrome.runtime.lastError && chrome.runtime.lastError.message } }, "*");
      });
      return;
    }

    if (d.__vg !== "main") return;
    if (d.type === "media") { report(d.url, null, "network", d.via || "network"); return; }

    const st = streams.get(d.id);
    if (!st) return;

    if (d.type === "blobMeta") {
      st.mime = d.mime || st.mime;
      st.total = d.total || 0;
      st.queue = st.queue.then(() => send({ type: "vg:blobBegin", id: d.id, mime: st.mime }));
    } else if (d.type === "blobChunk") {
      st.received += 4 * 1024 * 1024;
      const mb = st.total ? `${Math.min(st.received, st.total) / 1048576 | 0}/${st.total / 1048576 | 0} MB` : `${st.received / 1048576 | 0} MB`;
      setStatus(`⏳ อ่านข้อมูลจากหน้าเว็บ… ${mb}`);
      st.queue = st.queue.then(() => send({ type: "vg:chunk", id: d.id, b64: d.b64 }));
    } else if (d.type === "blobDone") {
      st.queue = st.queue.then(() => send({ type: "vg:blobEnd", id: d.id, name: st.name, mime: st.mime }))
        .then(() => streams.delete(d.id));
    } else if (d.type === "blobError") {
      st.queue = st.queue.then(() => send({ type: "vg:blobFailed", id: d.id, error: d.error }))
        .then(() => streams.delete(d.id));
    }
  });

  function send(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, () => { void chrome.runtime.lastError; resolve(); });
      } catch (e) { resolve(); }
    });
  }

  function askMain(msg) { try { window.postMessage(Object.assign({ __vg: "ui" }, msg), "*"); } catch (e) {} }

  function newStream(name, kind) {
    const id = "d" + (++seq) + "-" + Date.now();
    streams.set(id, { name, kind, mime: kind === "mse" ? "video/mp4" : "", total: 0, received: 0, queue: Promise.resolve() });
    return id;
  }

  // ---------------- UI (Shadow DOM pill + panel) ---------------------------
  const host = document.createElement("div");
  host.id = "vg-content-host";
  host.style.cssText = "position:fixed;right:14px;bottom:14px;z-index:2147483647";
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `
  <style>
    .pill{display:flex;align-items:center;gap:7px;background:linear-gradient(90deg,#ff5f6d,#ffc371);color:#fff;
          border:0;border-radius:22px;padding:9px 16px;font:600 13px 'Segoe UI',system-ui,sans-serif;cursor:pointer;
          box-shadow:0 6px 22px rgba(255,95,109,.45)}
    .panel{display:none;position:absolute;bottom:48px;right:0;width:390px;max-height:65vh;overflow:auto;
           background:#0f1729;border:1px solid #2a3a5c;border-radius:14px;box-shadow:0 16px 48px rgba(0,0,0,.55);
           color:#e8ecf4;font:13px/1.5 'Segoe UI',system-ui,sans-serif;padding:12px}
    .panel h4{margin:2px 4px 10px;color:#93a4c3;font-size:12.5px}
    .item{border:1px solid #233152;border-radius:10px;padding:9px;margin-bottom:8px;background:#131c33}
    .item b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#f1f5f9;font-size:12.5px}
    .item small{color:#7d8db1;word-break:break-all;font-size:11px;display:block;margin-top:2px}
    .row{display:flex;gap:6px;margin-top:7px;flex-wrap:wrap;align-items:center}
    .dl{background:#16a34a;color:#fff;border:0;border-radius:7px;padding:5px 12px;font-weight:700;cursor:pointer;font-size:12px}
    .dl:hover{background:#15803d}
    .cp{background:#243354;color:#cdd6e6;border:0;border-radius:7px;padding:5px 10px;cursor:pointer;font-size:11.5px}
    .tag{display:inline-block;background:#1a2547;color:#93a4c3;border-radius:8px;padding:1px 8px;font-size:10.5px;margin-left:4px}
    .empty{color:#5c6b8c;text-align:center;padding:16px 0;font-size:12px}
    .status{color:#ffc371;font-size:11.5px;margin:6px 4px 0;min-height:16px}
    .count{background:rgba(255,255,255,.25);border-radius:9px;padding:0 7px;font-size:11px}
  </style>
  <div class="panel" id="vg-panel">
    <h4>🎬 วิดีโอที่ตรวจพบในหน้านี้</h4>
    <div id="vg-list"><div class="empty">ยังไม่พบวิดีโอ — เล่นวิดีโอในหน้าสักครู่</div></div>
    <div class="status" id="vg-status"></div>
  </div>
  <button class="pill" id="vg-pill">▼ วิดีโอ <span class="count" id="vg-count">0</span></button>`;

  function mount() {
    if (!document.documentElement) { setTimeout(mount, 200); return; }
    document.documentElement.appendChild(host);
    shadow.getElementById("vg-pill").addEventListener("click", () => {
      const p = shadow.getElementById("vg-panel");
      p.style.display = p.style.display === "block" ? "none" : "block";
      renderList();
    });
    scanDom();
    setInterval(scanDom, 3000);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
  else mount();

  function updateBadge() {
    const c = shadow.getElementById("vg-count");
    if (c) c.textContent = String(items.size);
  }

  function setStatus(t) {
    const s = shadow.getElementById("vg-status");
    if (s) s.textContent = t;
  }

  function esc(s) { return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function renderList() {
    const list = shadow.getElementById("vg-list");
    if (!list || !items.size) return;
    const kindLabel = { blob: "blob (in-page)", m3u8: "HLS", mpd: "DASH" };
    list.innerHTML = [...items.values()].reverse().map((m) => `
      <div class="item">
        <b>${esc(m.label || m.kind)}<span class="tag">${esc(kindLabel[m.kind] || m.kind)}</span></b>
        <small>${esc(m.url.slice(0, 140))}</small>
        <div class="row">
          <button class="dl" data-u="${esc(m.url)}" data-k="${esc(m.kind)}">⬇ ดาวน์โหลด</button>
          <button class="cp" data-u="${esc(m.url)}">คัดลอกลิงก์</button>
        </div>
      </div>`).join("");
    list.querySelectorAll(".dl").forEach((b) => b.addEventListener("click", () => startDownload(b.getAttribute("data-u"), b.getAttribute("data-k"))));
    list.querySelectorAll(".cp").forEach((b) => b.addEventListener("click", () => {
      navigator.clipboard.writeText(b.getAttribute("data-u"));
      setStatus("คัดลอกลิงก์แล้ว");
    }));
  }

  // ---------------- download pipeline --------------------------------------
  function pageReadFallback(url, kind, name) {
    if (kind === "blob") {
      const id = newStream(name, "blob");
      setStatus("⏳ อ่าน blob จากหน้าเว็บ…");
      askMain({ type: "readBlob", id, url });
    } else if (kind === "m3u8" || kind === "mpd") {
      setStatus("📺 สตรีม HLS/DASH — ใช้ VDOGrabber เวอร์ชันเดสก์ท็อป (yt-dlp) เพื่อรวมไฟล์");
    } else {
      // http(s) media the browser refused (expired token / picky CDN):
      // re-fetch with the page's own credentials and stream it out
      const id = newStream(name, "media");
      setStatus("⏳ อ่านไฟล์ด้วย fetch ของหน้าเว็บ…");
      askMain({ type: "readUrl", id, url });
    }
  }

  function startDownload(url, kind) {
    try { chrome.runtime.sendMessage({ type: "vg:log", msg: `content startDownload kind=${kind} url=${url.slice(0, 80)}` }); } catch (e) {}
    const name = (document.title || "video").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80) || "video";
    if (kind === "m3u8" || kind === "mpd") {
      setStatus("📺 สตรีม HLS/DASH — ใช้ VDOGrabber เวอร์ชันเดสก์ท็อป (yt-dlp) เพื่อรวมไฟล์");
      return;
    }
    setStatus("⏳ กำลังส่งให้ตัวดาวน์โหลด…");
    chrome.runtime.sendMessage({ type: "vg:download", item: { url, kind, name, page: location.href } }, (resp) => {
      const lastErr = chrome.runtime.lastError;
      if (lastErr || !resp || !resp.ok) {
        pageReadFallback(url, kind, name);
      } else {
        setStatus("✅ เริ่มดาวน์โหลดแล้ว — ดูในแถบดาวน์โหลดของ Chrome");
      }
    });
  }

  // messages from the service worker
  chrome.runtime.onMessage.addListener((msg, sender) => {
    if (sender.id !== chrome.runtime.id) return;
    if (msg.type === "vg:netMedia") report(msg.url, msg.kind, "network", "webRequest");
    else if (msg.type === "vg:clearMedia") {
      items.clear();
      updateBadge();
      const list = shadow.getElementById("vg-list");
      if (list) list.innerHTML = '<div class="empty">ยังไม่พบวิดีโอ — เล่นวิดีโอในหน้าสักครู่</div>';
    }
    else if (msg.type === "vg:blobSaved") setStatus("✅ ดาวน์โหลดสำเร็จ: " + (msg.filename || ""));
    else if (msg.type === "vg:downloadFailed") {
      // chrome.downloads reported the async failure (MSE blob / expired link)
      const it = msg.item || {};
      setStatus("⚠️ Chrome ดาวน์โหลดตรงไม่สำเร็จ — สลับไปอ่านข้อมูลจากหน้าเว็บ…");
      pageReadFallback(it.url, it.kind, it.name || (document.title || "video").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80));
    } else if (msg.type === "vg:blobSaveFailed") {
      // last resort: MSE capture (for blob: sources backed by MediaSource)
      const mseStream = [...streams.values()].find((s) => s.kind === "mse");
      if (!mseStream) {
        const name = (document.title || "video").replace(/[\\/:*?"<>|]+/g, " ").trim().slice(0, 80) || "video";
        const id = newStream(name, "mse");
        setStatus("⏳ พยายามรวมชิ้นส่วนสตรีม (MSE capture)…");
        askMain({ type: "readMse", id });
      } else {
        setStatus("❌ " + (msg.error || "ดาวน์โหลดไม่สำเร็จ") + " — ลองเล่นวิดีโอให้ครบแล้วกดใหม่ หรือใช้เวอร์ชันเดสก์ท็อป");
      }
    }
  });
})();
