from datetime import datetime, timedelta

import pytest

import workouts as w
from constants import WORKOUT_HEADERS

NOW = datetime(2026, 10, 2, 18, 0, 0)


def sheet(rows):
    """Build get_all_values()-style data from (ts, session, entry, exercise, raw, wt, reps, dur, sets)."""
    return [WORKOUT_HEADERS] + [[str(c) for c in r] for r in rows]


def ts(days_ago=0, hour=7, minute=0):
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=minute).strftime(w.TS_FMT)


# ---------------------------------------------------------------------------
# alias_key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,key", [
    ("lat pulldowns, 100 x 8 x 3", "lat pulldowns"),
    ("Lat Pulldowns 100x8x3", "lat pulldowns"),
    ("pushups 80", "pushups"),
    ("floor pushups 20 x 4", "floor pushups"),
    ("dead hang 40s, 35s, 30s", "dead hang"),
    ("plank 2 min", "plank"),
    ("face pulls 3 sets of 12 at 40", "face pulls"),
    ("Push-up", "push-up"),
    ("incline DB press 50×10", "incline db press"),
])
def test_alias_key(raw, key):
    assert w.alias_key(raw) == key


# ---------------------------------------------------------------------------
# Sets
# ---------------------------------------------------------------------------

def test_expand_sets_count_and_validation():
    sets = [{"weight": 100, "reps": 8, "count": 3}, {"duration_sec": 40},
            {"weight": 50, "reps": 10, "duration_sec": 30}, {"weight": 0, "reps": 12}]
    assert w.expand_sets(sets) == [
        (100.0, 8, None), (100.0, 8, None), (100.0, 8, None),
        (None, None, 40),
        (50.0, 10, None),  # reps win over duration
        (None, 12, None),  # zero weight = bodyweight
    ]


def test_expand_sets_rejects_weight_without_reps():
    # "assisted dips 50/45/50" with no reps must never be silently dropped
    with pytest.raises(w.IncompleteSetError):
        w.expand_sets([{"weight": -50}, {"weight": -45}, {"weight": -50}])


def test_assisted_per_set_weights():
    # "assisted dips 50x8/45x8/50x6" → three sets with negative assistance
    sets = [{"weight": -50, "reps": 8}, {"weight": -45, "reps": 8}, {"weight": -50, "reps": 6}]
    assert w.collapse_sets(w.expand_sets(sets)) == [
        (-50.0, 8, None, 1), (-45.0, 8, None, 1), (-50.0, 6, None, 1)]


def test_collapse_identical_sets_into_one_row():
    assert w.collapse_sets([(100, 8, None)] * 3) == [(100, 8, None, 3)]


def test_collapse_mixed_sets_into_runs():
    exp = [(100, 8, None), (100, 8, None), (110, 6, None), (100, 8, None)]
    assert w.collapse_sets(exp) == [(100, 8, None, 2), (110, 6, None, 1), (100, 8, None, 1)]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def test_session_reused_within_gap():
    last = NOW - timedelta(hours=2, minutes=59)
    assert w.assign_session(last, "S-1", NOW) == "S-1"


def test_session_new_after_gap():
    last = NOW - timedelta(hours=3, minutes=1)
    assert w.assign_session(last, "S-1", NOW) == "S-20261002-1800"


def test_session_new_when_no_history():
    assert w.assign_session(None, "", NOW) == "S-20261002-1800"


# ---------------------------------------------------------------------------
# Directive parsing
# ---------------------------------------------------------------------------

def test_parse_fenced_directive():
    raw = ('Logged! Up 10 lb 💪\n```json\n{"workout_action": "log", "entries": '
           '[{"exercise": "Lat Pulldown", "raw": "lat pulldowns", '
           '"sets": [{"weight": 100, "reps": 8, "count": 3}]}]}\n```')
    d = w.parse_workout_directive(raw)
    assert d["action"] == "log"
    assert d["entries"][0]["exercise"] == "Lat Pulldown"
    assert len(d["entries"][0]["sets"]) == 3


def test_parse_ignores_reservation_and_meal_blocks():
    raw = '```json\n[{"item": "Eggs", "calories": 140}]\n```\n```json\n{"reservation_action": "set"}\n```'
    assert w.parse_workout_directive(raw) is None


def test_parse_delete_last_needs_no_entries():
    d = w.parse_workout_directive('Done.\n```json\n{"workout_action": "delete_last", "entries": []}\n```')
    assert d == {"action": "delete_last", "entries": []}


