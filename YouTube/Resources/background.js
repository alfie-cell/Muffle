// Muffle for YouTube: non-persistent background script. Runs at Safari launch and on
// install, then Safari unloads it. Two jobs:
//   1. register yt-main.js to run in the page's MAIN world at document_start
//      (Safari 16.4+ supports world: "MAIN" via registerContentScripts);
//   2. ask the native handler for the YouTube ad-removal data that `make update`
//      derives from uBlock Origin's list, and keep a validated copy in storage.

const MATCHES = [
  "*://*.youtube.com/*",
  "*://*.youtube-nocookie.com/*",
  "*://*.youtubekids.com/*"
];
const KEY_RE = /^[A-Za-z][A-Za-z0-9_]{2,40}$/;
const MAX_ITEMS = 20;

async function registerMainWorldScript() {
  try {
    const existing = await browser.scripting.getRegisteredContentScripts({ ids: ["yt-main"] });
    if (existing.length > 0) return;
    await browser.scripting.registerContentScripts([{
      id: "yt-main",
      js: ["yt-main.js"],
      matches: MATCHES,
      runAt: "document_start",
      world: "MAIN",
      persistAcrossSessions: true
    }]);
  } catch (error) {
    // Registration can fail before the user grants YouTube access, or on a
    // Safari without MAIN-world support. yt-guard.js injects the script itself
    // in that case, so this is not fatal.
    console.warn("Muffle: could not register main-world script", error);
  }
}

function validNeedle(n) {
  return typeof n === "string" && n.length >= 4 && n.length <= 80 &&
    /^[\x21-\x7e]+$/.test(n) && /[\/?]/.test(n);
}

// Same rules as the native side and yt-main.js: only field names and URL substrings
// get through, capped, never code.
function sanitize(config) {
  if (!config || typeof config !== "object" || config.schema !== 1) return null;
  const keys = [...new Set((Array.isArray(config.keys) ? config.keys : [])
    .filter((k) => typeof k === "string" && KEY_RE.test(k)))].slice(0, MAX_ITEMS);
  const needles = [...new Set((Array.isArray(config.needles) ? config.needles : [])
    .filter(validNeedle))].slice(0, MAX_ITEMS);
  if (keys.length === 0) return null;
  return { schema: 1, generated: String(config.generated || ""), keys, needles };
}

async function refreshConfig() {
  try {
    const reply = await browser.runtime.sendNativeMessage("com.example.muffle.youtube", { type: "getConfig" });
    const config = sanitize(reply && reply.config);
    if (!config) return;
    await browser.storage.local.set({ ytConfig: config, ytConfigFetched: Date.now() });
  } catch (error) {
    console.warn("Muffle: config refresh failed", error);
  }
}

function startup() {
  registerMainWorldScript();
  refreshConfig();
}

browser.runtime.onInstalled.addListener(startup);
browser.runtime.onStartup.addListener(startup);
if (browser.permissions && browser.permissions.onAdded) {
  browser.permissions.onAdded.addListener(registerMainWorldScript);
}
browser.runtime.onMessage.addListener((message) => {
  if (message && message.type === "refreshConfig") refreshConfig();
});
startup();
