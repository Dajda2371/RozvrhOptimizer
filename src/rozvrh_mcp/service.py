"""Business layer: name resolution, caching, bulk fetching and derived helpers.

Pure computations (`compute_*`, `normalize_day`) take model objects and are unit-testable
without any network; `TimetableService` wires them to the client and cache.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime
from typing import Any

from .cache import TTLCache
from .client import BakalariClient
from .config import Settings
from .errors import NotFoundError, RozvrhError
from .models import DAY_ABBREVS, VARIANTS, DailyChanges, Entity, Kind, Lesson, Timetable, Variant
from .parser import parse_changes_page, parse_selector_page, parse_timetable

log = logging.getLogger("rozvrh_mcp.service")

_DAY_ALIASES: dict[str, int] = {
    "po": 0, "pondeli": 0, "pondělí": 0, "mon": 0, "monday": 0,
    "ut": 1, "út": 1, "utery": 1, "úterý": 1, "tue": 1, "tuesday": 1,
    "st": 2, "streda": 2, "středa": 2, "wed": 2, "wednesday": 2,
    "ct": 3, "čt": 3, "ctvrtek": 3, "čtvrtek": 3, "thu": 3, "thursday": 3,
    "pa": 4, "pá": 4, "patek": 4, "pátek": 4, "fri": 4, "friday": 4,
}


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", strip_accents(text).casefold()).strip()


def name_tokens(text: str) -> frozenset[str]:
    """Name words without academic titles (tokens ending with a dot), accent-insensitive."""
    return frozenset(t for t in normalize_text(text).replace(",", " ").split() if not t.endswith("."))


def normalize_day(day: int | str) -> int:
    """Accept 0-4, Czech abbreviations (po/út/st/čt/pá) or English names; return 0 = Monday."""
    if isinstance(day, bool):
        raise ValueError("day must be a number or a weekday name")
    if isinstance(day, int):
        if 0 <= day <= 6:
            return day
        raise ValueError(f"day must be between 0 (Monday) and 4 (Friday), got {day}")
    text = day.strip()
    if text.lstrip("-").isdigit():
        return normalize_day(int(text))
    key = normalize_text(text)
    if key in _DAY_ALIASES:
        return _DAY_ALIASES[key]
    raise ValueError(f"Unknown day {day!r}; use 0-4, po/út/st/čt/pá or mon..fri")


# --------------------------------------------------------------------------- pure computations


def _cycles_compatible(a: Lesson, b: Lesson) -> bool:
    """True when two lessons can never happen in the same week (odd vs even)."""
    return a.cycle is not None and b.cycle is not None and a.cycle != b.cycle


def compute_free_rooms(
    timetables: dict[str, Timetable], entities: list[Entity], day: int, period: int
) -> dict[str, Any]:
    free: list[dict[str, Any]] = []
    busy: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for entity in entities:
        tt = timetables.get(entity.id)
        if tt is None:
            unknown.append(entity.to_dict())
            continue
        lessons = [l for l in tt.lessons if l.day == day and l.period == period and l.active]
        if lessons:
            busy.append({**entity.to_dict(), "lessons": [l.to_dict() for l in lessons]})
        else:
            free.append(entity.to_dict())
    return {"day": day, "period": period, "free": free, "busy": busy, "unknown": unknown}


def compute_free_slots(tt: Timetable) -> list[dict[str, Any]]:
    periods = [h.period for h in tt.hours]
    result = []
    for day in tt.days:
        busy: dict[int, list[Lesson]] = {}
        for lesson in day.lessons:
            if lesson.active:
                busy.setdefault(lesson.period, []).append(lesson)
        busy_periods = sorted(busy)
        entry: dict[str, Any] = {
            "day": day.day,
            "day_abbrev": day.day_abbrev,
            "free_periods": [p for p in periods if p not in busy] if periods else [],
            "busy_periods": busy_periods,
        }
        partial = {
            p: sorted({l.cycle for l in ls if l.cycle})
            for p, ls in busy.items()
            if ls and all(l.cycle for l in ls) and len({l.cycle for l in ls}) == 1
        }
        if partial:
            entry["busy_only_in_cycle"] = {str(p): cyc[0] for p, cyc in partial.items()}
        if day.day_off:
            entry["day_off"] = True
            entry["day_off_name"] = day.day_off_name
        if day.date:
            entry["date"] = day.date
        result.append(entry)
    return result


def compute_conflicts(tt: Timetable) -> list[dict[str, Any]]:
    """Slots where one teacher/room has two or more different classes at the same time."""
    conflicts: list[dict[str, Any]] = []
    slots: dict[tuple[int, int], list[Lesson]] = {}
    for lesson in tt.lessons:
        if lesson.active:
            slots.setdefault((lesson.day, lesson.period), []).append(lesson)
    for (day, period), lessons in sorted(slots.items()):
        if len(lessons) < 2:
            continue
        clashing: list[Lesson] = []
        for i, a in enumerate(lessons):
            for b in lessons[i + 1 :]:
                if _cycles_compatible(a, b):
                    continue
                if set(a.classes) == set(b.classes) and set(a.groups) == set(b.groups):
                    continue  # same lesson listed twice (e.g. two groups of one class)
                for x in (a, b):
                    if x not in clashing:
                        clashing.append(x)
        if not clashing:
            continue
        same_subject_room = len({(l.subject, l.room) for l in clashing}) == 1
        conflicts.append(
            {
                "day": day,
                "period": period,
                "severity": "possible",
                "likely_joined_lesson": same_subject_room,
                "lessons": [l.to_dict() for l in clashing],
            }
        )
    return conflicts


# --------------------------------------------------------------------------- service


@dataclass
class BulkResult:
    kind: Kind
    variant: Variant
    timetables: dict[str, Timetable] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def to_dict(self, compact: bool = True) -> dict[str, Any]:
        hours = next((tt.hours for tt in self.timetables.values()), [])
        return {
            "kind": self.kind,
            "variant": self.variant,
            "hours": [h.to_dict() for h in hours],
            "timetables": {
                tid: {k: v for k, v in tt.to_dict(compact).items() if k != "hours"}
                for tid, tt in self.timetables.items()
            },
            "errors": self.errors,
        }


class TimetableService:
    def __init__(self, client: BakalariClient, settings: Settings, cache: TTLCache[Any] | None = None) -> None:
        self.client = client
        self.settings = settings
        self.cache: TTLCache[Any] = cache or TTLCache(settings.cache_ttl)
        self._teacher_abbrevs: dict[str, str] = {}  # abbrev -> full name (learned from lessons)

    # ------------------------------------------------------------------ entities

    async def all_entities(self) -> dict[Kind, list[Entity]]:
        async def fetch() -> dict[Kind, list[Entity]]:
            html = await self.client.fetch_selector_page()
            return parse_selector_page(html)

        return await self.cache.get_or_fetch(("lists",), fetch)

    async def entities(self, kind: Kind) -> list[Entity]:
        return (await self.all_entities()).get(kind, [])

    def _learn_abbrevs(self, tt: Timetable) -> None:
        for lesson in tt.lessons:
            if lesson.teacher_abbrev and lesson.teacher:
                self._teacher_abbrevs.setdefault(lesson.teacher_abbrev, lesson.teacher)

    async def resolve(self, kind: Kind, query: str) -> Entity:
        query = (query or "").strip()
        if not query:
            raise NotFoundError(f"Empty {kind} identifier.")
        entities = await self.entities(kind)
        if not entities:
            raise NotFoundError(f"The school publishes no {kind} timetables.")
        by_id = {e.id: e for e in entities}
        if query in by_id:
            return by_id[query]
        lowered = {e.id.casefold(): e for e in entities}
        if query.casefold() in lowered:
            return lowered[query.casefold()]

        norm = normalize_text(query)
        exact = [e for e in entities if normalize_text(e.name) == norm]
        if len(exact) == 1:
            return exact[0]
        if kind == "teacher":
            tokens = name_tokens(query)
            if tokens:
                same_tokens = [e for e in entities if name_tokens(e.name) == tokens]
                if len(same_tokens) == 1:
                    return same_tokens[0]
                full = self._teacher_abbrevs.get(query)
                if full:
                    full_tokens = name_tokens(full)
                    by_full = [e for e in entities if name_tokens(e.name) == full_tokens]
                    if len(by_full) == 1:
                        return by_full[0]
                    by_full = [e for e in entities if name_tokens(e.name) <= full_tokens]
                    if len(by_full) == 1:
                        return by_full[0]
        partial = [e for e in entities if norm in normalize_text(e.name)]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(f"{e.name} ({e.id})" for e in partial[:8])
            raise NotFoundError(f"Ambiguous {kind} {query!r}: matches {names}.")
        suggestions = difflib.get_close_matches(norm, [normalize_text(e.name) for e in entities], n=5, cutoff=0.5)
        pretty = [e.name for e in entities if normalize_text(e.name) in suggestions]
        hint = ""
        if kind == "teacher" and len(query) <= 5 and not self._teacher_abbrevs:
            hint = " (teacher abbreviations become resolvable after any class timetable has been fetched)"
        raise NotFoundError(f"Unknown {kind} {query!r}{hint}.", suggestions=pretty)

    # ------------------------------------------------------------------ timetables

    async def get_timetable(self, kind: Kind, query: str, variant: Variant = "permanent") -> Timetable:
        if variant not in VARIANTS:
            raise RozvrhError(f"Unknown variant {variant!r}; use one of {', '.join(VARIANTS)}.")
        entity = await self.resolve(kind, query)
        return await self._fetch_timetable(entity, variant)

    async def _fetch_timetable(self, entity: Entity, variant: Variant) -> Timetable:
        async def fetch() -> Timetable:
            html = await self.client.fetch_timetable(variant, entity.kind, entity.id)
            tt = parse_timetable(html, kind=entity.kind, id=entity.id, name=entity.name, variant=variant)
            self._learn_abbrevs(tt)
            return tt

        return await self.cache.get_or_fetch((variant, entity.kind, entity.id), fetch)

    async def get_bulk(self, kind: Kind, ids: list[str] | None, variant: Variant = "permanent") -> BulkResult:
        result = BulkResult(kind=kind, variant=variant)
        if ids:
            targets: list[Entity | str] = []
            for query in ids:
                try:
                    targets.append(await self.resolve(kind, query))
                except NotFoundError as exc:
                    result.errors[query] = str(exc)
        else:
            targets = list(await self.entities(kind))

        async def one(entity: Entity) -> tuple[str, Timetable | Exception]:
            try:
                return entity.id, await self._fetch_timetable(entity, variant)
            except Exception as exc:  # noqa: BLE001 - reported per entity
                return entity.id, exc

        outcomes = await asyncio.gather(*(one(e) for e in targets if isinstance(e, Entity)))
        for entity_id, outcome in outcomes:
            if isinstance(outcome, Exception):
                result.errors[entity_id] = str(outcome)
            else:
                result.timetables[entity_id] = outcome
        return result

    # ------------------------------------------------------------------ derived helpers

    async def free_rooms(
        self, day: int | str, period: int, variant: Variant = "permanent", rooms: list[str] | None = None
    ) -> dict[str, Any]:
        day_index = normalize_day(day)
        bulk = await self.get_bulk("room", rooms, variant)
        if rooms:
            entities = [await self.resolve("room", r) for r in rooms if r not in bulk.errors]
        else:
            entities = await self.entities("room")
        out = compute_free_rooms(bulk.timetables, entities, day_index, period)
        out["variant"] = variant
        if bulk.errors:
            out["errors"] = bulk.errors
        return out

    async def teacher_free_slots(self, teacher: str, variant: Variant = "permanent") -> dict[str, Any]:
        tt = await self.get_timetable("teacher", teacher, variant)
        return {
            "teacher": {"id": tt.id, "name": tt.name},
            "variant": variant,
            "hours": [h.to_dict() for h in tt.hours],
            "days": compute_free_slots(tt),
        }

    async def conflicts(self, kind: Kind, variant: Variant = "permanent", ids: list[str] | None = None) -> dict[str, Any]:
        bulk = await self.get_bulk(kind, ids, variant)
        found = []
        for entity_id, tt in bulk.timetables.items():
            for conflict in compute_conflicts(tt):
                found.append({"id": entity_id, "name": tt.name, **conflict})
        return {"kind": kind, "variant": variant, "conflicts": found, "checked": len(bulk.timetables), "errors": bulk.errors}

    async def daily_changes(self, day: str | None = None) -> DailyChanges:
        target: date_type | None = None
        if day:
            try:
                target = datetime.strptime(day.strip(), "%Y-%m-%d").date()
            except ValueError as exc:
                raise RozvrhError(f"date must be YYYY-MM-DD, got {day!r}") from exc

        async def fetch() -> DailyChanges:
            html = await self.client.fetch_changes(target)
            return parse_changes_page(html)

        return await self.cache.get_or_fetch(("changes", target.isoformat() if target else "today"), fetch)

    # ------------------------------------------------------------------ housekeeping

    def refresh(self, scope: str = "all") -> int:
        if scope == "all":
            return self.cache.invalidate()
        if scope == "lists":
            return self.cache.invalidate(lambda k: k == ("lists",))
        if scope == "timetables":
            return self.cache.invalidate(lambda k: isinstance(k, tuple) and len(k) == 3)
        if scope == "changes":
            return self.cache.invalidate(lambda k: isinstance(k, tuple) and k and k[0] == "changes")
        raise RozvrhError(f"Unknown scope {scope!r}; use all, lists, timetables or changes.")

    def info(self) -> dict[str, Any]:
        return {
            "base_url": self.settings.base_url,
            "auth": "login" if self.settings.has_credentials else "anonymous",
            "login_generation": self.client.login_generation,
            "requests_made": self.client.request_count,
            "cache": self.cache.stats(),
            "variants": list(VARIANTS),
            "days": {i: abbrev for i, abbrev in enumerate(DAY_ABBREVS[:5])},
            "learned_teacher_abbrevs": len(self._teacher_abbrevs),
        }
