"""MCP server exposing read-only Bakaláři timetable tools over stdio."""

from __future__ import annotations

import functools
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal, ParamSpec, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
from .client import BakalariClient
from .config import Settings, load_settings
from .errors import RozvrhError
from .models import Kind, Variant
from .service import TimetableService

log = logging.getLogger("rozvrh_mcp")

INSTRUCTIONS = """Read-only access to a school's Bakaláři public timetables (rozvrh).

Timetable kinds: class, teacher, room. Variants: "permanent" (stálý rozvrh, the base schedule,
best for optimisation), "actual" (this week with substitutions) and "next" (next week).
Days are 0 = Monday .. 4 = Friday; periods are the school's own numbering (often starting at 0)
with start/end times listed under "hours". Lessons may carry "cycle": "odd"/"even" when they
only happen in odd (lichý) or even (sudý) weeks. "change" marks substitutions, moved, canceled
or removed lessons; removed/canceled lessons do not occupy the slot.
Identifiers: every tool accepts either the Bakaláři id (e.g. "C8") or the human name
("1.A", "Novák Jan", "2507"). Use list_* tools to discover them and get_timetables_bulk to load
many timetables at once. Nothing here can modify the school system."""

P = ParamSpec("P")
R = TypeVar("R")

_service: TimetableService | None = None
_client: BakalariClient | None = None


async def _ensure_service() -> TimetableService:
    global _service, _client
    if _service is None:
        settings = load_settings()
        _client = BakalariClient(settings)
        _service = TimetableService(_client, settings)
        log.info("initialised %r", settings)
    return _service


def configure(settings: Settings, client: BakalariClient | None = None) -> TimetableService:
    """Install a service built from explicit settings (used by tests and scripts)."""
    global _service, _client
    _client = client or BakalariClient(settings)
    _service = TimetableService(_client, settings)
    return _service


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[dict[str, Any]]:
    try:
        yield {}
    finally:
        global _service, _client
        if _client is not None:
            await _client.aclose()
        _client = None
        _service = None


