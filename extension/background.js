/**
 * VDO Grabber — MV3 service worker.
 *  - webRequest sniffer (observer role of the reference extension's service/main.js)
 *  - download orchestration:
 *      direct http(s) -> blob: URL via downloads API -> page-read bytes streamed
 *      through the offscreen assembler (real Blob + blob: URL, no data-URL size
 *      limit) -> MSE capture. Streaming chunks pass through the SW one by one,
 *      so memory stays at ~1x file size inside the offscreen document.
 *  - per-tab media store (chrome.storage.session) + event log ring buffer
 */
const MAX_ASSEMBLED = 800 * 1024 * 1024; // safety cap for page-read assembly (bytes)
const OFFSCREEN_URL = "offscreen.html";

// ---------------------------------------------------------------- site exclusions (v1.1.3)
// Patterns in chrome.storage.local "vgExclusions" - one Chrome match pattern
// ("https://www.facebook.com/*", "*://*.tiktok.com/*") or a bare domain
// ("facebook.com" = the domain + subdomains, any path). Excluded pages get
// no panel, no detection, no reporting and no streaming.
let vgExclusionsCache = null;
function vgExclusionRe(pattern) {
  let p = String(pattern || "").trim().toLowerCase();
  if (!p) return null;
  if (!/^[a-z*]+:\/\//.test(p)) p = "*://" + p;
  const m = p.match(/^([a-z*]+):\/\/([^\/]*)(.*)$/);
  if (!m) return null;
  const scheme = m[1], host = m[2];
  let path = m[3];
  if (!path || path === "/") path = "/*";
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const schemeRe = scheme === "*" ? "https?" : esc(scheme);
  // "*.host.tld" matches subdomains AND the bare host (Chrome patterns only
  // cover subdomains - matching the apex too is friendlier); a bare host
  // covers its subdomains as well
  const hostRe = host.startsWith("*.")
    ? "(?:[^/]+\\.)?" + esc(host.slice(2)).replace(/\\\*/g, "[^/]*")
    : host.includes("*")
      ? esc(host).replace(/\\\*/g, "[^/]*")
      : "(?:[^/]+\\.)?" + esc(host);
  const pathRe = esc(path).replace(/\\\*/g, ".*");
  try { return new RegExp("^" + schemeRe + "://" + hostRe + "(?::\\d+)?" + pathRe + "$"); }
  catch (e) { return null; }
}
function vgIsExcludedUrl(patterns, url) {
  const u = String(url || "").toLowerCase();
  return (patterns || []).some((p) => { const re = vgExclusionRe(p); return !!re && re.test(u); });
}
async function vgGetExclusions() {
  if (vgExclusionsCache) return vgExclusionsCache;
  try {
    const s = await chrome.storage.local.get("vgExclusions");
    vgExclusionsCache = s.vgExclusions || [];
  } catch (e) { vgExclusionsCache = []; }
  return vgExclusionsCache;
}

// ---------------------------------------------------------------- storage/log
async function tabKey(tabId) { return "tab:" + tabId; }

async function addMedia(tabId, item) {
  if (!tabId || tabId < 0) return;
  // v1.1.3: drop candidates from excluded sites (belt-and-suspenders - the
  // content script already refuses to report from an excluded page)
  if (await vgIsExcludedUrl(await vgGetExclusions(), item && item.page)) return;
  const key = await tabKey(tabId);
  const store = await chrome.storage.session.get(key);
  const list = store[key] || [];
  if (list.some((m) => m.url === item.url)) return;
  list.push({ ...item, ts: Date.now() });
  await chrome.storage.session.set({ [key]: list.slice(-60) });
  log("media", `${item.kind} ${item.url.slice(0, 110)} via ${item.via || item.label || "?"}`);
  chrome.tabs.sendMessage(tabId, { type: "vg:netMedia", url: item.url, kind: item.kind }).catch(() => {});
}

const logs = [];
let logPersistTimer = null;
function log(event, message) {
  logs.push(`${new Date().toISOString()} [${event}] ${message}`);
  if (logs.length > 400) logs.splice(0, logs.length - 400);
  // persist (best effort) so the popup / tests can read them even when the SW restarts
  clearTimeout(logPersistTimer);
  logPersistTimer = setTimeout(() => {
    chrome.storage.session.set({ vgLogs: logs.slice(-120) }).catch(() => {});
  }, 50);
}
chrome.runtime.onInstalled.addListener(() => log("life", "extension installed"));
chrome.runtime.onStartup.addListener(() => log("life", "browser startup"));

// ---------------------------------------------------------------- offscreen
let offscreenReady = false;
async function ensureOffscreen() {
  const has = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)],
  });
  if (has.length && offscreenReady) return true;
  if (!has.length) {
    try {
      await chrome.offscreen.createDocument({
        url: OFFSCREEN_URL,
        reasons: ["BLOBS"],
        justification: "Assemble downloaded video bytes into a Blob for saving",
      });
      log("offscreen", "created");
    } catch (e) {
      if (!/single offscreen|already exists/i.test(String(e.message))) {
        log("error", "offscreen create: " + e.message);
        return false;
      }
    }
  }
  // wait until the offscreen document actually answers (it loads async)
  for (let i = 0; i < 25; i++) {
    const pong = await offscreen({ type: "asb:ping" });
    if (pong && pong.ok) {
      offscreenReady = true;
      return true;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  log("error", "offscreen never became ready");
  return false;
}

function offscreen(msg) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(msg, (r) => {
      if (chrome.runtime.lastError) resolve({ ok: false, error: chrome.runtime.lastError.message });
      else resolve(r || { ok: false, error: "no response from offscreen" });
    });
  });
}

