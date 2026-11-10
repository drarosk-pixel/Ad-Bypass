// ==UserScript==
// @name         AroLinks Fast-Skip
// @namespace    https://github.com/drarosk-pixel/Ad-Bypass
// @version      1.5.0
// @description  AroLinks + uske partner "step X/3" ad-gate pages par Open/Continue/Get-Link/Verify auto-click + countdown skip. CAPTCHA aapko khud solve karna hoga. Aapke apne browser me chalta hai.
// @author       drarosk
// @match        *://*/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * v1.1.0 wala stable behaviour + 2nd page ("click to verify" + countdown) ki fix.
 *
 * Active sirf tab hoti hai jab hostname AroLinks chain ka ho YA page par
 * "step X/3" / "currently on step" marker ho. Normal sites par kuch nahi karti.
 *
 * Active hone par har ~1.2s me:
 *   1. Chhupe hue get-link buttons dikhati/enable karti hai (countdown skip).
 *   2. Sabse PROMINENT (bada) proceed button click karti hai — "Open /
 *      Continue / Get Link / click to verify". Chhote ad buttons aur naye-tab
 *      wale ad links chhod deti hai. Disabled/countdown button ka WAIT karti
 *      hai aur enable hote hi click karti hai.
 *   3. Final destination link milte hi seedha wahan bhejti hai.
 *   4. CAPTCHA dikhe to ruk jaati hai — wo aap solve karein.
 */

(function () {
  'use strict';

  var CHAIN_HOSTS =
    /(^|\.)(arolinks\.com|techmint\.in|onlinewish\.in|pipefitter\.in)$/i;
  var STEP_MARKER = /currently on step|step\s*\d\s*\/\s*\d|you are on step/i;

  var BTN_ID_CLS =
    /(get-link|gt-link|btn6|get_link|continue|readmore|skip-btn|proceed|next-btn|open-btn|verify-btn)/i;
  // Anchored — poora button text in phrases me se hona chahiye (ads nahi).
  var PROCEED_TXT =
    /^\s*(open|next|continue|proceed|get\s*link|click\s*here|read\s*more|readmore|skip|verify|go\s*to\s*link|click\s*to\s*verify|click\s*to\s*continue|verify\s*(?:&|and)?\s*continue)\s*>?\s*$/i;
  var COUNTDOWN_TXT = /^\s*\d+\s*(seconds?|sec|s)\s*$/i;

  var CAPTCHA_SEL = [
    "iframe[src*='challenges.cloudflare.com']",
    '.cf-turnstile',
    '.g-recaptcha',
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha.com']",
  ];

  var lastClick = new WeakMap();
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

  // Final page = arolinks khud / go-link form / gt-link / koi visible captcha.
  // In pages par script kuch click NAHI karegi (interference se chain restart
  // hota tha). Sirf beech ke ad "step X/3" pages par auto-click.
  function isFinalPage() {
    if (/(^|\.)arolinks\.com$/i.test(location.hostname)) return true;
    if (document.querySelector('form#go-link, a#gt-link')) return true;
    return captchaWaiting();
  }

  function findDestination() {
    var gt = document.querySelector(
      'a#gt-link, a.get-link[href], a.gt-link[href]'
    );
    if (gt && gt.href && isOffsiteDest(gt.href)) return gt.href;
    return null;
  }

  // Sabse bada (prominent) proceed button chuno — disabled/countdown skip.
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

      if (!idClsMatch && e.tagName === 'A' && e.target === '_blank') continue;

      // Cooldown: disabled->enable ka wait; same button spam na ho.
      if (now - (lastClick.get(e) || 0) < 2500) continue;

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
        'border-radius:6px;opacity:.9;max-width:70vw;pointer-events:none';
      (document.body || document.documentElement).appendChild(badge);
    }
    badge.textContent = '[AroLinks Skip] ' + msg;
  }

  function startWorker() {
    var ticks = 0;
    var captchaSeen = false;
    var timer = setInterval(function () {
      ticks++;
      try {
        var dest = findDestination();
        if (dest) {
          setStatus('final link mila, ja rahe hain…');
          clearInterval(timer);
          location.href = dest;
          return;
        }

        if (captchaWaiting()) captchaSeen = true;

        // Final / CAPTCHA / go-link page par script RUK jaata hai — koi click
        // nahi. Yahan AAP khud captcha solve karke 'click here' dabayein; sirf
        // beech ke "step X/3" ad pages auto-skip hote hain.
        if (captchaSeen || isFinalPage()) {
          setStatus("final/CAPTCHA page — aap khud 'click here' dabayein 🙏");
          return;
        }

        var btn = pickProceed();
        if (btn) {
          lastClick.set(btn, Date.now());
          var label = (btn.innerText || btn.value || btn.id || '').trim();
          setStatus('clicking: ' + label.slice(0, 24));
          try {
            btn.click();
          } catch (e) {}
        } else {
          setStatus('wait… (' + ticks + ')');
        }
      } catch (e) {
        /* keep going */
      }

      if (ticks > 150) {
        setStatus('ruk gaya — manually continue karein');
        clearInterval(timer);
      }
    }, 1200);
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
