// ==UserScript==
// @name         AroLinks Fast-Skip
// @namespace    https://github.com/drarosk-pixel/Ad-Bypass
// @version      1.0.0
// @description  AroLinks par countdown auto-skip + Continue/Get-Link auto-click. CAPTCHA aapko khud solve karna hoga. Aapke apne browser me chalta hai (no server, no bot-detection).
// @author       drarosk
// @match        *://arolinks.com/*
// @match        *://*.arolinks.com/*
// @match        *://techmint.in/*
// @match        *://*.techmint.in/*
// @match        *://onlinewish.in/*
// @match        *://*.onlinewish.in/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

/*
 * Kaam kaise karta hai
 * --------------------
 * Yeh script sirf AADMI ke browser me chalti hai (Tampermonkey/Violentmonkey),
 * isliye Cloudflare/anti-bot pass ho jaata hai. Har ~1 second me yeh:
 *   1. Chhupe hue "Get Link" buttons ko dikhata/enable karta hai (countdown skip).
 *   2. Continue / Get-Link / Readmore buttons ko auto-click karta hai.
 *   3. Jaise hi final destination link (gt-link) milta hai, seedha wahan bhej deta hai.
 *   4. Agar CAPTCHA dikhe, to ruk jaata hai — wo aapko khud solve karna hoga.
 *
 * Nasamajh ads par click na ho, isliye naye tab (target=_blank) wale text-match
 * anchors ko chhod deta hai; sirf shortener ke apne button/id/class click karta hai.
 */

(function () {
  'use strict';

  var CHAIN_HOSTS = /(^|\.)(arolinks\.com|techmint\.in|onlinewish\.in)$/i;
  var BTN_ID_CLS = /(get-link|gt-link|btn6|get_link|continue|readmore|skip-btn|proceed)/i;
  var BTN_TXT = /(get\s*link|continue|proceed|click\s*here|readmore|read\s*more|skip|verify|go\s*to\s*link)/i;
  var CAPTCHA_SEL = [
    "iframe[src*='challenges.cloudflare.com']",
    ".cf-turnstile",
    ".g-recaptcha",
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='hcaptcha.com']",
  ];

  var clicked = new WeakSet();

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

  function captchaWaiting() {
    for (var i = 0; i < CAPTCHA_SEL.length; i++) {
      var el = document.querySelector(CAPTCHA_SEL[i]);
      if (el && visible(el)) return true;
    }
    return false;
  }

  // Chhupe hue get-link buttons + countdown ko fast-forward.
  function revealButtons() {
    var hidden = document.querySelectorAll(
      '#btn6, .get-link, #get-link, #continue, .skip-btn, [id*="get-link"]'
    );
    for (var i = 0; i < hidden.length; i++) {
      var b = hidden[i];
      b.style.display = '';
      b.hidden = false;
      try {
        b.disabled = false;
      } catch (e) {}
      b.classList && b.classList.remove('disabled');
    }
  }

  // AdLinkFly ka final anchor (ya koi bhi prominent off-site link).
  function findDestination() {
    var gt = document.querySelector('a#gt-link, a.get-link[href], a.gt-link[href]');
    if (gt && gt.href && isOffsiteDest(gt.href)) return gt.href;
    return null;
  }

  // Continue / Get-Link buttons ko click karo (ads nahi).
  function autoAdvance() {
    var els = document.querySelectorAll(
      "a, button, input[type='submit'], input[type='button']"
    );
    for (var i = 0; i < els.length; i++) {
      var e = els[i];
      if (clicked.has(e) || !visible(e)) continue;

      var id = e.id || '';
      var cls = (e.className && e.className.toString()) || '';
      var txt = (e.innerText || e.value || '').trim();
      var idClsMatch = BTN_ID_CLS.test(id + ' ' + cls);
      var txtMatch = BTN_TXT.test(txt);

      if (!idClsMatch && !txtMatch) continue;

      // Text-only match + naya tab => sambhavit ad, chhod do.
      if (!idClsMatch && e.tagName === 'A' && e.target === '_blank') continue;

      clicked.add(e);
      setStatus('clicking: ' + (txt || id || cls).slice(0, 24));
      try {
        e.click();
      } catch (err) {}
      return true;
    }
    return false;
  }

  // Chhota status badge upar-right me.
  var badge = null;
  function setStatus(msg) {
    if (!badge) {
      badge = document.createElement('div');
      badge.style.cssText =
        'position:fixed;top:8px;right:8px;z-index:2147483647;' +
        'background:#111;color:#0f0;font:12px monospace;padding:6px 10px;' +
        'border-radius:6px;opacity:.9;max-width:60vw;pointer-events:none';
      (document.body || document.documentElement).appendChild(badge);
    }
    badge.textContent = '[AroLinks Skip] ' + msg;
  }

  var ticks = 0;
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

      revealButtons();

      if (captchaWaiting()) {
        setStatus('CAPTCHA aaya — ise aap solve karein 🙏');
        return; // captcha ke beech interfere na karo
      }

      autoAdvance();
      setStatus('skip chal raha hai… (' + ticks + ')');
    } catch (e) {
      /* keep going */
    }

    // 3 min baad ruk jao (loop se bachne ke liye).
    if (ticks > 150) {
      setStatus('ruk gaya — agar kuch na hua to manually continue karein');
      clearInterval(timer);
    }
  }, 1200);
})();
