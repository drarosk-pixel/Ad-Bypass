from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    Response,
    Route,
    async_playwright,
)
from playwright.async_api import Error as PlaywrightError

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
URL_KEYS = {
    "url",
    "destination",
    "destination_url",
    "final_url",
    "original_url",
    "redirect",
}
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
META_REFRESH_RE = re.compile(
    r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+content=[\"'][^\"']*url\s*=\s*([^\"']+)",
    re.IGNORECASE,
)
JSON_URL_RE = re.compile(
    r"[\"\'](?:destination|destination_url|final_url|original_url|redirect)[\"\']\s*:\s*[\"\'](https?:\\?/\\?/[^\"\']+)",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(
    r"(?:window\.)?location(?:\.href)?\s*=\s*[\"'](https?://[^\"']+)",
    re.IGNORECASE,
)


class ResolutionError(Exception):
    """Raised when a link cannot be resolved safely."""


@dataclass(frozen=True)
class ResolutionResult:
    url: str
    method: str
    # True  -> fully resolved final destination
    # False -> intermediate page (CAPTCHA / manual step) for the user to continue
    final: bool = True


class LinkResolver:
    def __init__(
        self,
        allowed_hosts: set[str],
        timeout_seconds: float = 75,
        max_concurrent: int = 2,
    ) -> None:
        self.allowed_hosts = {host.lower().rstrip(".") for host in allowed_hosts}
        self.timeout_seconds = timeout_seconds
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        if self._browser is not None:
            return
        playwright = await async_playwright().start()
        self._playwright = playwright
        self._browser = await playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def resolve(self, raw_url: str) -> ResolutionResult:
        url = self._normalize_url(raw_url)
        self._ensure_allowed_initial_host(url)
        await self._ensure_public_url(url)

        async with self._semaphore:
            direct_result = await self._resolve_http(url)
            if direct_result is not None:
                return direct_result

            if self._browser is None:
                await self.start()
            try:
                return await asyncio.wait_for(
                    self._resolve_browser(url),
                    timeout=self.timeout_seconds + 15,
                )
            except TimeoutError:
                # Hard safety net: hand back the entry URL so the user can
                # at least open the flow and continue manually.
                return ResolutionResult(url, "manual", final=False)

    @staticmethod
    def _normalize_url(raw_url: str) -> str:
        url = raw_url.strip().strip('<>[](){}.,;!"')
        parsed = urlparse(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ResolutionError("Sirf valid http:// ya https:// URL bhejein.")
        if parsed.username or parsed.password:
            raise ResolutionError("Username/password wala URL allowed nahi hai.")
        return url

    def _ensure_allowed_initial_host(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if host not in self.allowed_hosts:
            allowed = ", ".join(sorted(self.allowed_hosts))
            raise ResolutionError(
                f"Yeh bot filhaal sirf in hosts ko accept karta hai: {allowed}"
            )

    async def _resolve_http(self, start_url: str) -> ResolutionResult | None:
        current = start_url
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(15.0),
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
            ) as client:
                for _ in range(10):
                    await self._ensure_public_url(current)
                    response = await client.get(current)

                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            return None
                        next_url = urljoin(current, location)
                        if not self._is_allowed_host(next_url):
                            await self._ensure_public_url(next_url)
                            return ResolutionResult(next_url, "HTTP redirect")
                        current = next_url
                        continue

                    if response.status_code >= 400:
                        return None

                    candidate = await self._candidate_from_text(response.text, current)
                    if candidate:
                        return ResolutionResult(candidate, "page metadata")
                    return None
        except (httpx.HTTPError, ResolutionError, UnicodeError):
            return None

        raise ResolutionError("Redirect chain bahut lambi hai.")

    async def _resolve_browser(self, start_url: str) -> ResolutionResult:
        assert self._browser is not None
        context = await self._browser.new_context(
            user_agent=USER_AGENT,
            accept_downloads=False,
            java_script_enabled=True,
        )
        dns_cache: dict[str, bool] = {}
        discovered: asyncio.Queue[str] = asyncio.Queue()

        async def route_guard(route: Route) -> None:
            request_url = route.request.url
            parsed = urlparse(request_url)
            if parsed.scheme not in {"http", "https"}:
                await route.continue_()
                return

            host = (parsed.hostname or "").lower().rstrip(".")
            allowed = dns_cache.get(host)
            if allowed is None:
                try:
                    await self._ensure_public_url(request_url)
                    allowed = True
                except ResolutionError:
                    allowed = False
                dns_cache[host] = allowed

            if allowed:
                await route.continue_()
            else:
                await route.abort("blockedbyclient")

        await context.route("**/*", route_guard)
        page = await context.new_page()

        async def inspect_response(response: Response) -> None:
            try:
                if not self._is_allowed_host(response.url):
                    return
                content_type = (
                    await response.header_value("content-type") or ""
                ).lower()
                if "json" not in content_type:
                    return
                data = await response.json()
                candidate = self._url_from_json(data)
                if candidate:
                    await discovered.put(candidate)
            except (PlaywrightError, TypeError, ValueError):
                return

        def on_response(response: Response) -> None:
            asyncio.create_task(inspect_response(response))

        page.on("response", on_response)

        try:
            await page.goto(
                start_url,
                wait_until="domcontentloaded",
                timeout=min(int(self.timeout_seconds * 1000), 30_000),
            )

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout_seconds - 1
            clicked_selectors: set[str] = set()
            selectors = [
                "form#go-link button[type='submit']",
                "form#go-link input[type='submit']",
                "#get-link",
                "button.get-link",
                "a.get-link",
                "#continue",
                "button:has-text('Continue')",
                "a:has-text('Continue')",
                "button:has-text('Get Link')",
                "a:has-text('Get Link')",
            ]
            furthest_url = page.url

            while loop.time() < deadline:
                # 1. Final URL discovered via a background JSON/API response.
                while not discovered.empty():
                    candidate = await discovered.get()
                    accepted = await self._accept_candidate(candidate, page.url)
                    if accepted:
                        return ResolutionResult(accepted, "shortener API", final=True)

                # 2. Browser navigated off the shortener -> that's the destination.
                if page.url.startswith(
                    ("http://", "https://")
                ) and not self._is_allowed_host(page.url):
                    await self._ensure_public_url(page.url)
                    return ResolutionResult(page.url, "browser redirect", final=True)

                furthest_url = page.url

                # 3. Final URL embedded in the page markup/scripts.
                page_html = await self._safe_content(page)
                candidate = await self._candidate_from_text(page_html, page.url)
                if candidate:
                    return ResolutionResult(candidate, "page script", final=True)

                # 4. CAPTCHA reached -> hand this page to the user to solve.
                if await self._captcha_visible(page):
                    return ResolutionResult(page.url, "captcha", final=False)

                # 5. Otherwise advance the ad/countdown flow by clicking.
                clicked = False
                for selector in selectors:
                    if selector in clicked_selectors:
                        continue
                    locator = page.locator(selector).first
                    try:
                        if (
                            await locator.count()
                            and await locator.is_visible()
                            and await locator.is_enabled()
                        ):
                            await locator.click(timeout=3_000)
                            clicked_selectors.add(selector)
                            clicked = True
                            break
                    except PlaywrightError:
                        continue

                if clicked:
                    await page.wait_for_timeout(2_000)
                else:
                    # Wait out countdown timers; buttons may enable later.
                    await page.wait_for_timeout(1_500)

            # Deadline hit. Prefer a CAPTCHA page, else the furthest page reached,
            # so the user can finish the remaining steps by hand.
            if await self._captcha_visible(page):
                return ResolutionResult(page.url, "captcha", final=False)
            return ResolutionResult(furthest_url or start_url, "manual", final=False)
        finally:
            page.remove_listener("response", on_response)
            await context.close()

    @staticmethod
    async def _safe_content(page: Page) -> str:
        try:
            return await asyncio.wait_for(page.content(), timeout=5)
        except (PlaywrightError, TimeoutError):
            return ""

    async def _candidate_from_text(self, text: str, base_url: str) -> str | None:
        decoded = html.unescape(text)
        raw_candidates: list[str] = []

        meta_match = META_REFRESH_RE.search(decoded)
        if meta_match:
            raw_candidates.append(urljoin(base_url, meta_match.group(1).strip()))

        raw_candidates.extend(
            match.replace("\\/", "/") for match in JSON_URL_RE.findall(decoded)
        )
        raw_candidates.extend(LOCATION_RE.findall(decoded))

        for candidate in raw_candidates:
            accepted = await self._accept_candidate(candidate, base_url)
            if accepted:
                return accepted
        return None

    async def _accept_candidate(self, candidate: str, base_url: str) -> str | None:
        candidate = html.unescape(candidate).replace("\\/", "/").strip()
        candidate = urljoin(base_url, candidate)
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if self._is_allowed_host(candidate):
            return None
        try:
            await self._ensure_public_url(candidate)
        except ResolutionError:
            return None
        return candidate

    @staticmethod
    def _url_from_json(value: object) -> str | None:
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key.lower() in URL_KEYS
                    and isinstance(item, str)
                    and URL_RE.match(item)
                ):
                    return item
                nested = LinkResolver._url_from_json(item)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = LinkResolver._url_from_json(item)
                if nested:
                    return nested
        return None

    async def _captcha_visible(self, page: Page) -> bool:
        selectors = [
            "iframe[src*='recaptcha']",
            "iframe[src*='hcaptcha']",
            "iframe[src*='turnstile']",
            "iframe[src*='challenges.cloudflare']",
            ".g-recaptcha",
            ".h-captcha",
            ".cf-turnstile",
            "text=/verify you are human/i",
        ]
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() and await locator.is_visible():
                    return True
            except PlaywrightError:
                continue
        return False

    def _is_allowed_host(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        return host in self.allowed_hosts

    @staticmethod
    async def _ensure_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ResolutionError("Invalid URL.")
        if parsed.username or parsed.password:
            raise ResolutionError("Username/password wala URL allowed nahi hai.")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ResolutionError("Invalid port.") from exc
        if port is not None and port not in {80, 443}:
            raise ResolutionError("Sirf standard web ports 80 aur 443 allowed hain.")

        host = parsed.hostname.rstrip(".")
        try:
            direct_ip = ipaddress.ip_address(host)
            addresses = [direct_ip]
        except ValueError:
            try:
                infos = await asyncio.to_thread(
                    socket.getaddrinfo,
                    host,
                    port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            except socket.gaierror as exc:
                raise ResolutionError("Domain resolve nahi hua.") from exc
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]

        if not addresses or any(not address.is_global for address in addresses):
            raise ResolutionError("Private ya local network URL allowed nahi hai.")
