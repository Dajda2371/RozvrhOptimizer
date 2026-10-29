"""Print one timetable as JSON. Usage: uv run python scripts/smoke.py 1.A [--kind class] [--variant permanent] [--full]"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from rozvrh_mcp.client import BakalariClient
from rozvrh_mcp.config import load_settings
from rozvrh_mcp.service import TimetableService


async def run(query: str, kind: str, variant: str, compact: bool) -> None:
    settings = load_settings()
    async with BakalariClient(settings) as client:
        service = TimetableService(client, settings)
        tt = await service.get_timetable(kind, query, variant)  # type: ignore[arg-type]
        json.dump(tt.to_dict(compact), sys.stdout, ensure_ascii=False, indent=1)
        print()
        print(f"# {len(tt.lessons)} lessons, source={tt.source}, requests={client.request_count}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--kind", choices=["class", "teacher", "room"], default="class")
    parser.add_argument("--variant", choices=["actual", "next", "permanent"], default="permanent")
    parser.add_argument("--full", action="store_true", help="include raw fields")
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(run(args.query, args.kind, args.variant, not args.full))


if __name__ == "__main__":
    main()