def _tool_errors(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except RozvrhError as exc:
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


mcp = MCPServer(
    "rozvrh-mcp",
    version=__version__,
    instructions=INSTRUCTIONS,
    lifespan=_lifespan,
)


# --------------------------------------------------------------------------- list tools


@mcp.tool()
@_tool_errors
async def list_classes() -> list[dict[str, str]]:
    """List all classes as [{"id", "name"}]. Either value works as class_id elsewhere. Cached."""
    svc = await _ensure_service()
    return [e.to_dict() for e in await svc.entities("class")]


@mcp.tool()
@_tool_errors
async def list_teachers() -> list[dict[str, str]]:
    """List all teachers as [{"id", "name"}] (name is "Surname Firstname"). Cached."""
    svc = await _ensure_service()
    return [e.to_dict() for e in await svc.entities("teacher")]


@mcp.tool()
@_tool_errors
async def list_rooms() -> list[dict[str, str]]:
    """List all rooms as [{"id", "name"}]. Cached."""
    svc = await _ensure_service()
    return [e.to_dict() for e in await svc.entities("room")]


# --------------------------------------------------------------------------- timetables


@mcp.tool()
@_tool_errors
async def get_class_timetable(class_id: str, variant: Variant = "permanent", compact: bool = True) -> dict[str, Any]:
    """Timetable of one class.

    class_id: Bakaláři id ("C8") or class name ("1.A", case-insensitive).
    variant: "permanent" (base schedule, no dates), "actual" (this week incl. substitutions), "next".
    Returns hours (period -> time), days (with day_off info) and a flat "lessons" list with
    day (0=Mon..4=Fri), period, subject, teacher, room, groups, cycle (odd/even week) and change.
    compact=False adds per-day lesson lists and the raw Bakaláři fields of every lesson."""
    svc = await _ensure_service()
    return (await svc.get_timetable("class", class_id, variant)).to_dict(compact)


@mcp.tool()
@_tool_errors
async def get_teacher_timetable(teacher: str, variant: Variant = "permanent", compact: bool = True) -> dict[str, Any]:
    """Timetable of one teacher (id, "Surname Firstname", or the abbreviation shown in class
    timetables once any class timetable was fetched). Lessons carry "classes" and "groups"."""
    svc = await _ensure_service()
    return (await svc.get_timetable("teacher", teacher, variant)).to_dict(compact)


@mcp.tool()
@_tool_errors
async def get_room_timetable(room: str, variant: Variant = "permanent", compact: bool = True) -> dict[str, Any]:
    """Timetable of one room (id or room name such as "2507"). Lessons carry "classes" and "teacher"."""
    svc = await _ensure_service()
    return (await svc.get_timetable("room", room, variant)).to_dict(compact)


@mcp.tool()
@_tool_errors
async def get_timetables_bulk(
    kind: Kind, ids: list[str] | None = None, variant: Variant = "permanent", compact: bool = True
) -> dict[str, Any]:
    """Fetch many timetables at once. ids=None means every class/teacher/room of the school.

    Fetches are cached and concurrency-limited; prefer this over many single calls.
    Returns {"kind", "variant", "hours", "timetables": {id: timetable}, "errors": {id: message}}."""
    svc = await _ensure_service()
    return (await svc.get_bulk(kind, ids, variant)).to_dict(compact)


# --------------------------------------------------------------------------- derived helpers


@mcp.tool()
@_tool_errors
async def find_free_rooms(
    day: int | str, period: int, variant: Variant = "permanent", rooms: list[str] | None = None
) -> dict[str, Any]:
    """Rooms with no lesson in the given slot.

    day: 0-4, "po/út/st/čt/pá" or "mon".."fri"; period: as listed under "hours".
    rooms: optional subset (ids or names); default = all rooms (one request per room, cached).
    Returns {"free": [...], "busy": [{id, name, lessons}], "unknown": [rooms that failed]}."""
    svc = await _ensure_service()
    return await svc.free_rooms(day, period, variant, rooms)


@mcp.tool()
@_tool_errors
async def get_teacher_free_slots(teacher: str, variant: Variant = "permanent") -> dict[str, Any]:
    """Per-day free and busy periods of a teacher, including slots busy only in odd/even weeks."""
    svc = await _ensure_service()
    return await svc.teacher_free_slots(teacher, variant)


@mcp.tool()
@_tool_errors
async def find_conflicts(
    kind: Literal["teacher", "room"] = "teacher", variant: Variant = "permanent", ids: list[str] | None = None
) -> dict[str, Any]:
    """Heuristic double-booking check: slots where one teacher/room has two or more different
    classes at once (odd/even-week pairs and removed lessons are ignored). Items are "possible"
    conflicts; "likely_joined_lesson" flags same subject+room, which is usually intentional."""
    svc = await _ensure_service()
    return await svc.conflicts(kind, variant, ids)


@mcp.tool()
@_tool_errors
async def get_daily_changes(date: str | None = None) -> dict[str, Any]:
    """Today's public daily changes page (Změny v rozvrhu): absent classes, absent teachers with their
    absent periods, rooms out of service, timetable changes and supervision duties.
    date: optional "YYYY-MM-DD"; only today is available, other dates return an error (use the
    "actual"/"next" timetable variants for substitutions on other days)."""
    svc = await _ensure_service()
    return (await svc.daily_changes(date)).to_dict()


# --------------------------------------------------------------------------- housekeeping


@mcp.tool()
@_tool_errors
async def get_server_info() -> dict[str, Any]:
    """Base URL, auth mode, cache statistics, supported variants and day numbering."""
    svc = await _ensure_service()
    return {"version": __version__, **svc.info()}


@mcp.tool()
@_tool_errors
async def refresh_cache(scope: Literal["all", "lists", "timetables", "changes"] = "all") -> dict[str, Any]:
    """Forget cached pages so the next call re-reads them from Bakaláři. Changes nothing on the server."""
    svc = await _ensure_service()
    return {"dropped": svc.refresh(scope)}


# --------------------------------------------------------------------------- resources


@mcp.resource("bakalari://classes", mime_type="application/json")
async def classes_resource() -> list[dict[str, str]]:
    svc = await _ensure_service()
    return [e.to_dict() for e in await svc.entities("class")]


@mcp.resource("bakalari://teachers", mime_type="application/json")
async def teachers_resource() -> list[dict[str, str]]:
    svc = await _ensure_service()
    return [e.to_dict() for e in await svc.entities("teacher")]


@mcp.resource("bakalari://rooms", mime_type="application/json")
async def rooms_resource() -> list[dict[str, str]]:
    svc = await _ensure_service()
    return [e.to_dict() for e in await svc.entities("room")]


@mcp.resource("bakalari://timetable/{kind}/{entity_id}", mime_type="application/json")
async def timetable_resource(kind: str, entity_id: str) -> dict[str, Any]:
    svc = await _ensure_service()
    if kind not in ("class", "teacher", "room"):
        raise ToolError(f"Unknown kind {kind!r}")
    return (await svc.get_timetable(kind, entity_id, "permanent")).to_dict(True)  # type: ignore[arg-type]


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    try:
        load_settings()  # fail fast with a readable message
    except RozvrhError as exc:
        print(f"rozvrh-mcp: {exc}", file=sys.stderr)
        sys.exit(2)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