def test_parse_reports_errors_instead_of_silently_failing():
    assert "error" in w.parse_workout_directive('```json\n{"workout_action": "nuke"}\n```')
    assert "error" in w.parse_workout_directive('```json\n{"workout_action": "log", "entries": []}\n```')
    missing_reps = ('```json\n{"workout_action": "log", "entries": [{"exercise": "Assisted Dip", '
                    '"raw": "assisted dips", "sets": [{"weight": -50}, {"weight": -45}]}]}\n```')
    assert "reps" in w.parse_workout_directive(missing_reps)["error"]
    assert "error" in w.parse_workout_directive('```json\n{"workout_action": "log", "entries": [\n```')
    assert w.parse_workout_directive("Just chatting, no JSON here.") is None


def test_parse_unfenced_single_line():
    raw = 'Nice.\n{"workout_action": "log", "entries": [{"exercise": "Dead Hang", "raw": "dead hang", "sets": [{"duration_sec": 40}]}]}'
    d = w.parse_workout_directive(raw)
    assert d["entries"][0]["sets"] == [(None, None, 40)]


# ---------------------------------------------------------------------------
# Canonical resolution
# ---------------------------------------------------------------------------

def test_alias_table_overrides_model_at_write_time():
    aliases = {"floor pushups": "Push-up"}
    assert w.resolve_canonical("floor pushups", "Floor Push-up", aliases) == "Push-up"
    assert w.resolve_canonical("face pulls", "Face Pull", aliases) == "Face Pull"


def test_build_rows_and_new_aliases():
    entries = [{"exercise": "Lat Pulldown", "raw": "lat pulldowns",
                "sets": [(100.0, 8, None)] * 2 + [(102.5, 6, None)]}]
    rows, summary = w.build_rows(entries, {}, "2026-10-02 18:00:00", "S-x", "E-x")
    assert rows == [
        ["2026-10-02 18:00:00", "S-x", "E-x", "Lat Pulldown", "lat pulldowns", 100, 8, "", 2],
        ["2026-10-02 18:00:00", "S-x", "E-x", "Lat Pulldown", "lat pulldowns", 102.5, 6, "", 1],
    ]
    assert w.new_alias_rows(summary, {}, "2026-10-02") == [
        ["lat pulldowns", "Lat Pulldown", "2026-10-02"],
        ["lat pulldown", "Lat Pulldown", "2026-10-02"],
    ]
    # Nothing new once both keys are known
    known = {"lat pulldowns": "Lat Pulldown", "lat pulldown": "Lat Pulldown"}
    assert w.new_alias_rows(summary, known, "2026-10-02") == []


def test_read_time_resolution_regroups_history():
    values = sheet([
        (ts(5), "S-a", "E-1", "Cable Pulldown", "cable pulldown", 90, 8, "", 3),
        (ts(2), "S-b", "E-2", "Lat Pulldown", "lat pulldowns", 100, 8, "", 3),
    ])
    rows = w.parse_rows(values)
    # Before remap: two separate exercises
    assert set(w.exercise_stats(rows, {}, NOW)) == {"Cable Pulldown", "Lat Pulldown"}
    # User re-points the alias in the sheet → history merges
    aliases = {"cable pulldown": "Lat Pulldown"}
    stats = w.exercise_stats(rows, aliases, NOW)
    assert set(stats) == {"Lat Pulldown"}
    assert stats["Lat Pulldown"]["sessions"] == 2


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def pulldown_history():
    return w.parse_rows(sheet([
        (ts(20), "S-1", "E-1", "Lat Pulldown", "lat pulldowns", 80, 10, "", 3),
        (ts(14), "S-2", "E-2", "Lat Pulldown", "lat pulldowns", 90, 8, "", 3),
        (ts(7), "S-3", "E-3", "Lat Pulldown", "lat pulldowns", 100, 8, "", 2),
        (ts(7, minute=5), "S-3", "E-4", "Lat Pulldown", "lat pulldowns", 120, 4, "", 1),
        (ts(2), "S-4", "E-5", "Lat Pulldown", "lat pulldowns", 100, 8, "", 3),
    ]))


def test_working_weight_is_avg_top_set_last_3_sessions():
    s = w.exercise_stats(pulldown_history(), {}, NOW)["Lat Pulldown"]
    # top sets: 80, 90, 120, 100 → last 3 = 90, 120, 100
    assert s["working"] == pytest.approx((90 + 120 + 100) / 3)