// ---------------------------------------------------------------- sniffer
const CONTENT_TYPES = /(video\/|audio\/|mpegurl|dash\+xml|octet-stream)/i;
chrome.webRequest.onHeadersReceived.addListener(
  async (details) => {
    try {
      if (details.tabId < 0) return;
      // v1.1.3: the page (initiator) is excluded -> never sniff its media
      if (await vgIsExcludedUrl(await vgGetExclusions(), details.initiator || details.originUrl || details.documentUrl)) return;
      const ct = (details.responseHeaders || []).find((h) => h.name.toLowerCase() === "content-type");
      const val = (ct && ct.value) || "";
      if (!CONTENT_TYPES.test(val) || /text\/html/i.test(val)) return;
      const u = details.url;
      if (!/\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i.test(u) && !/mpegurl|dash\+xml/i.test(val)) return;
      const kind = /mpegurl/i.test(val) || /\.m3u8/i.test(u) ? "m3u8" : /dash/i.test(val) || /\.mpd/i.test(u) ? "mpd" : "media";
      addMedia(details.tabId, { url: u, kind, label: "network request", via: "webRequest" });
    } catch (e) {}
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// tell a tab's content script to re-check the exclusion list right away
// (called after the popup adds a pattern for the current site)
async function vgApplyExclusionToTab(tabId) {
  try { await chrome.tabs.sendMessage(tabId, { type: "vg:exclusionsUpdated" }); } catch (e) {}
}

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const key = await tabKey(tabId);
  chrome.storage.session.remove(key);
});

// a new navigation invalidates every blob: URL the old document created -
// clear the tab's media list so the panel never offers dead links
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== "loading") return;
  const key = await tabKey(tabId);
  const store = await chrome.storage.session.get(key);
  if (store[key] && store[key].length) {
    await chrome.storage.session.set({ [key]: [] });
    log("nav", "cleared media list for new navigation");
    chrome.tabs.sendMessage(tabId, { type: "vg:clearMedia" }).catch(() => {});
  }
});

