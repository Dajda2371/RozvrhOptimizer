"""Download real pages into tests/fixtures/ for parser tests.

Usage: uv run python scripts/capture_fixtures.py [--class C8] [--teacher UY045] [--room ZW] [--redact]
Requires BAKALARI_* settings (see .env.example). Cookies are never written to disk.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

from rozvrh_mcp.client import CHANGES_PATH, BakalariClient
from rozvrh_mcp.config import load_settings
from rozvrh_mcp.parser import parse_selector_page

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def redact(html: str) -> str:
    """Best-effort pseudonymisation of teacher names (TeacherFullname / option texts)."""
    counter = {"n": 0}

    def repl(_m: re.Match[str]) -> str:
        counter["n"] += 1
        return f'"TeacherFullname":"Učitel {counter["n"]}"'

    return re.sub(r'"TeacherFullname":"[^"]*"', repl, html)


async def run(args: argparse.Namespace) -> None:
    settings = load_settings()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    async with BakalariClient(settings) as client:
        selector = await client.fetch_selector_page()
        (FIXTURES / "selector.html").write_text(selector, encoding="utf-8")
        lists = parse_selector_page(selector)
        class_id = args.class_id or lists["class"][0].id
        teacher_id = args.teacher_id or lists["teacher"][0].id
        room_id = args.room_id or lists["room"][0].id
        pages = {
            "class_permanent.html": ("permanent", "class", class_id),
            "class_actual.html": ("actual", "class", class_id),
            "class_next.html": ("next", "class", class_id),
            "teacher_permanent.html": ("permanent", "teacher", teacher_id),
            "room_permanent.html": ("permanent", "room", room_id),
        }
        for filename, (variant, kind, entity_id) in pages.items():
            html = await client.fetch_timetable(variant, kind, entity_id)  # type: ignore[arg-type]
            if args.redact:
                html = redact(html)
            (FIXTURES / filename).write_text(html, encoding="utf-8")
            print(f"wrote {filename} ({len(html)} bytes) for {kind} {entity_id}", file=sys.stderr)
        changes = await client.fetch_changes()
        (FIXTURES / "zmeny.html").write_text(changes, encoding="utf-8")
        print(f"wrote zmeny.html from {CHANGES_PATH}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--class", dest="class_id")
    parser.add_argument("--teacher", dest="teacher_id")
    parser.add_argument("--room", dest="room_id")
    parser.add_argument("--redact", action="store_true")
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
