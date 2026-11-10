"""AroLinks (arolinks.com) final-link resolver — real-browser edition.

AroLinks is an AdLinkFly-based shortener sitting behind Cloudflare. The go-link
page loads, but the follow-up ``/links/go`` request is protected by a Cloudflare
"managed challenge" that plain HTTP clients (requests / cloudscraper) cannot
clear. The reliable way through is a REAL browser that executes Cloudflare's JS
challenge and then runs the page's own JavaScript flow (countdown -> click ->
``/links/go`` -> destination).

This module drives headful Chromium via Playwright (run under Xvfb in Docker),
clears Cloudflare, waits out the countdown, clicks the continue/get-link
control, and captures the destination from the ``/links/go`` response, a
revealed anchor, or an off-site navigation.

If Cloudflare shows an INTERACTIVE challenge (checkbox / Turnstile) it cannot be
solved on a headless server — the link is handed back for the user to open.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from playwright.async_api import Browser, Page, Playwright, Response, async_playwright
from playwright.async_api import Error as PlaywrightError

AROLINKS_HOSTS = {"arolinks.com", "www.arolinks.com"}

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

_COUNTER_RE = re.compile(r'"counter_value"\s*:\s*(\d+)')

# Cloudflare interstitial / challenge fingerprints.
_CF_MARKERS = ("just a moment", "challenge-platform", "cf-chl", "cf_chl_opt")

# Buttons that advance the AdLinkFly flow after the countdown.
_CONTINUE_SELECTORS = (
    "#go-link button[type='submit']",
    "#go-link input[type='submit']",
    "button#btn6",
    "#btn6",
    "#get-link",
    ".get-link",
    "button:has-text('Get Link')",
    "a:has-text('Get Link')",
    "button:has-text('Continue')",
    "a:has-text('Continue')",
    "button:has-text('Click here')",
    "a:has-text('Click here')",
)

# Interactive challenge widgets that need a human.
_INTERACTIVE_CAPTCHA = (
    "iframe[src*='challenges.cloudflare.com']",
    ".cf-turnstile",
    "iframe[src*='recaptcha/api2/bframe']",  # reCAPTCHA v2 image challenge
    "iframe[src*='hcaptcha.com']",
)


class ResolveError(Exception):
    """Raised when the link cannot be resolved."""


@dataclass
class _Trace:
    lines: list[str] = field(default_factory=list)

    def add(self, msg: str) -> None:
        self.lines.append(msg)

    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass(frozen=True)
class ResolveResult:
    # "final"   -> url is the real destination
    # "captcha" -> interactive challenge; user must open the page and solve it
    # "manual"  -> furthest page reached; user continues by hand
    status: str
    url: str
    note: str = ""


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _is_arolinks(url: str) -> bool:
    return _host(url) in AROLINKS_HOSTS


def _looks_like_destination(url: str) -> bool:
    return url.startswith(("http://", "https://")) and not _is_arolinks(url)


class LinkResolver:
    def __init__(self, timeout_seconds: float = 90) -> None:
        self.timeout_seconds = timeout_seconds
        self.headless = os.getenv("HEADLESS", "false").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._browser is not None:
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._launch()

    async def _launch(self) -> Browser:
        assert self._playwright is not None
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        try:
            return await self._playwright.chromium.launch(
                headless=self.headless, args=args
            )
        except PlaywrightError:
            # No display available for headful -> fall back to headless.
            return await self._playwright.chromium.launch(headless=True, args=args)

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def resolve(self, raw_url: str) -> ResolveResult:
        return await self._run(self._check(raw_url), None)

    async def diagnose(self, raw_url: str) -> str:
        trace = _Trace()
        try:
            url = self._check(raw_url)
            trace.add(f"Input : {url}")
            result = await self._run(url, trace)
            trace.add(f"RESULT: status={result.status}")
            trace.add(f"        url={result.url}")
            if result.note:
                trace.add(f"        note={result.note}")
        except ResolveError as exc:
            trace.add(f"RESULT: ERROR — {exc}")
        return trace.text()

    @staticmethod
    def _check(raw_url: str) -> str:
        url = (raw_url or "").strip().strip("<>[](){}.,;!\"'")
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ResolveError("Valid http:// ya https:// arolinks link bhejein.")
        if not _is_arolinks(url):
            raise ResolveError(
                "Filhaal sirf arolinks.com ke links supported hain. "
                f"(mila: {parsed.hostname})"
            )
        return url

    async def _run(self, url: str, trace: _Trace | None) -> ResolveResult:
        def log(msg: str) -> None:
            if trace is not None:
                trace.add(msg)

        async with self._lock:
            if self._browser is None:
                await self.start()
            try:
                return await asyncio.wait_for(
                    self._drive(url, log), timeout=self.timeout_seconds + 20
                )
            except TimeoutError:
                return ResolveResult("manual", url, "Resolve me zyada time lag gaya.")

    async def _drive(self, url, log) -> ResolveResult:
        assert self._browser is not None
        context = await self._browser.new_context(
            user_agent=DESKTOP_UA,
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )
        destinations: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        async def on_response(resp: Response) -> None:
            u = resp.url
            if "links/go" not in u and "/ajax" not in u:
                return
            try:
                ctype = (resp.headers or {}).get("content-type", "").lower()
                body = await resp.text()
            except PlaywrightError:
                return
            log(f"   xhr {resp.status} {u.split('?')[0]} :: {body[:120]}")
            if "json" not in ctype and not body.strip().startswith("{"):
                return
            match = re.search(r'"url"\s*:\s*"(https?:[^"]+)"', body)
            if match:
                await destinations.put(("links/go", match.group(1).replace("\\/", "/")))

        page = await context.new_page()
        page.on("response", on_response)

        try:
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=min(int(self.timeout_seconds * 1000), 45_000),
            )

            await self._clear_cloudflare(page, log)

            # Read the countdown value from the page, if present.
            html = await self._content(page)
            counter_match = _COUNTER_RE.search(html)
            counter = int(counter_match.group(1)) if counter_match else 0
            log(f"   counter_value={counter}")
            if 0 < counter <= 90:
                await page.wait_for_timeout((counter + 2) * 1000)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout_seconds
            clicked: set[str] = set()

            while loop.time() < deadline:
                # 1. destination captured from a /links/go response
                if not destinations.empty():
                    _src, dest = await destinations.get()
                    if _looks_like_destination(dest):
                        log(f"   destination via {_src}: {dest}")
                        return ResolveResult("final", dest)

                # 2. browser navigated off arolinks
                if _looks_like_destination(page.url):
                    log(f"   navigated to destination: {page.url}")
                    return ResolveResult("final", page.url)

                # 3. revealed anchor (gt-link) carrying the destination
                dest = await self._revealed_link(page)
                if dest:
                    log(f"   destination via anchor: {dest}")
                    return ResolveResult("final", dest)

                # 4. advance the flow by clicking a continue/get-link control
                await self._click_continue(page, clicked, log)
                await page.wait_for_timeout(1_500)

            # Deadline — decide why we failed.
            if await self._interactive_captcha(page):
                return ResolveResult(
                    "captcha",
                    page.url,
                    "Interactive challenge (Cloudflare/captcha) — khud kholkar solve karein.",
                )
            return ResolveResult(
                "manual", page.url, "Final link automatically nahi mila."
            )
        finally:
            page.remove_listener("response", on_response)
            await context.close()

    async def _clear_cloudflare(self, page: Page, log) -> None:
        """Wait for the Cloudflare 'Just a moment' interstitial to pass."""
        for _ in range(30):
            title = (await self._title(page)).lower()
            body = (await self._content(page)).lower()
            if not any(m in title or m in body for m in _CF_MARKERS):
                return
            await page.wait_for_timeout(1_000)
        log("   cloudflare interstitial did not clear in time")

    async def _click_continue(self, page: Page, clicked: set[str], log) -> None:
        for selector in _CONTINUE_SELECTORS:
            if selector in clicked:
                continue
            try:
                locator = page.locator(selector).first
                if (
                    await locator.count()
                    and await locator.is_visible()
                    and await locator.is_enabled()
                ):
                    await locator.click(timeout=3_000)
                    clicked.add(selector)
                    log(f"   clicked: {selector}")
                    return
            except PlaywrightError:
                continue

    async def _revealed_link(self, page: Page) -> str | None:
        for selector in ("a#gt-link", "a.get-link[href^='http']", "#gt-link a"):
            try:
                locator = page.locator(selector).first
                if await locator.count():
                    href = await locator.get_attribute("href")
                    if href and _looks_like_destination(href):
                        return href
            except PlaywrightError:
                continue
        return None

    async def _interactive_captcha(self, page: Page) -> bool:
        for selector in _INTERACTIVE_CAPTCHA:
            try:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    return True
            except PlaywrightError:
                continue
        return False

    @staticmethod
    async def _content(page: Page) -> str:
        try:
            return await asyncio.wait_for(page.content(), timeout=5)
        except (PlaywrightError, TimeoutError):
            return ""

    @staticmethod
    async def _title(page: Page) -> str:
        try:
            return await asyncio.wait_for(page.title(), timeout=5)
        except (PlaywrightError, TimeoutError):
            return ""
