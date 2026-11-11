// ==UserScript==
// @name         AroLinks Fast-Skip
// @namespace    https://github.com/drarosk-pixel/Ad-Bypass
// @version      2.0.0
// @description  AroLinks ad-gate chain (3-4 steps, pipefitter/techmint/onlinewish) auto-continue. Har page ka ad-timer PROPERLY wait karke aage badhta hai (isliye final link reset nahi hota). CAPTCHA aap khud solve karein. Aapke apne browser me chalta hai.
// @author       drarosk
// @match        *://*/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * DESIGN — "patience", not "force-skip"
 * -------------------------------------
 * Ye ad-gates SERVER-SIDE check karte hain ki aapne page ka countdown poora
 * ruke ya nahi. Agar script countdown ko force-skip karke button jaldi daba de,
 * to site poore chain ko STEP 1 par RESET kar deti hai (yahi "final link pe
 * wapas pehle page" wali dikkat thi).
 *
 * Isliye ye script:
 *   - Kisi button ko force-enable NAHI karta.
 *   - Har page par countdown / "wait N seconds" ko POORA wait karta hai.
 *   - Sirf tab click karta hai jab site KHUD button enable + visible kare.
 *   - Jitne bhi steps hon (3/4/5), har naye page par ye dobara chalta hai.
 *   - CAPTCHA ya final/go-link page par RUK jaata hai — wahan aap khud solve
 *     karke 'click here' dabayein (koi interference nahi).
 */

(function () {
  'use strict';

  var CHAIN_HOSTS =
    /(^|\.)(arolinks\.com|techmint\.in|onlinewish\.in|pipefitter\.in)$/i;
  var STEP_MARKER = /currently on step|step\s*\d\s*\/\s*\d|you are on step/i;

  var BTN_ID_CLS =
    /(get-link|gt-link|btn6|get_link|continue|readmore|skip-btn|proceed|next-btn|open-btn|verify-btn)/i;
  // Anchored: poora button text in phrases me se ho (ads match na hon).
  var PROCEED_TXT =
    /^\s*(open|next|continue|proceed|get\s*link|get\s*your\s*link|click\s*here|read\s*more|readmore|skip|verify|go\s*to\s*link|click\s*to\s*verify|click\s*to\s*continue|verify\s*(?:&|and)?\s*continue)\s*>?\s*$/i;
  // Visible countdown element (unit zaroori: "8 seconds" / "7 sec").
  var COUNTDOWN_TXT = /^\s*\d{1,3}\s*(seconds?|sec)\s*$/i;
  var WAIT_TEXT = /wait\s*(\d{1,3})\s*second/i;

  var CAPTCHA_SEL = [
    "iframe[src*='challenges.cloudflare.com']",
    '.cf-turnstile',
    '.g-recaptcha',
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha.com']",
  ];

  var MIN_DWELL_MS = 6000; // har page par kam se kam itna ruko
  var CLICK_PAUSE_MS = 3500; // click ke baad thodi der koi click nahi
  var MAX_TICKS = 360; // ~6 min (1s tick) — 4-5 steps ke liye kaafi

  var pageStart = Date.now();
  var lastClickAt = 0;
  var lastClick = new WeakMap();
  var captchaSeen = false;
  var badge = null;

  function hostIsChain() {
    return CHAIN_HOSTS.test(location.hostname);
  }
  function pageHasStepMarker() {
    var t = (document.body && document.body.innerText) || '';
    return STEP_MARKER.test(t);
  }
  function isActive() {
    return hostIsChain() || pageHasStepMarker();
  }

  function isOffsiteDest(href) {
    try {
      var u = new URL(href, location.href);
      if (!/^https?:$/i.test(u.protocol)) return false;
      return !CHAIN_HOSTS.test(u.hostname);
    } catch (e) {
      return false;
    }
  }

  function visible(el) {
    var r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    var s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none';
  }

  function enabled(el) {
    if (el.disabled) return false;
    if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') return false;
    var cls = (el.className && el.className.toString()) || '';
    return !/\bdisabled\b/.test(cls);
  }

  function captchaWaiting() {
    for (var i = 0; i < CAPTCHA_SEL.length; i++) {
      var el = document.querySelector(CAPTCHA_SEL[i]);
      if (el && visible(el)) return true;
    }
    return false;
  }

  // Final page = arolinks khud / go-link form / gt-link. Yahan click nahi.
  function isFinalPage() {
    if (/(^|\.)arolinks\.com$/i.test(location.hostname)) return true;
    return !!document.querySelector('form#go-link, a#gt-link');
  }

  function findDestination() {
    var gt = document.querySelector(
      'a#gt-link, a.get-link[href], a.gt-link[href]'
    );
    if (gt && gt.href && isOffsiteDest(gt.href)) return gt.href;
    return null;
  }

  // Koi visible countdown timer chal raha hai?
  function countdownRunning() {
    var els = document.querySelectorAll(
      'span, div, p, b, strong, small, button, a, td, h1, h2, h3, h4'
    );
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (!visible(e)) continue;
      if (e.children && e.children.length > 0) continue; // sirf leaf text
      var txt = (e.innerText || e.textContent || '').trim();
      if (COUNTDOWN_TXT.test(txt)) return true;
    }
    return false;
  }

  // Page ka required wait (ms) — "wait N seconds" text ya default dwell.
  function requiredWaitMs() {
    var t = (document.body && document.body.innerText) || '';
    var m = WAIT_TEXT.exec(t);
    if (m) return Math.min(parseInt(m[1], 10), 60) * 1000;
    return MIN_DWELL_MS;
  }

  // Sabse prominent (bada) proceed button — enabled + visible, countdown nahi.
  function pickProceed() {
    var els = document.querySelectorAll(
      "a, button, input[type='submit'], input[type='button']"
    );
    var best = null;
    var bestArea = 0;
    var now = Date.now();
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (!visible(e) || !enabled(e)) continue;

      var txt = (e.innerText || e.value || '').trim();
      if (COUNTDOWN_TXT.test(txt)) continue;

      var id = e.id || '';
      var cls = (e.className && e.className.toString()) || '';
      var idClsMatch = BTN_ID_CLS.test(id + ' ' + cls);
      var txtMatch = PROCEED_TXT.test(txt);
      if (!idClsMatch && !txtMatch) continue;

      // Text-only match + naya tab => sambhavit ad, chhod do.
      if (!idClsMatch && e.tagName === 'A' && e.target === '_blank') continue;

      if (now - (lastClick.get(e) || 0) < 4000) continue; // same button cooldown

      var r = e.getBoundingClientRect();
      var area = r.width * r.height;
      if (area > bestArea) {
        bestArea = area;
        best = e;
      }
    }
    return best;
  }

  function setStatus(msg) {
    if (!badge) {
      badge = document.createElement('div');
      badge.style.cssText =
        'position:fixed;top:8px;right:8px;z-index:2147483647;' +
        'background:#111;color:#0f0;font:12px monospace;padding:6px 10px;' +
        'border-radius:6px;opacity:.92;max-width:72vw;pointer-events:none';
      (document.body || document.documentElement).appendChild(badge);
    }
    badge.textContent = '[AroLinks] ' + msg;
  }

  function startWorker() {
    var ticks = 0;
    var timer = setInterval(function () {
      ticks++;
      try {
        // 1) Final destination mil gaya -> seedha wahan.
        var dest = findDestination();
        if (dest) {
          setStatus('final link mila, ja rahe hain…');
          clearInterval(timer);
          location.href = dest;
          return;
        }

        // 2) CAPTCHA / final page -> script RUK jaata hai (reset se bachne ke
        //    liye). Aap khud solve karke 'click here' dabayein.
        if (captchaWaiting()) captchaSeen = true;
        if (captchaSeen || isFinalPage()) {
          setStatus("final/CAPTCHA page — aap khud 'click here' dabayein 🙏");
          return;
        }

        // 3) Ad-timer POORA wait karo (warna chain reset ho jaata hai).
        var elapsed = Date.now() - pageStart;
        var need = requiredWaitMs();
        if (countdownRunning() || elapsed < need) {
          var left = Math.max(0, Math.ceil((need - elapsed) / 1000));
          setStatus('ad timer wait… ' + (countdownRunning() ? '(countdown)' : left + 's'));
          return;
        }

        // 4) Click ke turant baad dobara click na karo.
        if (Date.now() - lastClickAt < CLICK_PAUSE_MS) {
          setStatus('aage badh rahe hain…');
          return;
        }

        // 5) Prominent proceed button click karo.
        var btn = pickProceed();
        if (btn) {
          lastClick.set(btn, Date.now());
          lastClickAt = Date.now();
          var label = (btn.innerText || btn.value || btn.id || '').trim();
          setStatus('clicking: ' + label.slice(0, 24));
          try {
            btn.click();
          } catch (e) {}
        } else if (elapsed > 25000) {
          setStatus('button nahi mila — ek baar koi image/ad tap karein 👆');
        } else {
          setStatus('button ka wait…');
        }
      } catch (e) {
        /* keep going */
      }

      if (ticks > MAX_TICKS) {
        setStatus('ruk gaya — manually continue karein');
        clearInterval(timer);
      }
    }, 1000);
  }

  if (isActive()) {
    startWorker();
  } else {
    var checks = 0;
    var w = setInterval(function () {
      checks++;
      if (isActive()) {
        clearInterval(w);
        startWorker();
      } else if (checks >= 4) {
        clearInterval(w);
      }
    }, 1500);
  }
})();