def test_pr_and_last_session():
    s = w.exercise_stats(pulldown_history(), {}, NOW)["Lat Pulldown"]
    assert s["pr"] == "120×4"
    assert s["pr_date"] == "2026-09-25"
    assert s["last_date"] == "2026-09-30"
    assert s["last_sets"] == "100×8 ×3"
    assert s["today_sets"] is None


def test_current_session_excluded_from_last():
    rows = pulldown_history() + w.parse_rows(sheet([
        (NOW.replace(hour=17, minute=30).strftime(w.TS_FMT), "S-5", "E-6",
         "Lat Pulldown", "lat pulldowns", 110, 8, "", 1),
    ]))
    s = w.exercise_stats(rows, {}, NOW)["Lat Pulldown"]
    assert s["last_date"] == "2026-09-30"      # still the prior session
    assert s["today_sets"] == "110×8"


def test_weight_tie_broken_by_reps():
    rows = w.parse_rows(sheet([
        (ts(1), "S-1", "E-1", "Squat", "squat", 185, 5, "", 1),
        (ts(1, minute=5), "S-1", "E-2", "Squat", "squat", 185, 7, "", 1),
    ]))
    assert w.exercise_stats(rows, {}, NOW)["Squat"]["pr"] == "185×7"


def test_bodyweight_and_timed_have_no_working_weight():
    rows = w.parse_rows(sheet([
        (ts(3), "S-1", "E-1", "Push-up", "pushups", "", 20, "", 4),
        (ts(1), "S-2", "E-2", "Push-up", "floor pushups", "", 25, "", 1),
        (ts(1, minute=10), "S-2", "E-3", "Dead Hang", "dead hang", "", "", 40, 1),
        (ts(1, minute=11), "S-2", "E-3", "Dead Hang", "dead hang", "", "", 45, 1),
    ]))
    stats = w.exercise_stats(rows, {"floor pushups": "Push-up", "pushups": "Push-up"}, NOW)
    assert stats["Push-up"]["kind"] == "bodyweight"
    assert stats["Push-up"]["working"] is None
    assert stats["Push-up"]["pr"] == "25 reps"
    assert stats["Dead Hang"]["kind"] == "timed"
    assert stats["Dead Hang"]["pr"] == "45s"
    assert stats["Dead Hang"]["last_sets"] == "40s, 45s"


def test_snapshot_contents():
    rows = pulldown_history()
    snap = w.build_snapshot(rows, {"lat pulldowns": "Lat Pulldown", "pulldown": "Lat Pulldown"}, NOW)
    assert "Lat Pulldown [aliases: lat pulldowns, pulldown]" in snap
    assert "working weight 103.3 lb" in snap
    assert "PR 120×4 (2026-09-25)" in snap
    assert "last session 2026-09-30: 100×8 ×3 (top 100×8)" in snap


def test_assisted_stats_less_assistance_is_better():
    rows = w.parse_rows(sheet([
        (ts(14), "S-1", "E-1", "Assisted Dip", "assisted dips", -60, 8, "", 3),
        (ts(7), "S-2", "E-2", "Assisted Dip", "assisted dips", -50, 8, "", 1),
        (ts(7, minute=3), "S-2", "E-2", "Assisted Dip", "assisted dips", -45, 8, "", 1),
        (ts(7, minute=6), "S-2", "E-2", "Assisted Dip", "assisted dips", -50, 6, "", 1),
        (ts(2), "S-3", "E-3", "Assisted Dip", "assisted dips", -55, 8, "", 3),
    ]))
    s = w.exercise_stats(rows, {}, NOW)["Assisted Dip"]
    assert s["assisted"] is True
    assert s["pr"] == "-45×8"                                    # least assistance
    assert s["working"] == pytest.approx((-60 - 45 - 55) / 3)    # top sets: -60, -45, -55
    snap = w.build_snapshot(rows, {}, NOW)
    assert "Assisted Dip (assisted: negative weight = lb of assistance" in snap
    assert "last session 2026-09-30: -55×8 ×3" in snap


def test_snapshot_empty():
    assert "No exercises logged yet" in w.build_snapshot([], {}, NOW)


# ---------------------------------------------------------------------------
# Session summary (Log-screen card)
# ---------------------------------------------------------------------------

def today_at(h, m=0):
    return NOW.replace(hour=h, minute=m).strftime(w.TS_FMT)


