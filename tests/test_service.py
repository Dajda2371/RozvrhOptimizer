from __future__ import annotations

import pytest
from conftest import atom, day, hour, page, timetable_data

from rozvrh_mcp.cache import TTLCache
from rozvrh_mcp.config import Settings
from rozvrh_mcp.errors import NotFoundError
from rozvrh_mcp.models import Day, Entity, Hour, Lesson, Timetable
from rozvrh_mcp.service import (
    TimetableService,
    compute_conflicts,
    compute_free_rooms,
    compute_free_slots,
    normalize_day,
)


class FakeClient:
    """Stands in for BakalariClient; serves pages from a dict keyed by (variant, kind, id)."""

    def __init__(self, selector: str, pages: dict[tuple[str, str, str], str]) -> None:
        self.selector = selector
        self.pages = pages
        self.login_generation = 0
        self.request_count = 0

    async def fetch_selector_page(self) -> str:
        self.request_count += 1
        return self.selector

    async def fetch_timetable(self, variant, kind, entity_id) -> str:
        self.request_count += 1
        try:
            return self.pages[(variant, kind, entity_id)]
        except KeyError as exc:
            raise NotFoundError(f"no page for {entity_id}") from exc


def lesson(day_i: int, period: int, **kw) -> Lesson:
    base = dict(day_abbrev="po", subject="M", room="1", classes=["1.A"], groups=[])
    base.update(kw)
    return Lesson(day=day_i, period=period, **base)


def tt(lessons: list[Lesson], kind="teacher", id="T1", name="Teacher") -> Timetable:
    days = [Day(day=i, day_abbrev=a) for i, a in enumerate(["po", "út", "st", "čt", "pá"])]
    for l in lessons:
        days[l.day].lessons.append(l)
    return Timetable(kind=kind, id=id, name=name, variant="permanent", fetched_at="now",
                     hours=[Hour(p, "", "") for p in range(0, 4)], days=days)


# ----------------------------------------------------------------------------- pure helpers


@pytest.mark.parametrize("value,expected", [(0, 0), ("4", 4), ("po", 0), ("Út", 1), ("ct", 3), ("Pátek", 4), ("mon", 0), ("Friday", 4)])
def test_normalize_day(value, expected):
    assert normalize_day(value) == expected


@pytest.mark.parametrize("value", [7, -1, "sunday?", "", True])
def test_normalize_day_rejects(value):
    with pytest.raises(ValueError):
        normalize_day(value)


def test_compute_free_rooms():
    rooms = [Entity("room", "A", "101"), Entity("room", "B", "102"), Entity("room", "C", "103")]
    tts = {
        "A": tt([lesson(0, 1, room="101")], kind="room", id="A", name="101"),
        "B": tt([lesson(0, 1, room="102", change="removed")], kind="room", id="B", name="102"),
    }
    out = compute_free_rooms(tts, rooms, day=0, period=1)
    assert [r["id"] for r in out["free"]] == ["B"]
    assert [r["id"] for r in out["busy"]] == ["A"] and out["busy"][0]["lessons"][0]["subject"] == "M"
    assert [r["id"] for r in out["unknown"]] == ["C"]


def test_compute_free_slots_marks_cycle_only_slots():
    t = tt([lesson(0, 1), lesson(0, 2, cycle="odd"), lesson(0, 3, change="canceled")])
    days = compute_free_slots(t)
    assert days[0]["busy_periods"] == [1, 2]
    assert days[0]["free_periods"] == [0, 3]
    assert days[0]["busy_only_in_cycle"] == {"2": "odd"}
    assert days[1]["free_periods"] == [0, 1, 2, 3]


def test_compute_conflicts():
    t = tt([
        lesson(0, 1, classes=["1.A"], room="1"), lesson(0, 1, classes=["1.B"], room="2"),  # real clash
        lesson(1, 1, classes=["2.A"], cycle="odd"), lesson(1, 1, classes=["2.B"], cycle="even"),  # fine
        lesson(2, 1, classes=["3.A"], groups=["S1"]), lesson(2, 1, classes=["3.A"], groups=["S1"]),  # duplicate
        lesson(3, 1, classes=["4.A"], subject="X", room="9"), lesson(3, 1, classes=["4.B"], subject="X", room="9"),  # joined
        lesson(4, 1, classes=["5.A"]), lesson(4, 1, classes=["5.B"], change="removed"),  # removed ignored
    ])
    found = compute_conflicts(t)
    assert [(c["day"], c["period"]) for c in found] == [(0, 1), (3, 1)]
    assert found[0]["likely_joined_lesson"] is False
    assert found[1]["likely_joined_lesson"] is True


