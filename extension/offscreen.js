/**
 * VDO Grabber — offscreen document.
 * The MV3 service worker cannot use URL.createObjectURL, and huge data: URLs
 * fail in the downloads API ("Failed - Network error"). This document receives
 * binary chunks from the SW, assembles a real Blob and hands back a
 * chrome-extension:// blob: URL that chrome.downloads can save properly.
 */
const streams = new Map(); // id -> {parts: Uint8Array[], size, mime}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type || !msg.type.startsWith("asb:")) return;
  (async () => {
    switch (msg.type) {
      case "asb:ping": {
        sendResponse({ ok: true });
        break;
      }
      case "asb:begin": {
        streams.set(msg.id, { parts: [], size: 0, mime: msg.mime || "video/mp4" });
        sendResponse({ ok: true });
        break;
      }
      case "asb:chunk": {
        const s = streams.get(msg.id);
        if (!s) { sendResponse({ ok: false, error: "no such stream" }); break; }
        const bin = atob(msg.b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        s.parts.push(bytes);
        s.size += bytes.length;
        sendResponse({ ok: true, size: s.size });
        break;
      }
      case "asb:end": {
        const s = streams.get(msg.id);
        streams.delete(msg.id);
        if (!s) { sendResponse({ ok: false, error: "no such stream" }); break; }
        try {
          const blob = new Blob(s.parts, { type: s.mime });
          const blobUrl = URL.createObjectURL(blob);
          sendResponse({ ok: true, blobUrl, size: s.size, mime: s.mime });
        } catch (e) {
          sendResponse({ ok: false, error: String(e.message || e) });
        }
        break;
      }
      case "asb:abort": {
        streams.delete(msg.id);
        sendResponse({ ok: true });
        break;
      }
      case "asb:revoke": {
        try { URL.revokeObjectURL(msg.blobUrl); } catch (e) {}
        sendResponse({ ok: true });
        break;
      }
    }
  })();
  return true;
});