def summary_rows():
    # NOW is 18:00.  Prior sessions: 100×8 (d-9) and 100×8 (d-2).  Today: PR 110×6.
    return w.parse_rows(sheet([
        (ts(9), "S-1", "E-1", "Lat Pulldown", "lat pulldowns", 100, 8, "", 3),
        (ts(2), "S-2", "E-2", "Lat Pulldown", "lat pulldowns", 100, 8, "", 3),
        (ts(2, minute=30), "S-2", "E-3", "Assisted Dip", "assisted dips", -55, 8, "", 3),
        (today_at(17, 0), "S-3", "E-4", "Lat Pulldown", "lat pulldowns", 100, 8, "", 2),
        (today_at(17, 10), "S-3", "E-4", "Lat Pulldown", "lat pulldowns", 110, 6, "", 1),
        (today_at(17, 20), "S-3", "E-5", "Assisted Dip", "assisted dips", -50, 8, "", 1),
        (today_at(17, 21), "S-3", "E-5", "Assisted Dip", "assisted dips", -45, 8, "", 1),
        (today_at(17, 30), "S-3", "E-6", "Dead Hang", "dead hang", "", "", 40, 1),
    ]))


def test_session_summary_none_when_nothing_today():
    rows = w.parse_rows(sheet([(ts(2), "S-2", "E-2", "Lat Pulldown", "lat pulldowns", 100, 8, "", 3)]))
    assert w.session_summary(rows, {}, NOW) is None
    assert w.session_summary([], {}, NOW) is None


def test_session_summary_live_card():
    s = w.session_summary(summary_rows(), {}, NOW)
    assert s["session"] == "S-3" and s["live"] is True        # last set 17:30, now 18:00
    assert s["started"] == "17:00" and s["last_set"] == "17:30"
    assert s["elapsed_min"] == 60                              # live: start → now
    assert (s["n_exercises"], s["n_sets"]) == (3, 6)
    names = [e["name"] for e in s["exercises"]]
    assert names == ["Lat Pulldown", "Assisted Dip", "Dead Hang"]   # order of first set


def test_session_summary_pr_and_delta():
    ex = {e["name"]: e for e in w.session_summary(summary_rows(), {}, NOW)["exercises"]}
    lat = ex["Lat Pulldown"]
    assert lat["pr"] is True
    assert lat["delta"] == {"text": "▲ 10 lb", "tone": "ok"}
    assert lat["sets"] == [{"label": "100×8", "count": 2, "pr": False},
                           {"label": "110×6", "count": 1, "pr": True}]


def test_session_summary_assisted_less_assistance_is_improvement():
    dip = {e["name"]: e for e in w.session_summary(summary_rows(), {}, NOW)["exercises"]}["Assisted Dip"]
    assert dip["assisted"] is True and dip["pr"] is True
    # top set -45 (least assistance today) vs -55 last time: 10 lb less assistance
    assert dip["delta"] == {"text": "▼ 10 lb assist", "tone": "ok"}
    assert [s["label"] for s in dip["sets"]] == ["50 assist ×8", "45 assist ×8"]


def test_session_summary_new_exercise_has_no_pr():
    hang = {e["name"]: e for e in w.session_summary(summary_rows(), {}, NOW)["exercises"]}["Dead Hang"]
    assert hang["pr"] is False
    assert hang["delta"] == {"text": "New", "tone": "new"}
    assert hang["sets"][0]["label"] == "40s"


def test_session_summary_ended_after_live_window():
    later = NOW + timedelta(minutes=50)                         # 18:50, last set 17:30 → 80 min ago
    s = w.session_summary(summary_rows(), {}, later)
    assert s["live"] is False
    assert s["elapsed_min"] == 30                               # frozen: start → last set


def test_session_summary_same_and_lower_deltas():
    rows = w.parse_rows(sheet([
        (ts(3), "S-1", "E-1", "Squat", "squat", 185, 5, "", 1),
        (ts(3, minute=5), "S-1", "E-1", "Push-up", "pushups", "", 20, "", 1),
        (today_at(17), "S-2", "E-2", "Squat", "squat", 185, 5, "", 1),
        (today_at(17, 5), "S-2", "E-2", "Push-up", "pushups", "", 15, "", 1),
    ]))
    ex = {e["name"]: e for e in w.session_summary(rows, {}, NOW)["exercises"]}
    assert ex["Squat"]["delta"] == {"text": "= last", "tone": "flat"}
    assert ex["Squat"]["pr"] is False                           # equal, not beaten
    assert ex["Push-up"]["delta"] == {"text": "▼ 5 reps", "tone": "down"}


