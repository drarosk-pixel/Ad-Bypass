"""AroLinks (arolinks.com) final-link resolver.

AroLinks is an AdLinkFly-based shortener. The ad/countdown page never exposes
the destination directly; the site first wants the visitor to pass through a
"partner" article chain (techmint.in -> onlinewish.in). Once a request to the
short link arrives with an ``onlinewish.in`` referer, AroLinks serves the
timer-free variant whose ``form#go-link`` can be POSTed to ``/links/go`` to get
the real URL as JSON.

This module reimplements that documented flow with plain HTTP (requests +
cloudscraper) run in a worker thread. No browser is needed.

Flow derived from the public reference implementation:
https://github.com/MeherMankar/FZBypassBot/blob/main/FZBypass/bypass/ddl.py
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import cloudscraper
import requests
from bs4 import BeautifulSoup

AROLINKS_HOSTS = {"arolinks.com", "www.arolinks.com"}

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

_JS_REDIRECT_RE = re.compile(
    r"""(?:window|document)\.location(?:\.href)?\s*=\s*
        ["']((?:https?:)?(?:\\?/\\?/|//)[^"']+)["']""",
    re.IGNORECASE | re.VERBOSE,
)
_TECHMINT_RE = re.compile(
    r'href=["\'](https://techmint\.in[^"\']+)["\']', re.IGNORECASE
)
_COUNTER_RE = re.compile(r'"counter_value"\s*:\s*(\d+)')

_CAPTCHA_MARKERS = {
    "reCAPTCHA": ("g-recaptcha", "www.google.com/recaptcha", "grecaptcha"),
    "hCaptcha": ("h-captcha", "hcaptcha.com"),
    "Cloudflare Turnstile": ("cf-turnstile",),
    "Cloudflare challenge": ("challenges.cloudflare.com", "cf-chl", "cf_chl_opt"),
}


class ResolveError(Exception):
    """Raised when the link cannot be resolved."""


@dataclass
class _Trace:
    """Collects a human-readable, step-by-step report for /debug."""

    lines: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.lines.append(msg)

    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class ResolveResult:
    # "final"   -> url is the real destination
    # "captcha" -> url is a page the user must open and solve a CAPTCHA on
    # "manual"  -> url is the furthest page reached; user continues by hand
    status: str
    url: str
    note: str = ""


def _js_redirect(text: str) -> str | None:
    match = _JS_REDIRECT_RE.search(text or "")
    if not match:
        return None
    return match.group(1).replace("\\/", "/")


def _captcha_markers(html: str) -> list[str]:
    low = (html or "").lower()
    found = []
    for label, needles in _CAPTCHA_MARKERS.items():
        if any(n in low for n in needles):
            found.append(label)
    return found


def _page_title(html: str) -> str:
    match = re.search(
        r"<title[^>]*>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL
    )
    return match.group(1).strip()[:120] if match else "(no title)"


class LinkResolver:
    def __init__(self, timeout_seconds: float = 60) -> None:
        self.timeout_seconds = timeout_seconds

    async def resolve(self, raw_url: str) -> ResolveResult:
        url = self._check_host(self._normalize(raw_url))
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._resolve_arolinks, url, None),
                timeout=self.timeout_seconds + 20,
            )
        except TimeoutError:
            return ResolveResult("manual", url, "Resolve me zyada time lag gaya.")

    async def diagnose(self, raw_url: str) -> str:
        """Run the resolver with full step logging; return the report text."""
        trace = _Trace()
        try:
            url = self._check_host(self._normalize(raw_url))
            trace.add(f"Input : {url}")
            result = await asyncio.wait_for(
                asyncio.to_thread(self._resolve_arolinks, url, trace),
                timeout=self.timeout_seconds + 20,
            )
            trace.add(f"RESULT: status={result.status}")
            trace.add(f"        url={result.url}")
            if result.note:
                trace.add(f"        note={result.note}")
        except TimeoutError:
            trace.add("RESULT: TIMEOUT")
        except ResolveError as exc:
            trace.add(f"RESULT: ERROR — {exc}")
        return trace.text()

    @staticmethod
    def _normalize(raw_url: str) -> str:
        url = (raw_url or "").strip().strip("<>[](){}.,;!\"'")
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ResolveError("Valid http:// ya https:// arolinks link bhejein.")
        return url

    @staticmethod
    def _check_host(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if host not in AROLINKS_HOSTS:
            raise ResolveError(
                "Filhaal sirf arolinks.com ke links supported hain. "
                f"(mila: {host or 'invalid'})"
            )
        return url

    # ------------------------------------------------------------------ #
    # The actual AroLinks partner-chain bypass (synchronous, threaded).  #
    # ------------------------------------------------------------------ #
    def _resolve_arolinks(self, url: str, trace: _Trace | None) -> ResolveResult:
        def log(msg: str) -> None:
            if trace is not None:
                trace.add(msg)

        code = url.rstrip("/").split("/")[-1]
        session = requests.Session()
        session.headers.update({"User-Agent": DESKTOP_UA})

        # Step 1 — load the short link (sets ref/session cookies).
        try:
            r1 = session.get(url, timeout=15)
        except requests.RequestException as exc:
            raise ResolveError(f"arolinks khula nahi: {type(exc).__name__}") from exc
        log(f"1) GET short link   -> HTTP {r1.status_code}, len={len(r1.text)}")
        log(f"   title: {_page_title(r1.text)}")
        if r1.status_code != 200:
            raise ResolveError(f"arolinks ne HTTP {r1.status_code} diya.")

        early = self._parse_final_page(r1.text, str(r1.url), session, trace, "step1")
        if early is not None and early.status == "final":
            log("   -> destination already present on first page")
            return early

        partner_match = _TECHMINT_RE.search(r1.text)
        if partner_match:
            log(f"2) techmint partner -> {partner_match.group(1)[:90]}")
            self._walk_partner_chain(session, url, code, partner_match.group(1), trace)
        else:
            log("2) techmint partner -> NOT FOUND on first page")

        # Final step — request the short link again with an onlinewish referer.
        # IMPORTANT: keep using the SAME client (cloudscraper) for the follow-up
        # POST, else /links/go lacks Cloudflare's cf_clearance cookie and returns
        # a 403 "Just a moment" challenge.
        final_html, final_url, via, client = self._fetch_final_page(session, url, trace)
        log(f"3) final GET ({via}) -> len={len(final_html)}")
        log(f"   title: {_page_title(final_html)}")

        result = self._parse_final_page(final_html, final_url, client, trace, "final")
        if result is not None:
            return result

        markers = _captcha_markers(final_html)
        if markers:
            log(f"   captcha markers: {', '.join(markers)}")
            return ResolveResult(
                "captcha",
                final_url,
                f"Page par: {', '.join(markers)}. Isse solve karke final link milega.",
            )
        log("   no go-link form, no gt-link, no captcha marker found")
        return ResolveResult(
            "manual",
            final_url,
            "Final link automatically nahi mila — page khud kholkar try karein.",
        )

    def _walk_partner_chain(
        self,
        session: requests.Session,
        url: str,
        code: str,
        partner: str,
        trace: _Trace | None,
    ) -> None:
        """Visit the techmint.in / onlinewish.in article chain. Best-effort."""

        def log(msg: str) -> None:
            if trace is not None:
                trace.add(msg)

        partner = partner.replace("&amp;", "&")
        try:
            r2 = session.get(partner, headers={"Referer": url}, timeout=30)
            art1 = _js_redirect(r2.text)
            log(f"   2a techmint landing HTTP {r2.status_code}, art1={bool(art1)}")
            if art1:
                session.get(art1, headers={"Referer": partner}, timeout=15)

            r_rm = session.get(
                "https://techmint.in/readmore/",
                headers={"Referer": art1 or partner},
                timeout=15,
            )
            tl2 = _js_redirect(r_rm.text) or (
                "https://techmint.in/studyeducations/"
                f"?educationsscholorships={code}&pgtr=10&st=2"
            )

            r3 = session.get(tl2, headers={"Referer": art1 or partner}, timeout=15)
            art2 = _js_redirect(r3.text) or art1
            if art2:
                session.get(art2, headers={"Referer": tl2}, timeout=15)

            ow1 = (
                "https://onlinewish.in/studyblogs/educationsunivrsties/"
                f"?univrsityinsurances={code}"
            )
            r4 = session.get(ow1, headers={"Referer": art2 or tl2}, timeout=15)
            ow_art = _js_redirect(r4.text)
            log(f"   2b onlinewish landing HTTP {r4.status_code}, art={bool(ow_art)}")
            if ow_art:
                session.get(ow_art, headers={"Referer": ow1}, timeout=15)

            session.get(
                "https://onlinewish.in/readmore/",
                headers={"Referer": ow_art or ow1},
                timeout=15,
            )
            log("   2c onlinewish session established")
        except requests.RequestException as exc:
            log(
                f"   partner chain error: {type(exc).__name__} (best-effort, continuing)"
            )

    def _fetch_final_page(
        self, session: requests.Session, url: str, trace: _Trace | None
    ) -> tuple[str, str, str, object]:
        headers = {"Referer": "https://onlinewish.in/", "User-Agent": DESKTOP_UA}
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            for cookie in session.cookies:
                scraper.cookies.set(
                    cookie.name,
                    cookie.value,
                    domain=cookie.domain or "arolinks.com",
                )
            resp = scraper.get(url, headers=headers, timeout=30)
            return resp.text, str(resp.url), "cloudscraper", scraper
        except Exception as exc:  # noqa: BLE001 - any cloudscraper failure -> fallback
            if trace is not None:
                trace.add(
                    f"   cloudscraper failed: {type(exc).__name__}; using requests"
                )
            try:
                resp = session.get(url, headers=headers, timeout=30)
                return resp.text, str(resp.url), "requests-fallback", session
            except requests.RequestException as rexc:
                raise ResolveError(
                    f"Final page fetch fail: {type(rexc).__name__}"
                ) from rexc

    def _parse_final_page(
        self,
        html: str,
        page_url: str,
        client: object,
        trace: _Trace | None,
        where: str,
    ) -> ResolveResult | None:
        def log(msg: str) -> None:
            if trace is not None:
                trace.add(msg)

        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        gt_link = soup.find(
            "a", id="gt-link", href=lambda h: bool(h) and h.startswith("http")
        )
        if gt_link:
            log(f"   [{where}] gt-link anchor FOUND")
            return ResolveResult("final", gt_link["href"])

        form = soup.select_one("form#go-link")
        if not form:
            log(f"   [{where}] no form#go-link")
            return None
        log(f"   [{where}] form#go-link FOUND -> POSTing")

        hidden = {
            inp.get("name"): inp.get("value", "")
            for inp in form.find_all("input")
            if inp.get("name")
        }
        action = form.get("action") or "/links/go"
        if not action.startswith("http"):
            action = urljoin("https://arolinks.com", action)

        counter_match = _COUNTER_RE.search(html)
        counter = int(counter_match.group(1)) if counter_match else 0
        if 0 < counter <= 90:
            log(f"   [{where}] counter={counter}s, waiting")
            time.sleep(counter + 1)

        try:
            r_go = client.post(
                action,
                data=hidden,
                headers={
                    "Referer": page_url,
                    "Origin": "https://arolinks.com",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            raise ResolveError(f"go-link POST fail: {type(exc).__name__}") from exc

        log(
            f"   [{where}] /links/go -> HTTP {r_go.status_code}, body={r_go.text[:150]}"
        )
        try:
            payload = json.loads(r_go.text)
        except (ValueError, TypeError):
            if "just a moment" in r_go.text.lower():
                raise ResolveError(
                    "Cloudflare ne POST block kiya (cf_clearance missing). "
                    "cloudscraper client POST ke liye use hona chahiye."
                ) from None
            return None

        destination = payload.get("url")
        if destination:
            return ResolveResult("final", destination)
        return None
