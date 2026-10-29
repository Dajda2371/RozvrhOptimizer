from __future__ import annotations

import json

import pytest
from conftest import LOGIN_PAGE, atom, day, hour, load_fixture, page, timetable_data

from rozvrh_mcp.errors import LoginError, ParseError, TimetableUnavailableError
from rozvrh_mcp.parser import (
    classify_change,
    extract_form_state,
    extract_timetable_data,
    parse_changes_page,
    parse_selector_page,
    parse_timetable,
)


def test_selector_page_lists_entities(sample_class_page):
    lists = parse_selector_page(sample_class_page)
    assert [e.id for e in lists["class"]] == ["C8", "C9", "CA"]
    assert lists["class"][0].name == "1.A"
    assert lists["teacher"][0].name == "Ambrož Jan"
    assert [e.name for e in lists["room"]] == ["1101", "1200", "2507"]


def test_selector_page_without_selects_raises():
    with pytest.raises(ParseError):
        parse_selector_page("<html><body>nothing</body></html>")


def test_login_page_is_detected():
    with pytest.raises(LoginError):
        parse_selector_page(LOGIN_PAGE)
    with pytest.raises(LoginError):
        parse_timetable(LOGIN_PAGE, kind="class", id="C8", name="1.A", variant="permanent")


def test_extract_timetable_data_handles_braces_in_strings(sample_class_page):
    data = extract_timetable_data(sample_class_page)
    assert data is not None and len(data["Days"]) == 5
    assert extract_timetable_data("<html>no data</html>") is None


def test_json_timetable_basic_shape(sample_class_page):
    tt = parse_timetable(sample_class_page, kind="class", id="C8", name="1.A", variant="permanent")
    assert tt.source == "json"
    assert [h.period for h in tt.hours] == [0, 1, 2, 3]
    assert tt.hours[1].start == "8:00" and tt.hours[1].end == "8:45"
    assert [d.day_abbrev for d in tt.days] == ["po", "út", "st", "čt", "pá"]
    assert tt.days[0].date is None
    assert tt.days[2].day_off and tt.days[2].date == "28.10."
    assert tt.days[2].day_off_name.startswith("Den vzniku")


def test_period_mapped_by_begin_time(sample_class_page):
    tt = parse_timetable(sample_class_page, kind="class", id="C8", name="1.A", variant="permanent")
    monday = tt.days[0].lessons
    assert [l.period for l in monday] == [1, 1, 2]
    first = monday[0]
    assert first.subject == "Technická dokumentace"
    assert first.subject_abbrev == "TED"
    assert first.teacher == "Mgr. Bc. Pavel Petr"
    assert first.teacher_abbrev == "PePa"
    assert first.room == "2507"
    assert first.groups == ["S2"]
    assert first.classes == ["1.A"]
    assert first.change == "none"
    assert monday[2].groups == [] and monday[2].subject_abbrev == "ZAE"
    # the 0th period maps correctly too
    assert tt.days[1].lessons[0].period == 0


def test_cycles_and_changes(sample_class_page):
    tt = parse_timetable(sample_class_page, kind="class", id="C8", name="1.A", variant="permanent")
    tuesday = tt.days[1].lessons
    assert [l.cycle for l in tuesday] == ["odd", "even"]
    friday = tt.days[4].lessons
    assert friday[0].change == "removed" and not friday[0].active
    assert friday[0].change_info.startswith("Zrušeno")
    assert friday[1].change == "substitution" and friday[1].active


def test_compact_dict_drops_empty_and_raw(sample_class_page):
    tt = parse_timetable(sample_class_page, kind="class", id="C8", name="1.A", variant="permanent")
    d = tt.to_dict()
    lesson = d["lessons"][0]
    assert "raw" not in lesson and "change" not in lesson and "theme" not in lesson
    full = tt.to_dict(compact=False)
    assert full["lessons"][0]["raw"]["SubjectAbbrev"] == "TED"
    assert isinstance(full["lessons"][0]["raw"]["TooltipDetails"], dict)
    assert full["days"][0]["lessons"]
    json.dumps(full, ensure_ascii=False)  # serialisable


def test_teacher_view_fills_teacher_and_classes():
    data = timetable_data([day("po", [hour("8:00", [atom(Teacher="", TeacherFullname="", GroupsNames="MA2 celá")])])])
    tt = parse_timetable(page(data), kind="teacher", id="UY045", name="Ambrož Jan", variant="permanent")
    lesson = tt.lessons[0]
    assert lesson.teacher == "Ambrož Jan"
    assert lesson.teacher_abbrev is None
    assert lesson.classes == ["MA2"] and lesson.groups == ["MA2 celá"]


def test_room_view_splits_joined_classes():
    data = timetable_data([day("po", [hour("8:00", [atom(GroupsNames="3.A SL<br/>3.B SL", GroupsFullNames="3.A SL - Silnoproud<br/>3.B SL - Silnoproud")])])])
    tt = parse_timetable(page(data), kind="room", id="ZW", name="1101", variant="permanent")
    assert tt.lessons[0].classes == ["3.A", "3.B"]
    assert tt.lessons[0].groups == ["3.A SL", "3.B SL"]


