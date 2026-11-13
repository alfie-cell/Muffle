// Muffle for YouTube: runs in the page's MAIN world at document_start.
//
// YouTube describes video ads inside the player's JSON (adPlacements, playerAds,
// adSlots). Remove those before the player reads them and no ad is ever
// requested. This mirrors what the uBlock Origin filter list does for YouTube
// (tools/ubo-youtube-snapshot.txt). Four hooks, all cheap:
//   1. window.ytInitialPlayerResponse setter   (data embedded in the page)
//   2. fetch()                                  (/youtubei/v1/player etc.)
//   3. XMLHttpRequest response getters          (older code paths)
//   4. JSON.parse                               (everything else, incl. Shorts)
(() => {
  "use strict";
  if (window.__muffle) return;
  try { Object.defineProperty(window, "__muffle", { value: true }); } catch (_) { window.__muffle = true; }
  try { document.documentElement.setAttribute("data-muffle", "1"); } catch (_) {}

  // Built-in defaults. `make update` derives the same data from uBlock Origin's live
  // list and yt-guard.js hands it over through the DOM; applyConfig() can only ADD
  // field names and URL substrings on top of these, never remove or execute anything.
  const keys = new Set(["adPlacements", "playerAds", "adSlots"]);
  const needles = ["/youtubei/v1/player", "/youtubei/v1/get_watch", "/playlist?list=", "/watch?"];
  const REEL_URL = /reel_watch_sequence/;
  const KEY_RE = /^[A-Za-z][A-Za-z0-9_]{2,40}$/;
  const MAX_ITEMS = 20;
  const nativeParse = JSON.parse;
  const nativeStringify = JSON.stringify;
  let SCRUB_RE = buildScrubRe();

  function buildScrubRe() {
    // keys are validated against KEY_RE, so joining them is regex-safe
    return new RegExp('"(?:' + [...keys].join("|") + ')"', "g");
  }

  function validNeedle(n) {
    return typeof n === "string" && n.length >= 4 && n.length <= 80 &&
      /^[\x21-\x7e]+$/.test(n) && /[\/?]/.test(n);
  }

  function applyConfig(text) {
    try {
      const c = nativeParse.call(JSON, text);
      if (!c || c.schema !== 1) return;
      let changed = false;
      for (const k of Array.isArray(c.keys) ? c.keys : []) {
        if (keys.size < MAX_ITEMS && typeof k === "string" && KEY_RE.test(k) && !keys.has(k)) { keys.add(k); changed = true; }
      }
      for (const n of Array.isArray(c.needles) ? c.needles : []) {
        if (needles.length < MAX_ITEMS && validNeedle(n) && !needles.includes(n)) needles.push(n);
      }
      if (changed) SCRUB_RE = buildScrubRe();
    } catch (_) {}
  }

  function readConfigAttribute() {
    try {
      const text = document.documentElement && document.documentElement.getAttribute("data-muffle-config");
      if (text) applyConfig(text);
    } catch (_) {}
  }
  try {
    document.addEventListener("muffle-config", readConfigAttribute);
    readConfigAttribute();   // yt-guard.js may already have run
  } catch (_) {}

  function pruneShallow(o) {
    if (o && typeof o === "object") {
      for (const k of keys) if (k in o) delete o[k];
    }
    return o;
  }

  function prune(o) {
    if (!o || typeof o !== "object") return o;
    pruneShallow(o);
    if (o.playerResponse) pruneShallow(o.playerResponse);
    // Shorts: entries[].command.reelWatchEndpoint.adClientParams.isAd
    const seq = o.reelWatchSequenceResponse || o;
    const entries = seq && seq.entries;
    if (Array.isArray(entries)) {
      for (const e of entries) {
        try {
          const p = e.command.reelWatchEndpoint.adClientParams;
          if (p && "isAd" in p) delete p.isAd;
        } catch (_) {}
      }
    }
    return o;
  }

  function wantsScrub(url) {
    for (const n of needles) if (url.includes(n)) return true;
    return false;
  }

  function wants(url) {
    return wantsScrub(url) || REEL_URL.test(url);
  }

  function scrubText(url, text) {
    if (typeof text !== "string") return text;
    if (REEL_URL.test(url)) {
      try { return nativeStringify(prune(nativeParse(text))); } catch (_) { return text; }
    }
    if (wantsScrub(url)) return text.replace(SCRUB_RE, '"no_ads"');
    return text;
  }

  // 1. Page-embedded player data.
  function guardGlobal(name) {
    let value = prune(window[name]);
    Object.defineProperty(window, name, {
      configurable: true,
      enumerable: true,
      get() { return value; },
      set(v) { value = prune(v); }
    });
  }
  try { guardGlobal("ytInitialPlayerResponse"); } catch (_) {}
  try { guardGlobal("playerResponse"); } catch (_) {}

  // 2. fetch()
  try {
    const nativeFetch = window.fetch;
    window.fetch = function (input, init) {
      const url = typeof input === "string" ? input : (input && input.url) || String(input);
      const promise = nativeFetch.call(this, input, init);
      if (!wants(url)) return promise;
      return promise.then(async (res) => {
        try {
          const text = await res.clone().text();
          const out = scrubText(url, text);
          if (out === text) return res;
          return new Response(out, { status: res.status, statusText: res.statusText, headers: res.headers });
        } catch (_) {
          return res;
        }
      });
    };
  } catch (_) {}

  // 3. XMLHttpRequest
  try {
    const proto = XMLHttpRequest.prototype;
    const nativeOpen = proto.open;
    const textGetter = Object.getOwnPropertyDescriptor(proto, "responseText").get;
    const respGetter = Object.getOwnPropertyDescriptor(proto, "response").get;
    proto.open = function (method, url) {
      try {
        const u = String(url);
        if (wants(u)) {
          let lastIn, lastOut;
          const scrub = (t) => {
            if (t !== lastIn) { lastIn = t; lastOut = scrubText(u, t); }
            return lastOut;
          };
          Object.defineProperty(this, "responseText", {
            configurable: true,
            get: () => scrub(textGetter.call(this))
          });
          Object.defineProperty(this, "response", {
            configurable: true,
            get: () => {
              const r = respGetter.call(this);
              if (typeof r === "string") return scrub(r);
              if (r && typeof r === "object" && this.responseType === "json") return prune(r);
              return r;
            }
          });
        }
      } catch (_) {}
      return nativeOpen.apply(this, arguments);
    };
  } catch (_) {}

  // 4. JSON.parse (covers responses read in ways the hooks above miss).
  try {
    JSON.parse = function (text, reviver) {
      return prune(nativeParse.call(JSON, text, reviver));
    };
  } catch (_) {}
})();
