"""Plain data model shared by the parser, service and MCP tools.

Everything is a dataclass with a `to_dict()` that produces JSON-serialisable output.
`compact=True` (the default for tools) drops verbose / empty fields so that bulk
responses for a whole school stay small.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["class", "teacher", "room"]
Variant = Literal["actual", "next", "permanent"]
Change = Literal["none", "changed", "substitution", "moved", "canceled", "removed"]
Cycle = Literal["odd", "even"]

KINDS: tuple[Kind, ...] = ("class", "teacher", "room")
VARIANTS: tuple[Variant, ...] = ("actual", "next", "permanent")

KIND_PATH: dict[str, str] = {"class": "Class", "teacher": "Teacher", "room": "Room"}
VARIANT_PATH: dict[str, str] = {"actual": "Actual", "next": "Next", "permanent": "Permanent"}

# Czech weekday abbreviations as Bakaláři prints them, Monday first.
DAY_ABBREVS: tuple[str, ...] = ("po", "út", "st", "čt", "pá", "so", "ne")


def _drop_empty(d: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


@dataclass(slots=True)
class Entity:
    kind: Kind
    id: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}


@dataclass(slots=True)
class Hour:
    period: int
    start: str
    end: str

    def to_dict(self) -> dict[str, Any]:
        return {"period": self.period, "start": self.start, "end": self.end}


@dataclass(slots=True)
class Lesson:
    day: int  # 0 = Monday
    day_abbrev: str
    period: int
    subject: str | None = None
    subject_abbrev: str | None = None
    teacher: str | None = None  # full name incl. titles when known
    teacher_abbrev: str | None = None
    room: str | None = None
    room_full: str | None = None
    groups: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    cycle: Cycle | None = None  # odd / even week, permanent timetable only
    change: Change = "none"
    change_info: str | None = None
    theme: str | None = None
    notice: str | None = None
    ident_code: str | None = None
    absence: str | None = None  # absence code when the teacher is absent during this lesson (HasAbsent)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        """True when the lesson actually takes place (not removed / canceled)."""
        return self.change not in ("removed", "canceled")

    def to_dict(self, compact: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day": self.day,
            "period": self.period,
            "subject": self.subject,
            "subject_abbrev": self.subject_abbrev,
            "teacher": self.teacher,
            "teacher_abbrev": self.teacher_abbrev,
            "room": self.room,
            "groups": self.groups,
            "classes": self.classes,
            "cycle": self.cycle,
            "change": None if self.change == "none" else self.change,
            "change_info": self.change_info,
            "absence": self.absence,
        }
        if compact:
            return _drop_empty(d)
        d.update(
            {
                "day_abbrev": self.day_abbrev,
                "room_full": self.room_full,
                "change": self.change,
                "theme": self.theme,
                "notice": self.notice,
                "ident_code": self.ident_code,
                "raw": self.raw,
            }
        )
        return d


@dataclass(slots=True)
class Absence:
    """A teacher absence span (the "A" bar in the Bakaláři teacher view)."""

    day: int
    day_abbrev: str
    periods: list[int]  # periods covered by the span
    start: str
    end: str
    kind: str | None = None  # e.g. "EXU"
    kind_name: str | None = None  # e.g. "exkurse učitel"
    supervision: bool = False  # True = "dozor": present at school but not teaching
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, compact: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day": self.day,
            "periods": self.periods,
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "kind_name": self.kind_name,
            "supervision": self.supervision,
        }
        if compact:
            return _drop_empty(d) | {"supervision": self.supervision}
        return d | {"day_abbrev": self.day_abbrev, "raw": self.raw}


DutyKind = Literal["standby", "duty", "break_duty"]


@dataclass(slots=True)
class Duty:
    """A non-teaching assignment: substitution standby, supervision (dozor), canteen, break guard."""

    day: int
    day_abbrev: str
    name: str
    abbrev: str | None
    start: str
    end: str
    kind: DutyKind  # standby = available for substitution; duty = busy; break_duty = between periods
    period: int | None = None  # the period for standby/duty; None for break duties
    after_period: int | None = None  # break duties: the period the break follows
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, compact: bool = True) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day": self.day,
            "period": self.period,
            "after_period": self.after_period,
            "name": self.name,
            "abbrev": self.abbrev,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
        }
        if compact:
            return _drop_empty(d)
        return d | {"day_abbrev": self.day_abbrev, "raw": self.raw}


@dataclass(slots=True)
class Day:
    day: int
    day_abbrev: str
    date: str | None = None  # "26.10." as printed; None in the permanent timetable
    day_off: bool = False
    day_off_name: str | None = None
    lessons: list[Lesson] = field(default_factory=list)
    absences: list[Absence] = field(default_factory=list)
    duties: list[Duty] = field(default_factory=list)

    def to_dict(self, compact: bool = True, with_lessons: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day": self.day,
            "day_abbrev": self.day_abbrev,
            "date": self.date,
            "day_off": self.day_off,
            "day_off_name": self.day_off_name,
        }
        if with_lessons:
            d["lessons"] = [lesson.to_dict(compact) for lesson in self.lessons]
            d["absences"] = [a.to_dict(compact) for a in self.absences]
            d["duties"] = [x.to_dict(compact) for x in self.duties]
        return _drop_empty(d) if compact else d


@dataclass(slots=True)
class Timetable:
    kind: Kind
    id: str
    name: str
    variant: Variant
    fetched_at: str
    hours: list[Hour] = field(default_factory=list)
    days: list[Day] = field(default_factory=list)
    source: str = "json"  # "json" (embedded timetableData) or "html" (legacy grid)

    @property
    def lessons(self) -> list[Lesson]:
        return sorted((lesson for day in self.days for lesson in day.lessons), key=lambda x: (x.day, x.period))

    @property
    def absences(self) -> list[Absence]:
        return [a for day in self.days for a in day.absences]

    @property
    def duties(self) -> list[Duty]:
        return [x for day in self.days for x in day.duties]

    def to_dict(self, compact: bool = True) -> dict[str, Any]:
        d = {
            "kind": self.kind,
            "id": self.id,
            "name": self.name,
            "variant": self.variant,
            "fetched_at": self.fetched_at,
            "hours": [h.to_dict() for h in self.hours],
            "days": [d.to_dict(compact, with_lessons=not compact) for d in self.days],
            "lessons": [lesson.to_dict(compact) for lesson in self.lessons],
            "absences": [a.to_dict(compact) for a in self.absences],
            "duties": [x.to_dict(compact) for x in self.duties],
        }
        if compact:
            for key in ("absences", "duties"):
                if not d[key]:
                    del d[key]
        return d


@dataclass(slots=True)
class AbsentTeacher:
    name: str
    absent_periods: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "absent_periods": self.absent_periods}


@dataclass(slots=True)
class DailyChanges:
    date: str  # as printed in the page heading
    absent_classes: list[str] = field(default_factory=list)
    absent_teachers: list[AbsentTeacher] = field(default_factory=list)
    rooms_out_of_service: list[str] = field(default_factory=list)
    teacher_changes: list[list[str]] = field(default_factory=list)  # table rows as cell texts
    class_changes: list[list[str]] = field(default_factory=list)
    supervisions: list[list[str]] = field(default_factory=list)
    other_sections: dict[str, list[list[str]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "absent_classes": self.absent_classes,
            "absent_teachers": [t.to_dict() for t in self.absent_teachers],
            "rooms_out_of_service": self.rooms_out_of_service,
            "teacher_changes": self.teacher_changes,
            "class_changes": self.class_changes,
            "supervisions": self.supervisions,
            "other_sections": self.other_sections,
        }
