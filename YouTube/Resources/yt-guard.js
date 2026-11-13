// Muffle for YouTube: isolated-world content script, document_start.
//
// 1. Makes sure yt-main.js reached the page. Normally background.js registers
//    it as a MAIN-world script; if that did not happen (permission not yet
//    granted, or an older Safari) this injects it directly.
// 2. Passes the ad-removal data from storage to yt-main.js via the DOM.
// 3. Safety net: if an ad still starts playing, mute it and jump to its end.
//    One MutationObserver on the player's class attribute; no polling.
(() => {
  "use strict";

  function ensureMainScript() {
    try {
      if (document.documentElement.hasAttribute("data-muffle")) return;
      const url = browser.runtime.getURL("yt-main.js");
      const script = document.createElement("script");
      try {
        // Synchronous so the code runs before the page's own inline scripts.
        const xhr = new XMLHttpRequest();
        xhr.open("GET", url, false);
        xhr.send();
        if (xhr.status === 200 || xhr.status === 0) script.textContent = xhr.responseText;
      } catch (_) {}
      if (!script.textContent) script.src = url;
      (document.head || document.documentElement).appendChild(script);
      script.remove();
    } catch (_) {}
  }
  ensureMainScript();

  // Hand the YouTube ad-removal data (from `make update`, kept in storage by
  // background.js) to yt-main.js. Objects don't cross the isolated/MAIN world
  // boundary, but the DOM does: the JSON goes into an attribute plus an event.
  const CONFIG_MAX_AGE = 24 * 3600 * 1000;
  function publishConfig(config) {
    try {
      document.documentElement.setAttribute("data-muffle-config", JSON.stringify(config));
      document.dispatchEvent(new CustomEvent("muffle-config"));
    } catch (_) {}
  }
  try {
    browser.storage.local.get(["ytConfig", "ytConfigFetched"]).then((stored) => {
      if (stored && stored.ytConfig) publishConfig(stored.ytConfig);
      const age = Date.now() - ((stored && stored.ytConfigFetched) || 0);
      if (age > CONFIG_MAX_AGE) browser.runtime.sendMessage({ type: "refreshConfig" }).catch(() => {});
    }).catch(() => {});
  } catch (_) {}

  let player = null;
  let video = null;
  let mutedByUs = false;
  const SKIP = ".ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern, .ytp-ad-skip-button-slot button";

  function onPlayerState() {
    if (!player) return;
    const adShowing = player.classList.contains("ad-showing") || player.classList.contains("ad-interrupting");
    const v = player.querySelector("video");
    if (v !== video) {
      video = v;
      if (video) video.addEventListener("durationchange", onPlayerState, { passive: true });
    }
    if (adShowing && video) {
      if (!video.muted) { video.muted = true; mutedByUs = true; }
      if (isFinite(video.duration) && video.duration > 0 && video.currentTime < video.duration) {
        video.currentTime = video.duration;
      }
      const skip = player.querySelector(SKIP);
      if (skip) skip.click();
    } else if (!adShowing && mutedByUs && video) {
      video.muted = false;
      mutedByUs = false;
    }
  }

  const observer = new MutationObserver(onPlayerState);

  function attach() {
    const p = document.getElementById("movie_player");
    if (!p || p === player) return;
    if (player) observer.disconnect();
    player = p;
    observer.observe(player, { attributes: true, attributeFilter: ["class"] });
    onPlayerState();
  }

  document.addEventListener("DOMContentLoaded", attach, { once: true });
  window.addEventListener("load", attach, { once: true });
  // YouTube is a single-page app; it fires these on every in-app navigation.
  window.addEventListener("yt-navigate-finish", attach);
  document.addEventListener("yt-navigate-finish", attach);
})();