def test_missing_keys_and_broken_tooltip_do_not_raise():
    weird = {"Type": "atom", "TooltipDetails": "{not json", "SubjectText": "X"}
    data = timetable_data([day("po", [{"Atoms": [weird, "garbage", None], "Begin": "nonsense"}]), "junk"])
    tt = parse_timetable(page(data), kind="class", id="C8", name="1.A", variant="actual")
    assert len(tt.lessons) == 1
    assert tt.lessons[0].subject == "X"
    assert tt.lessons[0].period == 0  # positional fallback


def test_tooltip_time_fallback_when_begin_unknown():
    a = atom(BeginTimeValue=None, Begin=None)
    data = timetable_data([day("po", [{"Atoms": [a], "Begin": None, "BeginTimeValue": None}])])
    tt = parse_timetable(page(data), kind="class", id="C8", name="1.A", variant="permanent")
    assert tt.lessons[0].period == 1


def test_empty_timetable_page_raises_unavailable():
    with pytest.raises(TimetableUnavailableError):
        parse_timetable(page(None), kind="class", id="C8", name="1.A", variant="permanent")


def test_legacy_grid_fallback():
    html = load_fixture("legacy_grid.html")
    tt = parse_timetable(html, kind="teacher", id="UWZIS", name="Adamčíková Gabriela", variant="actual")
    assert tt.source == "html"
    assert [h.period for h in tt.hours] == [1, 2, 3]
    assert tt.days[0].date == "10.11." and tt.days[1].day_abbrev == "út"
    lessons = tt.days[0].lessons
    assert len(lessons) == 2
    assert lessons[0].period == 2 and lessons[0].subject == "Občanská výchova" and lessons[0].subject_abbrev == "OV"
    assert lessons[0].room == "401" and lessons[0].classes == ["Z9.A"]
    assert lessons[0].teacher == "Adamčíková Gabriela"
    assert lessons[1].period == 3 and lessons[1].change == "substitution"
    assert lessons[1].change_info == "Suplování: Novák Jan"
    assert tt.days[1].lessons == []
    lists = parse_selector_page(html)
    assert lists["teacher"][0].name == "Adamčíková Gabriela"


@pytest.mark.parametrize(
    "args,expected",
    [
        (("atom", 0, "", False), "none"),
        (("removed", 0, "Zrušeno", False), "removed"),
        (("atom", 21, "", False), "removed"),
        (("atom", 17, "", False), "canceled"),
        (("atom", 1, "Zrušeno (ANJ)", False), "canceled"),
        (("atom", 8, "Suplování: X", True), "substitution"),
        (("atom", 4, "Přesun na 6.11., 07:05", True), "moved"),
        (("atom", 1, "něco jiného", False), "changed"),
        (("atom", 1, "", True), "changed"),
        (("atom", "x", None, False), "none"),
    ],
)
def test_classify_change(args, expected):
    assert classify_change(*args) == expected


def test_changes_page():
    html = load_fixture("zmeny.html")
    changes = parse_changes_page(html)
    assert changes.date == "Thursday 29. 10."
    assert changes.absent_classes == []
    names = [t.name for t in changes.absent_teachers]
    assert names[0] == "Egrtová Miroslava" and len(names) == 4
    assert changes.absent_teachers[0].absent_periods == [1, 3, 4, 6]
    assert changes.absent_teachers[2].absent_periods == []
    assert changes.rooms_out_of_service == [] and changes.teacher_changes == [] and changes.supervisions == []
    d = changes.to_dict()
    json.dumps(d, ensure_ascii=False)


def test_changes_page_with_filled_sections():
    html = """<html><body><form id="form1"><input type="hidden" name="__VIEWSTATE" value="v"/>
    <input type="submit" name="ButtonPrev" value=""/><input type="text" name="DateEdit" value="10/29/2026"/>
    <input type="checkbox" name="cb" value="1"/><input type="radio" name="r" value="a" checked/>
    <h1>Změny v rozvrhu Friday 30. 10.</h1>
    <table class="datagrid"><tr><th>Nepřítomné třídy</th><th>info</th></tr><tr><td>1.A</td><td>exkurze</td></tr></table>
    <div> Nepřítomní učitelé - nejsou </div>
    <table class="datagrid"><tr><th>Místnosti mimo provoz</th></tr><tr><td>2507</td></tr></table>
    <table class="datagrid"><tr><th>Změny v rozvrzích tříd</th><th>hodina</th><th>změna</th></tr><tr><td>1.B</td><td>3</td><td>supl</td></tr></table>
    <table class="datagrid"><tr><th>Něco nového</th></tr><tr><td>x</td></tr></table>
    </form></body></html>"""
    changes = parse_changes_page(html)
    assert changes.date == "Friday 30. 10."
    assert changes.absent_classes == ["1.A"]
    assert changes.absent_teachers == []
    assert changes.rooms_out_of_service == ["2507"]
    assert changes.class_changes == [["1.B", "3", "supl"]]
    assert changes.other_sections == {"Něco nového": [["x"]]}
    state = extract_form_state(html)
    assert state == {"__VIEWSTATE": "v", "DateEdit": "10/29/2026", "r": "a"}


def test_changes_page_without_heading():
    with pytest.raises(ParseError):
        parse_changes_page("<html><body>nope</body></html>")
