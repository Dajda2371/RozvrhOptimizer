from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def atom(**overrides: Any) -> dict[str, Any]:
    """A realistic atom as embedded by Bakaláři 2.x, with overrides."""
    base: dict[str, Any] = {
        "ChangeInfo": "",
        "AbsentInfo": None,
        "IdentCode": "1       003C8JT2PUW02Z0480045",
        "GroupsNames": "S2",
        "GroupsFullNames": "S2 - Skupina 2",
        "Homeworks": None,
        "Notice": "",
        "Room": "2507",
        "RoomFullName": "2507 - 2507 jazyková ANJ",
        "SubjectText": "Technická dokumentace",
        "SubjectAbbrev": "TED",
        "Teacher": "PePa",
        "TeacherFullname": "Mgr. Bc. Pavel Petr",
        "Theme": "",
        "HasChanged": False,
        "HasAbsent": False,
        "CycleText": "",
        "StackLevel": 0,
        "Type": "atom",
        "Day": "26.10.2026 (pondělí)",
        "Time": "8:00 - 8:45",
        "InfoChangeCode": 2,
        "HourIndex": 3,
        "BeginTimeValue": 480,
        "EndTimeValue": 525,
        "Begin": "08:00:00",
        "End": "08:45:00",
        "ChangeType": 0,
    }
    base.update(overrides)
    tooltip = {
        "type": base["Type"],
        "subjecttext": base.get("SubjectText"),
        "teacher": base.get("TeacherFullname"),
        "room": base.get("RoomFullName"),
        "group": base.get("GroupsFullNames"),
        "changeinfo": base.get("ChangeInfo"),
        "infoChangeCode": "NoChange" if base["InfoChangeCode"] == 2 else "Changed",
        "time": "1 (8:00 - 8:45)",
        "hourIndex": base["HourIndex"],
    }
    base.setdefault("TooltipDetails", json.dumps(tooltip, ensure_ascii=False))
    return base


HOURS = [
    {"Caption": "0", "BeginTime": "7:05", "EndTime": "7:50"},
    {"Caption": "1", "BeginTime": "8:00", "EndTime": "8:45"},
    {"Caption": "2", "BeginTime": "8:55", "EndTime": "9:40"},
    {"Caption": "3", "BeginTime": "10:00", "EndTime": "10:45"},
]


def hour(begin: str, atoms: list[dict[str, Any]]) -> dict[str, Any]:
    h, m = begin.split(":")
    return {
        "HourText": None,
        "InfoRemoved": None,
        "Atoms": atoms,
        "Type": "atoms",
        "BeginTimeValue": int(h) * 60 + int(m),
        "Begin": f"{int(h):02d}:{m}:00",
        "End": "",
        "Index": 99,
    }


def day(abbrev: str, hours: list[dict[str, Any]], date: str = "", off: bool = False, off_name: str = "") -> dict[str, Any]:
    return {
        "Hours": hours,
        "DayAbbrev": abbrev,
        "Date": date,
        "DayOff": off,
        "DayOffName": off_name,
        "PauseGuards": [],
        "HourGuards": [],
        "GeneralGuards": [],
        "Absences": [],
    }


def timetable_data(days: list[dict[str, Any]], hours: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "TimetableDataAvailable": False,
        "SelectedTeacher": None,
        "Days": days,
        "Hours": hours if hours is not None else HOURS,
        "Raster": [],
        "UserType": 0,
    }


SELECTS = """
<select id="selectedClass" class="custom-select" onchange="changeClass()"><option></option>
<option value="C8">1.A</option><option value="C9">1.B</option><option value="CA">1.C</option></select>
<select id="selectedTeacher" class="custom-select"><option></option>
<option value="UY045">Ambrož Jan</option><option value="UQZYX">Anderle Jan</option><option value="UZKA6">Anderle Pavel</option>
<option value="U0001">Petr Pavel</option></select>
<select id="selectedRoom" class="custom-select"><option></option>
<option value="ZW">1101</option><option value="51">1200</option><option value="R3">2507</option></select>
"""


def page(data: dict[str, Any] | None = None, selects: str = SELECTS) -> str:
    script = ""
    if data is not None:
        script = f"<script>\n        const timetableData = {json.dumps(data, ensure_ascii=False)};\n        self.x = 1;\n</script>"
    return f"<html><head><title>Bakaláři - Rozvrh</title></head><body>{selects}<div id='main' class='bk-timetable-main'></div>{script}</body></html>"


LOGIN_PAGE = """<html><head><title>Bakalari - login to the system</title></head><body>
<form id="formlogin" action="Login" onsubmit="makeDisable()">
<input type="hidden" name="__RequestVerificationToken" value="tok123" />
<input type="text" name="username" id="username" required />
<input type="password" name="password" id="password" required>
<input type='checkbox' name='persistent' id='persistent' value="true" />
<input type="hidden" name="returnUrl" value="/Timetable/Public/" />
<button class="btn-login" type="submit" name="login" formaction="/Login" formmethod="post">Log In</button>
</form></body></html>"""


@pytest.fixture
def sample_class_page() -> str:
    days = [
        day("po", [hour("8:00", [atom(), atom(GroupsNames="S1", SubjectText="Český jazyk a komunikace", SubjectAbbrev="CJK", Teacher="ChLu", TeacherFullname="Mgr. Lucie Charvátová", Room="2508")]),
                   hour("8:55", [atom(GroupsNames="", SubjectText="Základy elektrotechniky", SubjectAbbrev="ZAE", Teacher="BaMa", TeacherFullname="Ing. Martin Baxa", Room="2508")])]),
        day("út", [hour("7:05", [atom(CycleText="L", SubjectText="Matematika", SubjectAbbrev="MAT", Teacher="VaIv", TeacherFullname="Mgr. Ivana Vargová", Room="1501"),
                                atom(CycleText="S", SubjectText="Fyzika", SubjectAbbrev="FYZ", Teacher="NoJa", TeacherFullname="Mgr. Jan Novák", Room="1502")])]),
        day("st", [], date="28.10.", off=True, off_name="Den vzniku samostatného Československého státu"),
        day("čt", []),
        day("pá", [hour("10:00", [atom(Type="removed", ChangeInfo="Zrušeno (ANJ, Hromádková Petra)", SubjectText=None, Room=None, Teacher=None, TeacherFullname=None, GroupsNames=None, InfoChangeCode=0),
                                 atom(ChangeInfo="Suplování: Vlčková Světlana (SkMo)", ChangeType=8, HasChanged=True, SubjectText="Anglický jazyk", SubjectAbbrev="ANJ", Teacher="VlSv", TeacherFullname="Mgr. Světlana Vlčková", Room="2208", InfoChangeCode=1)])]),
    ]
    return page(timetable_data(days))
