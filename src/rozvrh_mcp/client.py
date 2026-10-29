"""HTTP access to a Bakaláři instance: fetch public timetable pages, log in when required.

Only GET requests are made against the timetable pages. The login form POST and the
daily-changes date postback are the only POSTs, and neither modifies school data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as date_type
from typing import Any
from urllib.parse import quote, urljoin

import httpx
from bs4 import BeautifulSoup

from .config import Settings
from .errors import LoginError, NotFoundError, UpstreamError
from .models import KIND_PATH, VARIANT_PATH, Kind, Variant
from .parser import extract_form_state, is_login_page

log = logging.getLogger("rozvrh_mcp.client")

SELECTOR_PATH = "/Timetable/Public/"
LOGIN_PATH = "/login"
CHANGES_PATH = "/next/zmeny.aspx"
RETRY_DELAY = 1.0


def timetable_path(variant: Variant, kind: Kind, entity_id: str) -> str:
    return f"/Timetable/Public/{VARIANT_PATH[variant]}/{KIND_PATH[kind]}/{quote(entity_id, safe='')}"


class BakalariClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=settings.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "cs,en;q=0.5",
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._login_lock = asyncio.Lock()
        self.login_generation = 0
        self.request_count = 0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> BakalariClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ public API

    async def fetch_selector_page(self) -> str:
        return await self._get_html(SELECTOR_PATH)

    async def fetch_timetable(self, variant: Variant, kind: Kind, entity_id: str) -> str:
        return await self._get_html(timetable_path(variant, kind, entity_id))

    async def fetch_changes(self, day: date_type | None = None) -> str:
        """Daily changes page; `day` switches the date through the WebForms postback."""
        html = await self._get_html(CHANGES_PATH, require_timetable_auth=False)
        if day is None:
            return html
        state = extract_form_state(html)
        current = state.get("DateEdit", "")
        wanted = _format_date_like(day, current)
        if not state or wanted == current:
            return html
        state["DateEdit"] = wanted
        resp = await self._request("POST", CHANGES_PATH, data=state)
        return resp.text

    # ------------------------------------------------------------------ internals

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                async with self._semaphore:
                    self.request_count += 1
                    resp = await self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                last_exc = exc
                log.warning("transport error on %s %s (attempt %d): %s", method, path, attempt + 1, exc)
            else:
                if resp.status_code == 404:
                    raise NotFoundError(f"The server returned 404 for {path}.")
                if resp.status_code >= 500:
                    last_exc = UpstreamError(f"HTTP {resp.status_code} from {path}")
                    log.warning("server error %s on %s (attempt %d)", resp.status_code, path, attempt + 1)
                else:
                    return resp
            if attempt == 0:
                await asyncio.sleep(RETRY_DELAY)
        raise UpstreamError(f"Request to {path} failed: {last_exc}")

    @staticmethod
    def _needs_login(resp: httpx.Response) -> bool:
        if resp.url.path.lower().rstrip("/").endswith(LOGIN_PATH):
            return True
        return is_login_page(resp.text)

    async def _get_html(self, path: str, *, require_timetable_auth: bool = True) -> str:
        resp = await self._request("GET", path)
        if not require_timetable_auth or not self._needs_login(resp):
            return resp.text
        if not self.settings.has_credentials:
            raise LoginError(
                "This Bakaláři instance requires a login to show public timetables. "
                "Set BAKALARI_USERNAME and BAKALARI_PASSWORD."
            )
        generation = self.login_generation
        async with self._login_lock:
            if generation == self.login_generation:  # nobody logged in meanwhile
                await self.login(return_url=path)
        resp = await self._request("GET", path)
        if self._needs_login(resp):
            raise LoginError("Still redirected to the login page after a successful-looking login.")
        return resp.text

    async def login(self, return_url: str = SELECTOR_PATH) -> None:
        """Submit the web login form; the session cookie lives in the client's cookie jar."""
        settings = self.settings
        if not settings.has_credentials:
            raise LoginError("No credentials configured.")
        page = await self._request("GET", LOGIN_PATH, params={"ReturnUrl": return_url})
        soup = BeautifulSoup(page.text, "html.parser")
        form = soup.find("form", id="formlogin") or soup.find("form")
        if form is None:
            raise LoginError("Login page did not contain a login form.")
        fields: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            itype = (inp.get("type") or "text").lower()
            if itype in ("submit", "button", "image"):
                continue
            if itype == "checkbox":
                continue
            fields[str(name)] = str(inp.get("value") or "")
        fields["username"] = settings.username or ""
        fields["password"] = settings.password or ""
        fields["persistent"] = "true"
        fields.setdefault("returnUrl", return_url)
        if not fields.get("returnUrl"):
            fields["returnUrl"] = return_url
        button = form.find("button", attrs={"formaction": True})
        action = (button.get("formaction") if button else None) or form.get("action") or "/Login"
        action_url = urljoin(str(page.url), str(action))
        log.info("logging in as %s via %s", settings.username, action_url)
        resp = await self._request(
            "POST",
            action_url,
            data=fields,
            headers={"Referer": str(page.url), "Origin": settings.base_url},
        )
        if self._needs_login(resp):
            msg = _login_error_text(resp.text) or "the server returned the login form again (wrong username/password?)"
            raise LoginError(f"Login failed: {msg}")
        self.login_generation += 1
        log.info("login successful (generation %d)", self.login_generation)


def _login_error_text(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for selector in (".validation-summary-errors", ".login-error", "#generalWarning", "[data-valmsg-summary]"):
        el = soup.select_one(selector)
        if el is not None:
            text = el.get_text(" ", strip=True)
            if text:
                return text
    return None


def _format_date_like(day: date_type, sample: str) -> str:
    """Format `day` using the same convention as the page's current DateEdit value."""
    sample = sample.strip()
    if "/" in sample:  # en-US style M/d/yyyy as observed ("10/29/2026")
        parts = sample.split("/")
        if len(parts) == 3 and len(parts[0]) == 2 and len(parts[1]) == 2:
            return day.strftime("%m/%d/%Y")
        return f"{day.month}/{day.day}/{day.year}"
    if "." in sample:  # cs style d.M.yyyy
        return f"{day.day}.{day.month}.{day.year}"
    if "-" in sample:
        return day.isoformat()
    return day.strftime("%m/%d/%Y")
