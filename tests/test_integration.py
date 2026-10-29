"""Live checks against the configured school. Skipped unless BAKALARI_BASE_URL is set in the environment."""

from __future__ import annotations

import os

import pytest

from rozvrh_mcp.client import BakalariClient
from rozvrh_mcp.config import load_settings
from rozvrh_mcp.service import TimetableService

pytestmark = pytest.mark.skipif(not os.environ.get("BAKALARI_BASE_URL"), reason="BAKALARI_BASE_URL not set")


async def test_live_lists_and_one_timetable():
    settings = load_settings(dotenv=False)
    async with BakalariClient(settings) as client:
        service = TimetableService(client, settings)
        classes = await service.entities("class")
        assert classes, "no classes listed"
        tt = await service.get_timetable("class", classes[0].id, "permanent")
        assert tt.lessons, "first class has no lessons"
        assert tt.hours