def test_session_summary_groups_by_alias():
    rows = w.parse_rows(sheet([
        (ts(3), "S-1", "E-1", "Cable Pulldown", "cable pulldown", 90, 8, "", 1),
        (today_at(17), "S-2", "E-2", "Lat Pulldown", "lat pulldowns", 100, 8, "", 1),
    ]))
    aliases = {"cable pulldown": "Lat Pulldown", "lat pulldowns": "Lat Pulldown"}
    ex = w.session_summary(rows, aliases, NOW)["exercises"][0]
    assert ex["delta"] == {"text": "▲ 10 lb", "tone": "ok"}    # compared against the aliased history


def test_session_markers():
    m = w.session_markers(summary_rows(), {}, NOW)
    assert len(m) == 1 and m[0]["title"] == "Training: Lat Pulldown, Assisted Dip, Dead Hang"
    assert m[0]["ts"] == datetime(2026, 10, 2, 17, 0)


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "lat pulldowns, 100 x 8 x 3",
    "dead hang 40s",
    "face pulls 3 sets of 12",
    "what's my current working weight for lat pulldowns?",
    "what's my PR on squat",
    "did 25 reps",
])
def test_workout_intent_positive(text):
    assert w.looks_like_workout(text)


@pytest.mark.parametrize("text", [
    "chicken breast 6oz",
    "2 slices of toast with butter",
    "protein shake",
    "how am I doing on protein today?",
])
def test_workout_intent_negative(text):
    assert not w.looks_like_workout(text)


def test_workout_intent_cold_start_vocabulary():
    assert w.looks_like_workout("pushups 80")
    assert w.looks_like_workout("dead hang")


def test_workout_intent_via_known_alias():
    assert not w.looks_like_workout("skull crushers")
    assert w.looks_like_workout("skull crushers", {"skull crushers"})


# ---------------------------------------------------------------------------
# Turn classification
# ---------------------------------------------------------------------------

RECENT = NOW - timedelta(minutes=10)


@pytest.mark.parametrize("text,prev_mode,prev_ts,expected", [
    # strong workout signals win regardless of context
    ("lat pulldowns 100x8x3", "", None, True),
    ("did pushups 80 and ate an apple", "", None, True),        # mixed -> case C
    # clear food / done signals beat follow-up context
    ("chicken breast 6oz", "workout", RECENT, False),
    ("time to eat!", "workout", RECENT, False),
    ("breaking my fast now", "workout", RECENT, False),
    ("post-workout shake", "workout", RECENT, False),
    ("done with my workout", "workout", RECENT, False),
    ("workout's over", "workout", RECENT, False),
    ("leaving the gym", "workout", RECENT, False),
    ("how's my protein today?", "workout", RECENT, False),
    # weak gym words with no food signal
    ("heading to the gym", "", None, True),
    # follow-ups depend on the coach's last reply
    ("yes", "workout", RECENT, True),
    ("scratch that", "workout", RECENT, True),
    ("sorry I misread the stack, it was actually one ten not one hundred", "workout", RECENT, True),
    ("yes", "", RECENT, False),
    ("yes", "workout", NOW - timedelta(hours=4), False),
])
def test_is_workout_turn(text, prev_mode, prev_ts, expected):
    assert w.is_workout_turn(text, set(), prev_mode, prev_ts, NOW) is expected


def test_parse_remap_action_without_sets():
    raw = '```json\n{"workout_action": "remap", "entries": [{"exercise": "Cable Pulldown", "raw": "cable pulldown"}]}\n```'
    d = w.parse_workout_directive(raw)
    assert d == {"action": "remap", "entries": [
        {"exercise": "Cable Pulldown", "raw": "cable pulldown", "sets": [], "remap": True}]}


def test_remap_pairs_only_for_flagged_changes():
    entries = [
        {"exercise": "Cable Pulldown", "raw": "cable pulldown", "remap": True},
        {"exercise": "Lat Pulldown", "raw": "lat pulldowns", "remap": True},     # already mapped
        {"exercise": "Squat", "raw": "squats", "remap": False},
    ]
    aliases = {"cable pulldown": "Lat Pulldown", "lat pulldowns": "Lat Pulldown"}
    assert w.remap_pairs(entries, aliases) == {"cable pulldown": "Cable Pulldown"}