// ---------------------------------------------------------------- naming
function safeName(title, ext) {
  const base = (title || "video").replace(/[\\/:*?"<>|]+/g, " ").replace(/\s+/g, " ").trim().slice(0, 80) || "video";
  return `${base}.${ext}`;
}
function extOf(url, mime) {
  const m = (url.match(/\.(mp4|webm|mkv|m3u8|mpd|flv|mov|avi|mp3|m4a|aac|ts|3gp)([?#].*)?$/i) || [])[1];
  if (m) return m.toLowerCase();
  if (mime) {
    const mm = /(?:video|audio)\/(\w+)/i.exec(mime);
    if (mm) return mm[1].toLowerCase();
  }
  return "mp4";
}

// ---------------------------------------------------------------- download
async function downloadUrl(url, filename) {
  // NOTE: chrome.downloads.download rejects restricted headers such as
  // Referer ("Unsafe request header name") - it would kill the whole
  // download. Cookies ride along automatically via the browser profile;
  // hotlink-protected URLs are handled by the page-fetch fallback instead.
  return new Promise((resolve) => {
    try {
      chrome.downloads.download({ url, filename, conflictAction: "uniquify", saveAs: false }, (id) => {
        if (chrome.runtime.lastError || !id) {
          resolve({ ok: false, error: (chrome.runtime.lastError && chrome.runtime.lastError.message) || "no download id" });
        } else {
          log("download", `started #${id} ${filename}`);
          resolve({ ok: true, id });
        }
      });
    } catch (e) {
      resolve({ ok: false, error: String(e.message || e) });
    }
  });
}

// watch a download started via chrome.downloads:
//  - on complete: release the offscreen blob URL (if any)
//  - on interrupted ("Failed - Network error", typical for MediaSource-backed
//    blob: URLs or expired signed links): hand the job to the page-read chain
function watchDownload(downloadId, tabId, item, blobUrl) {
  const onChange = async (delta) => {
    if (delta.id !== downloadId) return;
    const state = delta.state && delta.state.current;
    if (!state) return;
    chrome.downloads.onChanged.removeListener(onChange);
    if (blobUrl) {
      await ensureOffscreen();
      await offscreen({ type: "asb:revoke", blobUrl });
    }
    if (state === "complete") {
      log("download", `#${downloadId} complete`);
      return;
    }
    const err = (delta.error && delta.error.current) || "interrupted";
    if (blobUrl) {
      // assembled download failed - nothing more we can do for it here
      log("download", `#${downloadId} ${err} (assembled)`);
      return;
    }
    log("download-fallback", `#${downloadId} ${err} → page read`);
    if (tabId) {
      chrome.tabs.sendMessage(tabId, { type: "vg:downloadFailed", item }).catch(() => {});
    }
  };
  chrome.downloads.onChanged.addListener(onChange);
}

// assemble a stream of base64 chunks (already forwarded to offscreen) into a
// real blob: URL and download it - avoids the data:URL "Failed - Network error"
async function assembleAndDownload(id, mime, filename, tabId) {
  await ensureOffscreen();
  const end = await offscreen({ type: "asb:end", id });
  if (!end.ok) return { ok: false, error: end.error };
  if (end.size > MAX_ASSEMBLED) {
    await offscreen({ type: "asb:revoke", blobUrl: end.blobUrl });
    return { ok: false, error: `ไฟล์ใหญ่ ${(end.size / 1048576).toFixed(0)} MB เกินขีดจำกัดของส่วนขยาย — ใช้เวอร์ชันเดสก์ท็อปแทน` };
  }
  const r = await downloadUrl(end.blobUrl, filename);
  if (!r.ok) {
    await offscreen({ type: "asb:revoke", blobUrl: end.blobUrl });
    return { ok: false, error: r.error };
  }
  watchDownload(r.id, tabId, { url: "", kind: "assembled" }, end.blobUrl);
  if (tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaved", filename }).catch(() => {});
  return { ok: true, filename };
}

// ---------------------------------------------------------------- messaging
async function forceFallback() {
  const s = await chrome.storage.session.get("forceFallback");
  return !!s.forceFallback; // E2E test hook
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const tabId = sender.tab ? sender.tab.id : null;
    try {
      switch (msg.type) {
      case "vg:media":
        await addMedia(tabId, msg.item);
        sendResponse({ ok: true });
        break;

      // ---- site exclusions (v1.1.3) ----------------------------------
      case "vg:exclusions":
        sendResponse({ ok: true, patterns: await vgGetExclusions() });
        break;

      case "vg:exclusion:add": {
        const pat = String(msg.pattern || "").trim();
        if (!pat) { sendResponse({ ok: false, error: "empty pattern" }); break; }
        const list = await vgGetExclusions();
        if (!list.includes(pat)) list.push(pat);
        vgExclusionsCache = list;
        await chrome.storage.local.set({ vgExclusions: list });
        log("exclusions", `added ${pat} (${list.length} total)`);
        if (tabId != null) await vgApplyExclusionToTab(tabId);
        sendResponse({ ok: true, patterns: list });
        break;
      }

      case "vg:exclusion:remove": {
        const pat2 = String(msg.pattern || "").trim();
        const list2 = (await vgGetExclusions()).filter((x) => x !== pat2);
        vgExclusionsCache = list2;
        await chrome.storage.local.set({ vgExclusions: list2 });
        log("exclusions", `removed ${pat2} (${list2.length} total)`);
        // every tab re-checks itself - an excluded page must learn it is
        // un-excluded even though its content script ignores other traffic
        for (const t of await chrome.tabs.query({})) {
          chrome.tabs.sendMessage(t.id, { type: "vg:exclusionsUpdated" }).catch(() => {});
        }
        sendResponse({ ok: true, patterns: list2 });
        break;
      }

      case "vg:download": {
        const item = msg.item || {};
        log("download-request", `${item.kind} ${item.url.slice(0, 110)}`);
        const ext = extOf(item.url, "");
        const filename = safeName(item.name, item.kind === "m3u8" ? "m3u8" : ext);
        let skip = false;
        try { skip = await forceFallback(); } catch (e) { log("error", "forceFallback: " + e.message); }
        // 1) direct http(s) download (cookies ride along with the browser profile)
        if (/^https?:/i.test(item.url) && !skip) {
          const r = await downloadUrl(item.url, filename);
          log(r.ok ? "download" : "download-fallback", `direct ${item.kind}: ${r.ok ? "ok #" + r.id : r.error}`);
          if (r.ok) {
            watchDownload(r.id, tabId, item);
            sendResponse({ ok: true });
            return;
          }
        } else if (item.url.startsWith("blob:") && !skip) {
          // 2) page-created blob URL: let the downloads system resolve it
          //    while the page is alive (works for real Blob/File objects;
          //    MediaSource-backed blobs fail here and fall through)
          const r = await downloadUrl(item.url, filename);
          log(r.ok ? "download" : "download-fallback", `blob direct: ${r.ok ? "ok #" + r.id : r.error}`);
          if (r.ok) {
            watchDownload(r.id, tabId, item);
            sendResponse({ ok: true });
            return;
          }
        }
        // 3) content script reads bytes inside the page (blob fetch / URL fetch / MSE)
        sendResponse({ ok: false, error: "direct download unavailable" });
        break;
      }

      // ---- streaming assembly (content script forwards page bytes) ----
      case "vg:blobBegin": {
        await ensureOffscreen();
        sendResponse(await offscreen({ type: "asb:begin", id: msg.id, mime: msg.mime || "video/mp4" }));
        break;
      }
      case "vg:chunk": {
        sendResponse(await offscreen({ type: "asb:chunk", id: msg.id, b64: msg.b64 }));
        break;
      }
      case "vg:blobEnd": {
        const mime = msg.mime || "video/mp4";
        const filename = safeName(msg.name || "video", extOf("", mime));
        const r = await assembleAndDownload(msg.id, mime, filename, tabId);
        log(r.ok ? "download" : "error", `assembled ${filename}: ${r.ok ? "saved" : r.error}`);
        if (!r.ok && tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaveFailed", error: r.error }).catch(() => {});
        sendResponse(r);
        break;
      }
      case "vg:blobAbort": {
        await offscreen({ type: "asb:abort", id: msg.id });
        sendResponse({ ok: true });
        break;
      }

      case "vg:blobFailed":
        log("error", `page read failed: ${msg.error}`);
        if (tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaveFailed", error: msg.error }).catch(() => {});
        sendResponse({ ok: true });
        break;

      case "vg:log":
        log("content", msg.msg || "");
        sendResponse({ ok: true });
        break;

      case "vg:setFlag":
        await chrome.storage.session.set({ forceFallback: !!msg.value });
        log("test", `forceFallback=${!!msg.value}`);
        sendResponse({ ok: true });
        break;

      case "vg:logs":
        chrome.storage.session.get("vgLogs").then((s) => {
          const persisted = s.vgLogs || [];
          const merged = Array.from(new Set(persisted.concat(logs.slice(-30))));
          sendResponse({ ok: true, logs: merged.slice(-100) });
        }).catch(() => sendResponse({ ok: true, logs: logs.slice(-100) }));
        break;

      case "vg:downloads":
        chrome.downloads.search({}).then((d) => sendResponse({
          ok: true,
          downloads: d.map((x) => ({ id: x.id, state: x.state && x.state.current, error: x.error, file: (x.filename || "").split(/[\\/]/).pop(), bytes: x.bytesReceived, total: x.totalBytes }))
        }));
        break;

      case "vg:list": {
        // content scripts don't know their tab id - fall back to the sender
        const tabId = (sender.tab && sender.tab.id) != null ? sender.tab.id : msg.tabId;
        const key = await tabKey(tabId);
        const store = await chrome.storage.session.get(key);
        sendResponse({ ok: true, items: store[key] || [], logs: logs.slice(-120) });
        break;
      }

      default:
        sendResponse({ ok: false, error: "unknown message" });
      }
    } catch (e) {
      log("error", `handler ${msg && msg.type}: ${e.message}\n${(e.stack || "").split("\n")[1] || ""}`);
      try { sendResponse({ ok: false, error: e.message }); } catch (e2) {}
    }
  })();
  return true; // async sendResponse
});

// CDP debug access: chrome.runtime SW is evaluated by scripts/test_extension.py
self.__vgDebug = { get logs() { return logs.slice(-60); } };