# ----------------------------------------------------------------------------- service


@pytest.fixture
def service(sample_class_page):
    teacher_page = page(timetable_data([day("po", [hour("8:00", [atom(Teacher="", TeacherFullname="", GroupsNames="1.A celá")])])]))
    room_pages = {
        ("permanent", "room", rid): page(timetable_data([day("po", [hour("8:00", [atom(Room=name)])] if rid == "R3" else [])]))
        for rid, name in [("ZW", "1101"), ("51", "1200"), ("R3", "2507")]
    }
    pages = {
        ("permanent", "class", "C8"): sample_class_page,
        ("actual", "class", "C8"): sample_class_page,
        ("permanent", "teacher", "U0001"): teacher_page,
        ("permanent", "teacher", "UY045"): teacher_page,
        **room_pages,
    }
    client = FakeClient(sample_class_page, pages)
    settings = Settings(base_url="https://school.example", cache_ttl=60)
    return TimetableService(client, settings, TTLCache(60))  # type: ignore[arg-type]


async def test_resolve_by_id_name_and_case(service):
    assert (await service.resolve("class", "C8")).name == "1.A"
    assert (await service.resolve("class", "c8")).id == "C8"
    assert (await service.resolve("class", "1.a")).id == "C8"
    assert (await service.resolve("room", "2507")).id == "R3"


async def test_resolve_teacher_variants(service):
    assert (await service.resolve("teacher", "Jan Ambrož")).id == "UY045"
    assert (await service.resolve("teacher", "ambroz")).id == "UY045"  # accent-insensitive substring
    with pytest.raises(NotFoundError, match="Ambiguous"):
        await service.resolve("teacher", "Anderle")
    with pytest.raises(NotFoundError) as exc:
        await service.resolve("teacher", "Ambrosch Jan")
    assert "Ambrož Jan" in str(exc.value)


async def test_teacher_abbreviation_learned_from_class_timetable(service):
    with pytest.raises(NotFoundError, match="abbreviations"):
        await service.resolve("teacher", "PePa")
    await service.get_timetable("class", "1.A")
    assert (await service.resolve("teacher", "PePa")).id == "U0001"  # "Mgr. Bc. Pavel Petr" -> "Petr Pavel"


async def test_get_timetable_is_cached(service):
    await service.get_timetable("class", "C8")
    await service.get_timetable("class", "1.A")
    assert service.client.request_count == 2  # selector + one page
    assert service.refresh("timetables") == 1
    await service.get_timetable("class", "C8")
    assert service.client.request_count == 3


async def test_bulk_reports_errors_per_entity(service):
    result = await service.get_bulk("class", None, "permanent")
    assert set(result.timetables) == {"C8"}
    assert set(result.errors) == {"C9", "CA"}
    d = result.to_dict()
    assert d["hours"] and "hours" not in d["timetables"]["C8"]
    named = await service.get_bulk("class", ["1.A", "nope"], "permanent")
    assert "nope" in named.errors and "C8" in named.timetables


async def test_free_rooms_and_slots_and_conflicts(service):
    out = await service.free_rooms("po", 1)
    assert [r["name"] for r in out["free"]] == ["1101", "1200"]
    assert [r["name"] for r in out["busy"]] == ["2507"]
    slots = await service.teacher_free_slots("Petr Pavel")
    assert slots["days"][0]["busy_periods"] == [1]
    conflicts = await service.conflicts("teacher", "permanent", ["Petr Pavel"])
    assert conflicts["conflicts"] == [] and conflicts["checked"] == 1


async def test_info_and_refresh_scopes(service):
    await service.entities("class")
    info = service.info()
    assert info["auth"] == "anonymous" and info["cache"]["entries"] == 1
    assert service.refresh("lists") == 1
    with pytest.raises(Exception):
        service.refresh("bogus")


async def test_daily_changes_rejects_other_dates(service):
    with pytest.raises(Exception, match="only show today"):
        await service.daily_changes("2000-01-01")
    with pytest.raises(Exception, match="YYYY-MM-DD"):
        await service.daily_changes("yesterday")
