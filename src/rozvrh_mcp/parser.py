"""Pure parsing functions: HTML text in, model objects out. No network access here.

Supported inputs
----------------
* The selector page ``/Timetable/Public/`` (three ``<select>`` lists).
* A timetable page ``/Timetable/Public/{Variant}/{Kind}/{id}``:
  - current Bakaláři (2.x) embeds ``const timetableData = {...};`` in a script and renders
    it client-side with Knockout -> we read the JSON directly (``source="json"``);
  - older versions render the grid server-side (``.bk-timetable-row`` / ``.day-item-hover``
    with a ``data-detail`` JSON attribute) -> fallback grid parser (``source="html"``).
* The public daily changes page ``/next/zmeny.aspx``.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup, Tag

from .errors import LoginError, ParseError, TimetableUnavailableError
from .models import (
    DAY_ABBREVS,
    AbsentTeacher,
    DailyChanges,
    Day,
    Entity,
    Hour,
    Kind,
    Lesson,
    Timetable,
    Variant,
)

SELECT_IDS: dict[Kind, str] = {"class": "selectedClass", "teacher": "selectedTeacher", "room": "selectedRoom"}

# From the page's own GetChangeTypeClass(): these ChangeType values are drawn as canceled/removed.
CANCELED_CHANGE_TYPES = frozenset({11, 15, 17, 24})
REMOVED_CHANGE_TYPES = frozenset({7, 21, 22, 23})

_TIMETABLE_DATA_RE = re.compile(r"const\s+timetableData\s*=\s*")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})")


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def is_login_page(html: str) -> bool:
    return 'id="formlogin"' in html or "id='formlogin'" in html


def _text(el: Tag | None) -> str | None:
    if el is None:
        return None
    value = el.get_text(" ", strip=True)
    return value or None


def _clean(value: Any) -> str | None:
    """Return a stripped string or None for empty / non-string values."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = html_lib.unescape(value).strip()
    return value or None


def _minutes(value: str | None) -> int | None:
    """'8:00', '08:00:00' -> minutes since midnight."""
    if not value:
        return None
    m = _TIME_RE.match(value)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def _hhmm(value: str | None) -> str | None:
    """Normalise '08:00:00' / '8:00' -> '8:00'."""
    mins = _minutes(value)
    if mins is None:
        return _clean(value)
    return f"{mins // 60}:{mins % 60:02d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- selector page


def parse_selector_page(html: str) -> dict[Kind, list[Entity]]:
    """Return {"class": [...], "teacher": [...], "room": [...]} from the selector page."""
    if is_login_page(html):
        raise LoginError("The timetable page requires a login.")
    soup = _soup(html)
    result: dict[Kind, list[Entity]] = {}
    found_any = False
    for kind, select_id in SELECT_IDS.items():
        select = soup.find("select", id=select_id)
        entities: list[Entity] = []
        if select is not None:
            found_any = True
            for option in select.find_all("option"):
                value = _clean(option.get("value"))
                if not value:
                    continue
                name = _clean(option.get_text()) or value
                entities.append(Entity(kind=kind, id=value, name=name))
        result[kind] = entities
    if not found_any:
        raise ParseError("No class/teacher/room selectors found on the page (is the URL a Bakaláři public timetable?)")
    return result


# --------------------------------------------------------------------------- embedded JSON


def extract_timetable_data(html: str) -> dict[str, Any] | None:
    """Locate and decode ``const timetableData = {...};`` in the page, or return None."""
    m = _TIMETABLE_DATA_RE.search(html)
    if not m:
        return None
    try:
        data, _end = json.JSONDecoder().raw_decode(html, m.end())
    except ValueError as exc:
        raise ParseError(f"timetableData JSON could not be decoded: {exc}") from exc
    return data if isinstance(data, dict) else None


