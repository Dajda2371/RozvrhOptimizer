# rozvrh-mcp

Read-only [MCP](https://modelcontextprotocol.io) server that gives an AI agent access to a
school's **Bakaláři** public timetables: class, teacher and room schedules in the
*permanent* (stálý rozvrh), *actual* (this week) and *next* week variants, plus the public
daily changes page (Změny v rozvrhu). Built for timetable-optimisation agents; nothing in it
can modify the school system.

## How it works

Bakaláři's JSON API only returns the logged-in user's own timetable, so this server reads the
public timetable web pages (`/Timetable/Public/{Actual|Next|Permanent}/{Class|Teacher|Room}/{id}`).
Current Bakaláři versions embed the timetable as JSON in the page (`timetableData`), which is
parsed directly; older server-rendered grids are parsed from HTML as a fallback.

Some schools require a login even for the "public" timetables. When `BAKALARI_USERNAME` and
`BAKALARI_PASSWORD` are set, the server logs in through the normal web login form and keeps the
session cookie in memory; otherwise it fetches anonymously.

## Setup

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then fill in BAKALARI_BASE_URL (+ username/password if needed)
```

| variable | meaning | default |
|---|---|---|
| `BAKALARI_BASE_URL` | the school's Bakaláři URL, e.g. `https://bakalari.spseplzen.cz` | required |
| `BAKALARI_USERNAME` / `BAKALARI_PASSWORD` | web login, only if the public timetable redirects to `/login` | unset |
| `BAKALARI_CACHE_TTL` | seconds to cache fetched pages | `600` |
| `BAKALARI_TIMEOUT` | HTTP timeout in seconds | `20` |
| `BAKALARI_MAX_CONCURRENCY` | parallel requests to the school server | `4` |

Quick check without MCP:

```bash
uv run python scripts/smoke.py 1.A --variant permanent
```

## Using it from Claude Code / Claude Desktop

Claude Code (project-level `.mcp.json` is included; it reads `.env` from the project directory):

```bash
claude mcp add rozvrh -- uv --directory /path/to/RozvrhOptimizer run rozvrh-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rozvrh": {
      "command": "uv",
      "args": ["--directory", "/path/to/RozvrhOptimizer", "run", "rozvrh-mcp"],
      "env": {
        "BAKALARI_BASE_URL": "https://bakalari.example.cz",
        "BAKALARI_USERNAME": "...",
        "BAKALARI_PASSWORD": "..."
      }
    }
  }
}
```

Interactive testing: `npx @modelcontextprotocol/inspector uv run rozvrh-mcp`.

## Tools

| tool | purpose |
|---|---|
| `list_classes`, `list_teachers`, `list_rooms` | ids and names; every other tool accepts either the id (`C8`) or the name (`1.A`, `Novák Jan`, `2507`) |
| `get_class_timetable`, `get_teacher_timetable`, `get_room_timetable` | one timetable; `variant` = `permanent` (default) / `actual` / `next`; `compact=false` adds raw Bakaláři fields |
| `get_timetables_bulk` | many (or all) timetables of one kind in one call, concurrency-limited and cached |
| `find_free_rooms` | rooms without a lesson in a given day/period |
| `get_teacher_free_slots` | per-day free and busy periods of a teacher |
| `find_conflicts` | heuristic double-booking check for teachers or rooms |
| `get_daily_changes` | absent classes/teachers, rooms out of service, changes and supervisions for one day |
| `get_server_info`, `refresh_cache` | diagnostics and cache control |

Resources: `bakalari://classes`, `bakalari://teachers`, `bakalari://rooms`,
`bakalari://timetable/{kind}/{id}`.

### Lesson format

```json
{"day": 0, "period": 1, "subject": "Technická dokumentace", "subject_abbrev": "TED",
 "teacher": "Mgr. Bc. Pavel Petr", "teacher_abbrev": "PePa", "room": "2507",
 "groups": ["S2"], "classes": ["1.A"], "cycle": "odd", "change": "substitution",
 "change_info": "Suplování: ..."}
```

* `day`: 0 = Monday … 4 = Friday. `period`: the school's own numbering (often starts at 0);
  times are listed under `hours`.
* `cycle`: `odd` / `even` when the lesson happens only in odd (lichý) or even (sudý) weeks.
* `change`: absent for unchanged lessons; `substitution`, `moved`, `changed`, `canceled`,
  `removed`. Canceled/removed lessons do not occupy the slot.
* Teacher timetables list `classes`/`groups` instead of a teacher; room timetables list both.

## Development

```bash
uv sync --group dev
uv run pytest
uv run python scripts/capture_fixtures.py   # saves live pages to tests/fixtures (needs .env)
```

Layout: `parser.py` (pure HTML/JSON → models), `client.py` (httpx + login), `cache.py`,
`service.py` (name resolution, bulk, derived helpers), `server.py` (MCP tools).
