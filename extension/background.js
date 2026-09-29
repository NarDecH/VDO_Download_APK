/**
 * VDO Grabber — MV3 service worker.
 *  - webRequest sniffer (observer role of the reference extension's service/main.js)
 *  - download orchestration: direct URL → blob: URL → page-read data URL → MSE capture
 *  - per-tab media store (chrome.storage.session) + event log ring buffer
 */
const MAX_DATA_URL = 120 * 1024 * 1024; // safety cap for data-URL downloads

// ---------------------------------------------------------------- storage
async function tabKey(tabId) { return "tab:" + tabId; }

async function addMedia(tabId, item) {
  if (!tabId || tabId < 0) return;
  const key = await tabKey(tabId);
  const store = await chrome.storage.session.get(key);
  const list = store[key] || [];
  if (list.some((m) => m.url === item.url)) return;
  list.push({ ...item, ts: Date.now() });
  await chrome.storage.session.set({ [key]: list.slice(-60) });
  log("media", `${item.kind} ${item.url.slice(0, 110)} via ${item.via || item.label || "?"}`);
  // wake the content script UI badge
  chrome.tabs.sendMessage(tabId, { type: "vg:netMedia", url: item.url, kind: item.kind }).catch(() => {});
}

async function getList(tabId) {
  const key = await tabKey(tabId);
  const store = await chrome.storage.session.get(key);
  return store[key] || [];
}

// ---------------------------------------------------------------- log
const logs = [];
function log(event, message) {
  logs.push(`${new Date().toISOString()} [${event}] ${message}`);
  if (logs.length > 400) logs.splice(0, logs.length - 400);
}
chrome.runtime.onInstalled.addListener(() => log("life", "extension installed"));
chrome.runtime.onStartup.addListener(() => log("life", "browser startup"));

// ---------------------------------------------------------------- sniffer
const CONTENT_TYPES = /(video\/|audio\/|mpegurl|dash\+xml|octet-stream)/i;
chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    try {
      if (details.tabId < 0) return;
      const ct = (details.responseHeaders || []).find((h) => h.name.toLowerCase() === "content-type");
      const val = (ct && ct.value) || "";
      if (!CONTENT_TYPES.test(val)) return;
      if (/text\/html/i.test(val)) return;
      const u = details.url;
      if (!/\.(mp4|m3u8|mpd|webm|mkv|flv|mov|avi|mp3|m4a|aac|ts|3gp)(\?|#|$)/i.test(u) && !/mpegurl|dash\+xml/i.test(val)) return;
      const kind = /mpegurl/i.test(val) || /\.m3u8/i.test(u) ? "m3u8" : /dash/i.test(val) || /\.mpd/i.test(u) ? "mpd" : "media";
      addMedia(details.tabId, { url: u, kind, label: "network request", via: "webRequest" });
    } catch (e) {}
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const key = await tabKey(tabId);
  chrome.storage.session.remove(key);
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
    const mm = /video\/(\w+)/i.exec(mime);
    if (mm) return mm[1].toLowerCase();
  }
  return "mp4";
}

// ---------------------------------------------------------------- download
async function downloadUrl(url, filename, referrer) {
  const opts = { url, filename, conflictAction: "uniquify", saveAs: false };
  if (referrer && /^https?:/i.test(referrer)) opts.headers = [{ name: "Referer", value: referrer }];
  return new Promise((resolve) => {
    try {
      chrome.downloads.download(opts, (id) => {
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

function b64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function downloadDataUrl(b64, mime, filename, tabId) {
  const bytes = b64ToBytes(b64);
  if (bytes.length < 1) return { ok: false, error: "empty data" };
  if (bytes.length > MAX_DATA_URL) {
    return { ok: false, error: `ข้อมูลใหญ่เกิน ${(bytes.length / 1048576).toFixed(0)} MB (cap) — ใช้เวอร์ชันเดสก์ท็อปแทน` };
  }
  let binary = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + step, bytes.length)));
  }
  const dataUrl = `data:${mime || "video/mp4"};base64,${btoa(binary)}`;
  const res = await downloadUrl(dataUrl, filename);
  if (!res.ok && tabId) {
    chrome.tabs.sendMessage(tabId, { type: "vg:blobSaveFailed", error: res.error }).catch(() => {});
  }
  return res;
}

// ---------------------------------------------------------------- messaging
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    const tabId = sender.tab ? sender.tab.id : null;
    switch (msg.type) {
      case "vg:media":
        await addMedia(tabId, msg.item);
        sendResponse({ ok: true });
        break;

      case "vg:download": {
        const item = msg.item || {};
        log("download-request", `${item.kind} ${item.url.slice(0, 110)}`);
        const ext = extOf(item.url, "");
        let filename = safeName(item.name, item.kind === "m3u8" ? "m3u8" : ext);
        // 1) direct http(s) → plain download with page as referrer
        if (/^https?:/i.test(item.url)) {
          const r = await downloadUrl(item.url, filename, item.page);
          if (r.ok) { sendResponse({ ok: true }); return; }
          log("download-fallback", `${r.error} → page blob read`);
        } else if (item.url.startsWith("blob:")) {
          // 2) page-created blob URL: let the downloads system resolve it while the page is alive
          const r = await downloadUrl(item.url, filename, item.page);
          if (r.ok) { sendResponse({ ok: true }); return; }
          log("download-fallback", `blob direct: ${r.error} → page blob read`);
        }
        // 3) neither worked → content script will read bytes in the page (blob fetch / MSE)
        sendResponse({ ok: false, error: "direct download unavailable" });
        break;
      }

      case "vg:blobData": {
        const mime = msg.mime || "video/mp4";
        const ext = extOf("", mime);
        const filename = safeName(`${(msg.name || "blob-video")}`, ext);
        const r = await downloadDataUrl(msg.b64, mime, filename, tabId);
        log(r.ok ? "download" : "error", `blob data ${filename}: ${r.ok ? "saved" : r.error}`);
        if (tabId) {
          chrome.tabs.sendMessage(tabId, r.ok ? { type: "vg:blobSaved", filename } : { type: "vg:blobSaveFailed", error: r.error }).catch(() => {});
        }
        sendResponse({ ok: r.ok });
        break;
      }

      case "vg:blobFailed":
        log("error", `page blob read failed: ${msg.error}`);
        if (tabId) chrome.tabs.sendMessage(tabId, { type: "vg:blobSaveFailed", error: msg.error }).catch(() => {});
        sendResponse({ ok: true });
        break;

      case "vg:list": {
        const list = await getList(msg.tabId);
        sendResponse({ ok: true, items: list, logs: logs.slice(-120) });
        break;
      }

      default:
        sendResponse({ ok: false, error: "unknown message" });
    }
  })();
  return true; // async sendResponse
});