def _parse_tooltip(atom: dict[str, Any]) -> dict[str, Any]:
    raw = atom.get("TooltipDetails")
    if not raw or not isinstance(raw, str):
        return {}
    try:
        data = json.loads(html_lib.unescape(raw))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _split_groups(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [html_lib.unescape(p).strip() for p in _BR_RE.split(value)]
    return [p for p in parts if p]


def _classes_from_groups(groups: list[str]) -> list[str]:
    classes: list[str] = []
    for group in groups:
        first = group.split()[0] if group.split() else group
        if first and first not in classes:
            classes.append(first)
    return classes


def _cycle(value: Any) -> str | None:
    text = _clean(value)
    if not text:
        return None
    lowered = text.lower()
    if lowered.startswith("l"):  # lichý
        return "odd"
    if lowered.startswith("s"):  # sudý
        return "even"
    return None


def classify_change(atom_type: str | None, change_type: Any, change_info: str | None, has_changed: bool) -> str:
    """Map Bakaláři's change encoding onto the normalised `Lesson.change` values."""
    try:
        ct = int(change_type) if change_type is not None else None
    except (TypeError, ValueError):
        ct = None
    if atom_type == "removed" or ct in REMOVED_CHANGE_TYPES:
        return "removed"
    if atom_type == "absent" or ct in CANCELED_CHANGE_TYPES:
        return "canceled"
    info = (change_info or "").strip().lower()
    if info.startswith("zrušeno") or info.startswith("odpadá"):
        return "canceled"
    if info.startswith("suplov") or info.startswith("supl"):
        return "substitution"
    if info.startswith("přesun") or info.startswith("presun") or info.startswith("výměna"):
        return "moved"
    if info or has_changed:
        return "changed"
    return "none"


def _lesson_from_atom(
    atom: dict[str, Any],
    *,
    day: int,
    day_abbrev: str,
    period: int,
    kind: Kind,
    entity_name: str,
) -> Lesson:
    tooltip = _parse_tooltip(atom)
    groups = _split_groups(atom.get("GroupsNames") or tooltip.get("group"))
    if kind == "class":
        classes = [entity_name]
    else:
        classes = _classes_from_groups(groups)

    teacher = _clean(atom.get("TeacherFullname")) or _clean(tooltip.get("teacher"))
    teacher_abbrev = _clean(atom.get("Teacher"))
    if kind == "teacher" and not teacher:
        teacher = entity_name

    room = _clean(atom.get("Room"))
    room_full = _clean(atom.get("RoomFullName")) or _clean(tooltip.get("room"))
    if kind == "room" and not room:
        room = entity_name
    change_info = _clean(atom.get("ChangeInfo")) or _clean(tooltip.get("changeinfo"))
    change = classify_change(atom.get("Type"), atom.get("ChangeType"), change_info, bool(atom.get("HasChanged")))

    raw = {k: v for k, v in atom.items() if k != "TooltipDetails"}
    if tooltip:
        raw["TooltipDetails"] = tooltip
    return Lesson(
        day=day,
        day_abbrev=day_abbrev,
        period=period,
        subject=_clean(atom.get("SubjectText")) or _clean(tooltip.get("subjecttext")),
        subject_abbrev=_clean(atom.get("SubjectAbbrev")),
        teacher=teacher,
        teacher_abbrev=teacher_abbrev,
        room=room,
        room_full=room_full,
        groups=groups,
        classes=classes,
        cycle=_cycle(atom.get("CycleText")),
        change=change,
        change_info=change_info,
        theme=_clean(atom.get("Theme")),
        notice=_clean(atom.get("Notice")),
        ident_code=_clean(atom.get("IdentCode")),
        raw=raw,
    )


def _hours_from_json(data: dict[str, Any]) -> tuple[list[Hour], dict[int, int]]:
    """Return (hours header, minutes-of-day -> period map)."""
    hours: list[Hour] = []
    by_minutes: dict[int, int] = {}
    for idx, h in enumerate(data.get("Hours") or []):
        if not isinstance(h, dict):
            continue
        caption = _clean(h.get("Caption"))
        period = int(caption) if caption and caption.lstrip("-").isdigit() else idx
        start = _hhmm(h.get("BeginTime")) or ""
        end = _hhmm(h.get("EndTime")) or ""
        hours.append(Hour(period=period, start=start, end=end))
        mins = _minutes(h.get("BeginTime"))
        if mins is not None:
            by_minutes.setdefault(mins, period)
    return hours, by_minutes


def _period_for_hour(hour: dict[str, Any], by_minutes: dict[int, int], fallback: int) -> int:
    mins = hour.get("BeginTimeValue")
    if isinstance(mins, int) and mins in by_minutes:
        return by_minutes[mins]
    mins = _minutes(hour.get("Begin"))
    if mins is not None and mins in by_minutes:
        return by_minutes[mins]
    for atom in hour.get("Atoms") or []:
        tooltip = _parse_tooltip(atom) if isinstance(atom, dict) else {}
        m = re.match(r"\s*(\d+)", str(tooltip.get("time") or ""))
        if m:
            return int(m.group(1))
    return fallback


def timetable_from_json(
    data: dict[str, Any], *, kind: Kind, id: str, name: str, variant: Variant, fetched_at: str | None = None
) -> Timetable:
    hours, by_minutes = _hours_from_json(data)
    days: list[Day] = []
    for day_index, raw_day in enumerate(data.get("Days") or []):
        if not isinstance(raw_day, dict):
            continue
        abbrev = _clean(raw_day.get("DayAbbrev")) or (DAY_ABBREVS[day_index] if day_index < len(DAY_ABBREVS) else str(day_index))
        day = Day(
            day=day_index,
            day_abbrev=abbrev,
            date=_clean(raw_day.get("Date")),
            day_off=bool(raw_day.get("DayOff")),
            day_off_name=_clean(raw_day.get("DayOffName")),
        )
        for hour_pos, hour in enumerate(raw_day.get("Hours") or []):
            if not isinstance(hour, dict):
                continue
            period = _period_for_hour(hour, by_minutes, fallback=hour_pos)
            for atom in hour.get("Atoms") or []:
                if not isinstance(atom, dict):
                    continue
                day.lessons.append(
                    _lesson_from_atom(atom, day=day_index, day_abbrev=abbrev, period=period, kind=kind, entity_name=name)
                )
        days.append(day)
    return Timetable(
        kind=kind,
        id=id,
        name=name,
        variant=variant,
        fetched_at=fetched_at or _now_iso(),
        hours=hours,
        days=days,
        source="json",
    )


# --------------------------------------------------------------------------- legacy HTML grid


def _hours_from_grid(soup: BeautifulSoup) -> list[Hour]:
    hours: list[Hour] = []
    for idx, wrapper in enumerate(soup.select(".bk-timetable-hours .bk-hour-wrapper")):
        num = _text(wrapper.select_one(".num"))
        period = int(num) if num and num.lstrip("-").isdigit() else idx
        times = _text(wrapper.select_one(".hour")) or ""
        start, _, end = times.partition("-")
        hours.append(Hour(period=period, start=_hhmm(start) or start.strip(), end=_hhmm(end) or end.strip()))
    return hours


def _detail_json(hover: Tag) -> dict[str, Any]:
    raw = hover.get("data-detail")
    if not raw:
        return {}
    try:
        data = json.loads(html_lib.unescape(str(raw)))
    except ValueError:
        return {"_parse_warning": "data-detail is not valid JSON", "_raw": str(raw)[:500]}
    return data if isinstance(data, dict) else {}


def _lesson_from_grid_cell(
    hover: Tag, *, day: int, day_abbrev: str, period: int, kind: Kind, entity_name: str
) -> Lesson | None:
    detail = _detail_json(hover)
    css = " ".join(hover.get("class") or [])
    subject_text = _clean(detail.get("subjecttext"))
    subject = subject_text.split("|")[0].strip() if subject_text else None
    subject_abbrev = _text(hover.select_one(".middle"))
    top_left = _text(hover.select_one(".top .left"))
    top_right = _text(hover.select_one(".top .right"))
    bottom = _text(hover.select_one(".bottom"))
    dtype = _clean(detail.get("type"))
    if not subject and not detail and not subject_abbrev:
        return None  # empty placeholder cell
    change_info = _clean(detail.get("changeinfo")) or _clean(detail.get("removedinfo")) or _clean(detail.get("absentinfo"))
    change = classify_change(dtype, None, change_info, "pink" in css.split() or "green" in css.split())
    if change == "none" and "green" in css.split():
        change = "changed"
    group_text = _clean(detail.get("group")) or (top_left if kind == "class" else None)
    groups = _split_groups(group_text) if group_text else []
    if kind == "class":
        classes = [entity_name]
    else:
        source = _clean(detail.get("group")) or top_left or ""
        classes = _classes_from_groups(_split_groups(source))
    teacher = _clean(detail.get("teacher"))
    if kind == "teacher" and not teacher:
        teacher = entity_name
    room = _clean(detail.get("room")) or top_right
    if kind == "room" and not room:
        room = entity_name
    return Lesson(
        day=day,
        day_abbrev=day_abbrev,
        period=period,
        subject=subject,
        subject_abbrev=subject_abbrev,
        teacher=teacher,
        teacher_abbrev=bottom if kind == "class" else None,
        room=room,
        room_full=None,
        groups=groups,
        classes=classes,
        cycle=None,
        change=change,
        change_info=change_info,
        theme=_clean(detail.get("theme")),
        notice=_clean(detail.get("notice")),
        ident_code=None,
        raw={**detail, "css": css},
    )


def timetable_from_grid(
    soup: BeautifulSoup, *, kind: Kind, id: str, name: str, variant: Variant, fetched_at: str | None = None
) -> Timetable:
    hours = _hours_from_grid(soup)
    days: list[Day] = []
    for day_index, row in enumerate(soup.select(".bk-timetable-row")):
        abbrev = _text(row.select_one(".bk-day-day")) or (DAY_ABBREVS[day_index] if day_index < len(DAY_ABBREVS) else str(day_index))
        day = Day(day=day_index, day_abbrev=abbrev, date=_text(row.select_one(".bk-day-date")))
        for cell_index, cell in enumerate(row.select(".bk-timetable-cell")):
            period = hours[cell_index].period if cell_index < len(hours) else cell_index
            for hover in cell.select(".day-item-hover"):
                lesson = _lesson_from_grid_cell(
                    hover, day=day_index, day_abbrev=abbrev, period=period, kind=kind, entity_name=name
                )
                if lesson is not None:
                    day.lessons.append(lesson)
        days.append(day)
    return Timetable(
        kind=kind, id=id, name=name, variant=variant, fetched_at=fetched_at or _now_iso(), hours=hours, days=days, source="html"
    )


# --------------------------------------------------------------------------- entry point


def parse_timetable(html: str, *, kind: Kind, id: str, name: str, variant: Variant) -> Timetable:
    """Parse a timetable page using the embedded JSON when present, else the legacy grid."""
    if is_login_page(html):
        raise LoginError("The timetable page requires a login.")
    data = extract_timetable_data(html)
    if data is not None and data.get("Days"):
        return timetable_from_json(data, kind=kind, id=id, name=name, variant=variant)
    soup = _soup(html)
    if soup.select_one(".bk-timetable-row"):
        return timetable_from_grid(soup, kind=kind, id=id, name=name, variant=variant)
    title = _text(soup.title) or "untitled page"
    raise TimetableUnavailableError(
        f"No timetable found on the page ({title}). The public timetable may be disabled for this school."
    )


# --------------------------------------------------------------------------- daily changes (zmeny.aspx)

_SECTION_KEYS: dict[str, str] = {
    "nepřítomné třídy": "absent_classes",
    "nepřítomní učitelé": "absent_teachers",
    "místnosti mimo provoz": "rooms_out_of_service",
    "změny v rozvrzích učitelů": "teacher_changes",
    "změny v rozvrzích tříd": "class_changes",
    "pedagogické dohledy": "supervisions",
}


def _section_key(title: str) -> str | None:
    lowered = title.lower()
    for prefix, key in _SECTION_KEYS.items():
        if lowered.startswith(prefix):
            return key
    return None


def _table_rows(table: Tag) -> list[list[str]]:
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    return rows


def extract_form_state(html: str, form_id: str = "form1") -> dict[str, str]:
    """All named inputs of an ASP.NET WebForms page, for replaying a postback."""
    soup = _soup(html)
    form = soup.find("form", id=form_id) or soup.find("form")
    state: dict[str, str] = {}
    if form is None:
        return state
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        itype = (inp.get("type") or "text").lower()
        if itype in ("submit", "button", "image"):
            continue  # only sent when clicked
        if itype in ("checkbox", "radio") and not inp.has_attr("checked"):
            continue
        state[str(name)] = str(inp.get("value") or "")
    return state


def parse_changes_page(html: str) -> DailyChanges:
    soup = _soup(html)
    heading = soup.find("h1")
    if heading is None:
        raise ParseError("Daily changes page has no heading; unexpected markup.")
    heading_text = heading.get_text(" ", strip=True)
    date = re.sub(r"^\s*změny v rozvrhu\s*", "", heading_text, flags=re.IGNORECASE).strip() or heading_text
    result = DailyChanges(date=date)

    for el in heading.find_all_next(["div", "table"]):
        if el.name == "div":
            text = el.get_text(" ", strip=True)
            if not text or el.find("table"):
                continue
            title, sep, rest = text.partition(" - ")
            key = _section_key(title)
            if key is None:
                continue
            if sep and rest.strip().lower().startswith("nejsou"):
                continue  # explicit "none" marker, leave the list empty
            result.other_sections.setdefault(key, []).append([text])
        elif el.name == "table":
            rows = _table_rows(el)
            if not rows:
                continue
            title = rows[0][0] if rows[0] else ""
            key = _section_key(title)
            if key is None:
                result.other_sections[title or f"table{len(result.other_sections)}"] = rows[1:]
                continue
            if key == "absent_teachers":
                periods = [int(c) if c.isdigit() else None for c in rows[0][1:]]
                for row in rows[1:]:
                    if not row:
                        continue
                    absent = [
                        p for p, mark in zip(periods, row[1:]) if p is not None and mark and mark != "-"
                    ]
                    result.absent_teachers.append(AbsentTeacher(name=row[0], absent_periods=absent))
            elif key == "absent_classes":
                result.absent_classes.extend(r[0] for r in rows[1:] if r)
            elif key == "rooms_out_of_service":
                result.rooms_out_of_service.extend(r[0] for r in rows[1:] if r)
            else:
                getattr(result, key).extend(rows[1:])
    return result
