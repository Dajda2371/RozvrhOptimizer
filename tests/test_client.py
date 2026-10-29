from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx
from conftest import LOGIN_PAGE, atom, day, hour, load_fixture, page, timetable_data

from rozvrh_mcp.client import BakalariClient, timetable_path
from rozvrh_mcp.config import Settings
from rozvrh_mcp.errors import LoginError, NotFoundError, UpstreamError

BASE = "https://school.example"
TT_PAGE = page(timetable_data([day("po", [hour("8:00", [atom()])])]))


def settings(**kw):
    return Settings(base_url=BASE, **kw)


def test_timetable_path_quotes_ids():
    assert timetable_path("permanent", "class", "C8") == "/Timetable/Public/Permanent/Class/C8"
    assert timetable_path("next", "room", "a/b") == "/Timetable/Public/Next/Room/a%2Fb"


@respx.mock
async def test_anonymous_fetch_when_public():
    respx.get(f"{BASE}/Timetable/Public/").mock(return_value=httpx.Response(200, text=TT_PAGE))
    async with BakalariClient(settings()) as client:
        html = await client.fetch_selector_page()
    assert "timetableData" in html


@respx.mock
async def test_login_required_without_credentials():
    respx.get(f"{BASE}/Timetable/Public/").mock(
        return_value=httpx.Response(302, headers={"location": f"{BASE}/login?ReturnUrl=/Timetable/Public/"})
    )
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    async with BakalariClient(settings()) as client:
        with pytest.raises(LoginError, match="BAKALARI_USERNAME"):
            await client.fetch_selector_page()


@respx.mock
async def test_login_flow_copies_hidden_fields_and_sets_cookie():
    logged_in = {"value": False}
    posted = {}

    def timetable(request: httpx.Request) -> httpx.Response:
        if request.headers.get("cookie", "").find("BakaAuth=secret") >= 0:
            return httpx.Response(200, text=TT_PAGE)
        return httpx.Response(302, headers={"location": f"{BASE}/login?ReturnUrl={request.url.path}"})

    def login_post(request: httpx.Request) -> httpx.Response:
        posted.update(httpx.QueryParams(request.content.decode()))
        logged_in["value"] = True
        return httpx.Response(
            302, headers={"location": f"{BASE}/Timetable/Public/", "set-cookie": "BakaAuth=secret; Path=/"}
        )

    respx.get(f"{BASE}/Timetable/Public/").mock(side_effect=timetable)
    respx.get(f"{BASE}/Timetable/Public/Permanent/Class/C8").mock(side_effect=timetable)
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    login_route = respx.post(f"{BASE}/Login").mock(side_effect=login_post)

    async with BakalariClient(settings(username="user", password="pw")) as client:
        html = await client.fetch_selector_page()
        assert "timetableData" in html
        assert posted["username"] == "user" and posted["password"] == "pw"
        assert posted["__RequestVerificationToken"] == "tok123"
        assert posted["persistent"] == "true" and posted["returnUrl"] == "/Timetable/Public/"
        assert client.login_generation == 1
        # second page reuses the cookie, no second login
        await client.fetch_timetable("permanent", "class", "C8")
        assert client.login_generation == 1
        assert login_route.call_count == 1


@respx.mock
async def test_failed_login_raises():
    respx.get(f"{BASE}/Timetable/Public/").mock(
        return_value=httpx.Response(302, headers={"location": f"{BASE}/login?ReturnUrl=/Timetable/Public/"})
    )
    respx.get(f"{BASE}/login").mock(return_value=httpx.Response(200, text=LOGIN_PAGE))
    respx.post(f"{BASE}/Login").mock(
        return_value=httpx.Response(200, text=LOGIN_PAGE.replace("<form", '<div class="login-error">Špatné heslo</div><form'))
    )
    async with BakalariClient(settings(username="user", password="bad")) as client:
        with pytest.raises(LoginError, match="Špatné heslo"):
            await client.fetch_selector_page()


@respx.mock
async def test_server_error_retries_then_raises():
    route = respx.get(f"{BASE}/Timetable/Public/").mock(return_value=httpx.Response(503))
    async with BakalariClient(settings()) as client:
        with pytest.raises(UpstreamError):
            await client.fetch_selector_page()
    assert route.call_count == 2


@respx.mock
async def test_404_is_not_found():
    respx.get(f"{BASE}/Timetable/Public/Permanent/Class/XX").mock(return_value=httpx.Response(404))
    async with BakalariClient(settings()) as client:
        with pytest.raises(NotFoundError):
            await client.fetch_timetable("permanent", "class", "XX")


@respx.mock
async def test_changes_page_postback_for_other_date():
    html = load_fixture("zmeny.html")
    respx.get(f"{BASE}/next/zmeny.aspx").mock(return_value=httpx.Response(200, text=html))
    post = respx.post(f"{BASE}/next/zmeny.aspx").mock(return_value=httpx.Response(200, text=html.replace("29. 10.", "30. 10.")))
    async with BakalariClient(settings()) as client:
        same = await client.fetch_changes(date(2026, 10, 29))
        assert "29. 10." in same and post.call_count == 0
        other = await client.fetch_changes(date(2026, 10, 30))
        assert "30. 10." in other and post.call_count == 1
        body = httpx.QueryParams(post.calls[0].request.content.decode())
        assert body["DateEdit"] in ("10/30/2026", "30.10.2026") and body["__VIEWSTATE"]  # mirrors the page format
