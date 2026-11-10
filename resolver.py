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
from dataclasses import dataclass
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


class ResolveError(Exception):
    """Raised when the link cannot be resolved."""


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


def _has_captcha(html: str) -> bool:
    needles = (
        "g-recaptcha",
        "h-captcha",
        "cf-turnstile",
        "challenges.cloudflare.com",
        "www.google.com/recaptcha",
        "hcaptcha.com",
    )
    low = (html or "").lower()
    return any(n in low for n in needles)


class LinkResolver:
    def __init__(self, timeout_seconds: float = 60) -> None:
        self.timeout_seconds = timeout_seconds

    async def resolve(self, raw_url: str) -> ResolveResult:
        url = self._normalize(raw_url)
        host = (urlparse(url).hostname or "").lower()
        if host not in AROLINKS_HOSTS:
            raise ResolveError(
                "Filhaal sirf arolinks.com ke links supported hain. "
                f"(mila: {host or 'invalid'})"
            )
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._resolve_arolinks, url),
                timeout=self.timeout_seconds + 20,
            )
        except TimeoutError:
            return ResolveResult("manual", url, "Resolve me zyada time lag gaya.")

    @staticmethod
    def _normalize(raw_url: str) -> str:
        url = (raw_url or "").strip().strip("<>[](){}.,;!\"'")
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ResolveError("Valid http:// ya https:// arolinks link bhejein.")
        return url

    # ------------------------------------------------------------------ #
    # The actual AroLinks partner-chain bypass (synchronous, threaded).  #
    # ------------------------------------------------------------------ #
    def _resolve_arolinks(self, url: str) -> ResolveResult:
        code = url.rstrip("/").split("/")[-1]
        session = requests.Session()
        session.headers.update({"User-Agent": DESKTOP_UA})

        # Step 1 — load the short link; this sets the ref/session cookies and
        # exposes the techmint.in partner URL.
        try:
            r1 = session.get(url, timeout=15)
        except requests.RequestException as exc:
            raise ResolveError(f"arolinks khula nahi: {type(exc).__name__}") from exc
        if r1.status_code != 200:
            raise ResolveError(f"arolinks ne HTTP {r1.status_code} diya.")

        # If the page already exposes the destination, short-circuit.
        early = self._parse_final_page(r1.text, str(r1.url), session)
        if early is not None and early.status == "final":
            return early

        partner_match = _TECHMINT_RE.search(r1.text)
        if partner_match:
            self._walk_partner_chain(session, url, code, partner_match.group(1))

        # Final step — request the short link again with an onlinewish referer,
        # via cloudscraper to clear the Cloudflare bot gate.
        final_html, final_url = self._fetch_final_page(session, url)
        result = self._parse_final_page(final_html, final_url, session)
        if result is not None:
            return result

        if _has_captcha(final_html):
            return ResolveResult(
                "captcha",
                final_url,
                "Page par CAPTCHA hai — use solve karke final link mil jayega.",
            )
        return ResolveResult(
            "manual",
            final_url,
            "Final link automatically nahi mila — page khud kholkar try karein.",
        )

    def _walk_partner_chain(
        self, session: requests.Session, url: str, code: str, partner: str
    ) -> None:
        """Visit the techmint.in / onlinewish.in article chain so AroLinks
        later serves the timer-free page. Best-effort: failures are tolerated."""
        partner = partner.replace("&amp;", "&")
        try:
            r2 = session.get(partner, headers={"Referer": url}, timeout=30)
            art1 = _js_redirect(r2.text)
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
            if ow_art:
                session.get(ow_art, headers={"Referer": ow1}, timeout=15)

            session.get(
                "https://onlinewish.in/readmore/",
                headers={"Referer": ow_art or ow1},
                timeout=15,
            )
        except requests.RequestException:
            # Partner chain is best-effort; the final fetch may still work.
            return

    def _fetch_final_page(self, session: requests.Session, url: str) -> tuple[str, str]:
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
            return resp.text, str(resp.url)
        except Exception:  # noqa: BLE001 - any cloudscraper failure -> fallback
            # Fall back to the plain session if cloudscraper is unavailable.
            try:
                resp = session.get(url, headers=headers, timeout=30)
                return resp.text, str(resp.url)
            except requests.RequestException as exc:
                raise ResolveError(
                    f"Final page fetch fail: {type(exc).__name__}"
                ) from exc

    def _parse_final_page(
        self, html: str, page_url: str, session: requests.Session
    ) -> ResolveResult | None:
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")

        # Fast path: a gt-link anchor already carries the destination.
        gt_link = soup.find(
            "a", id="gt-link", href=lambda h: bool(h) and h.startswith("http")
        )
        if gt_link:
            return ResolveResult("final", gt_link["href"])

        form = soup.select_one("form#go-link")
        if not form:
            return None

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
            time.sleep(counter + 1)

        try:
            r_go = session.post(
                action,
                data=hidden,
                headers={
                    "Referer": page_url,
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": DESKTOP_UA,
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            raise ResolveError(f"go-link POST fail: {type(exc).__name__}") from exc

        try:
            payload = json.loads(r_go.text)
        except (ValueError, TypeError):
            return None

        destination = payload.get("url")
        if destination:
            return ResolveResult("final", destination)
        return None
