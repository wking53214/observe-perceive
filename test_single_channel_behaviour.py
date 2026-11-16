"""test_single_channel_behaviour.py -- what the engine does today with the cases in docs/race/CLINICIAN_REVIEW_SHEET.md.

These are NOT clinical expectations. They pin the engine's CURRENT behaviour on twelve illustrative readings, so that the clinical
owner who reads the review sheet is reading what the software really does, and so that a change to any row is a deliberate act
that updates the sheet. The same mechanism as `test_detection_record.py`: if a row here moves, do not paste in the new value. Read
which way it moved. A case that now pages may be a gap closed or a new source of false alarms; a case that stopped paging is a
regression to be explained. Update the row AND the matching row of the review sheet in the same change.

Convention: one reading repeated three times, one minute apart, no history, default settings, the age shown.
"""
from datetime import datetime, timedelta, timezone

import pytest

from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)

# id: (age_months, hr, spo2, rr, temp, regimes over three readings, reading on which the first page happens or None)
CASES = {
    "S1 infant 6 mo, HR 190, RR 55, T 39.8, SpO2 97": (6, 190, 97, 55, 39.8, ["stable", "caution", "caution"], None),
    "S2 toddler 24 mo, HR 160, RR 42, T 38.9, SpO2 95": (24, 160, 95, 42, 38.9, ["stable", "stable", "stable"], None),
    "S3 child 5 y, HR 140, RR 30, T 38.5, SpO2 91": (60, 140, 91, 30, 38.5, ["stable", "stable", "stable"], None),
    "S4 child 5 y, HR 200, other channels normal": (60, 200, 98, 22, 37.0, ["stable", "stable", "stable"], None),
    "S5 child 5 y, HR 45, other channels normal": (60, 45, 98, 18, 37.0, ["stable", "caution", "caution"], None),
    "S6 infant 6 mo, SpO2 89, other channels normal": (6, 130, 89, 35, 37.0, ["stable", "stable", "stable"], None),
    "S7 neonate 1 mo, T 34.5, other channels normal": (1, 140, 97, 40, 34.5, ["stable", "stable", "stable"], None),
    "S8 neonate 1 mo, T 38.8, other channels normal": (1, 140, 97, 40, 38.8, ["stable", "stable", "stable"], None),
    "S9 toddler 24 mo, RR 70, other channels normal": (24, 120, 96, 70, 37.0, ["stable", "stable", "stable"], None),
    "S10 child 12 y, HR 70, other channels normal (a healthy adolescent)": (144, 70, 98, 18, 37.0, ["stable", "caution", "caution"], None),
    "S11 adult 40 y, HR 70, other channels normal (out of scope)": (480, 70, 98, 14, 37.0, ["warning", "warning", "warning"], 1),
    "S12 child 5 y, SpO2 86, other channels normal": (60, 100, 86, 24, 37.0, ["warning", "warning", "warning"], 1),
}


@pytest.mark.parametrize("name", list(CASES))
def test_the_engines_current_behaviour_on_each_review_case(name):
    age, hr, o2, rr, temp, regimes, first_page = CASES[name]
    engine = ObserveClinicalEngine()
    seen, page = [], None
    for k in range(3):
        verdict = engine.evaluate(VitalsSnapshot("P", T0 + timedelta(minutes=k), hr, o2, rr, temp, {"age_months": age}))
        seen.append(verdict.regime.value)
        if verdict.escalation_required and page is None:
            page = k + 1
    assert (seen, page) == (regimes, first_page), (
        f"{name}: the engine's behaviour on a clinician-review case moved. Read which way, then update this row and the matching row "
        "of docs/race/CLINICIAN_REVIEW_SHEET.md together; do not paste in the new value.")


def test_the_review_sheet_has_a_row_for_every_case_here():
    import pathlib
    sheet = (pathlib.Path(__file__).parent / "docs" / "race" / "CLINICIAN_REVIEW_SHEET.md").read_text()
    for name in CASES:
        assert name.split(" ", 1)[0] + " " in sheet or f"| {name.split(' ', 1)[0]} |" in sheet, name
