"""Integration tests for the workout flow in server.py against an in-memory sheet
and a scripted fake Gemini session (no network, no credentials)."""
import json

import gspread
import pytest
from fastapi.testclient import TestClient

import server
from constants import WS_EXERCISE_ALIASES, WS_WORKOUT_LOGS


class FakeWS:
    def __init__(self, title, rows=None):
        self.title = title
        self.rows = [list(r) for r in (rows or [])]

    def get_all_values(self):
        return [[str(c) for c in r] for r in self.rows]

    def get_all_records(self):
        if not self.rows:
            return []
        hdr = self.rows[0]
        return [dict(zip(hdr, r)) for r in self.rows[1:]]

    def append_row(self, row, **_):
        self.rows.append(list(row))

    def append_rows(self, rows, **_):
        self.rows.extend(list(r) for r in rows)

    def delete_rows(self, start, end=None):
        del self.rows[start - 1:(end or start)]

    def clear(self):
        self.rows = []

    # --- cell-level API used by the Mode column and alias remaps ---
    @property
    def col_count(self):
        return self._cols if hasattr(self, "_cols") else 3

    def add_cols(self, n):
        self._cols = self.col_count + n

    def _cell(self, a1):
        col = ord(a1[0]) - ord("A")
        return int(a1[1:]) - 1, col

    def acell(self, a1):
        r, c = self._cell(a1)
        val = self.rows[r][c] if r < len(self.rows) and c < len(self.rows[r]) else ""
        return type("Cell", (), {"value": val})()

    def update_acell(self, a1, value):
        self.update(range_name=a1, values=[[value]])

    def update(self, range_name=None, values=None, **_):
        if range_name is None:
            return
        start = range_name.split(":")[0]
        r, c = self._cell(start)
        for dr, vals in enumerate(values):
            row = self.rows[r + dr]
            row.extend([""] * (c + len(vals) - len(row)))
            row[c:c + len(vals)] = vals


class FakeSheet:
    def __init__(self):
        self.tabs = {}
        self.sheet1 = FakeWS("Log", [["Timestamp", "Item", "Calories", "Protein", "Density", "Week", "Emoji", "Mode"]])

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows=None, cols=None):
        self.tabs[title] = FakeWS(title)
        return self.tabs[title]


class _Chunk:
    def __init__(self, text):
        self.text = text


class FakeSession:
    def __init__(self, reply):
        self.reply = reply

    def send_message_stream(self, parts):
        for line in self.reply.splitlines(keepends=True):
            yield _Chunk(line)


@pytest.fixture
def env(monkeypatch):
    sh = FakeSheet()
    server._cache.clear()
    server._chat_mode_col_ready.clear()
    monkeypatch.setattr(server, "_get_sh", lambda user_id="ed": sh)
    state = {"reply": "", "prompt": ""}

    def fake_session(model_id, system_prompt, history):
        state["prompt"] = system_prompt
        return FakeSession(state["reply"])

    monkeypatch.setattr(server, "_make_chat_session", fake_session)
    return sh, state, TestClient(server.app)


def chat(client, state, text, reply):
    state["reply"] = reply
    r = client.post("/api/chat", data={"text": text, "user_id": "ed"})
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    return next(e for e in events if e.get("done"))


def directive(obj):
    return "\n```json\n" + json.dumps(obj) + "\n```\n"


LOG_PULLDOWN = {"workout_action": "log", "entries": [
    {"exercise": "Lat Pulldown", "raw": "lat pulldowns", "sets": [{"weight": 100, "reps": 8, "count": 3}]}]}


def test_log_writes_rows_and_aliases(env):
    sh, state, client = env
    done = chat(client, state, "lat pulldowns, 100 x 8 x 3", "Logged!" + directive(LOG_PULLDOWN))

    assert done["workout"]["entries"][0]["exercise"] == "Lat Pulldown"
    rows = sh.tabs[WS_WORKOUT_LOGS].rows
    assert len(rows) == 2                      # header + one collapsed row
    assert rows[1][3:] == ["Lat Pulldown", "lat pulldowns", 100, 8, "", 3]
    aliases = {r[0]: r[1] for r in sh.tabs[WS_EXERCISE_ALIASES].rows[1:]}
    assert aliases == {"lat pulldowns": "Lat Pulldown", "lat pulldown": "Lat Pulldown"}
    # Workout mode was injected into the prompt
    assert "WORKOUT MODE" in state["prompt"]
    # Nothing touched the food log
    assert len(sh.sheet1.rows) == 1


def test_food_turn_has_no_workout_section(env):
    sh, state, client = env
    chat(client, state, "chicken breast 6oz", "Nice.")
    assert "WORKOUT MODE" not in state["prompt"]


def test_snapshot_reflects_logged_history(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns, 100 x 8 x 3", "ok" + directive(LOG_PULLDOWN))
    chat(client, state, "what's my working weight for lat pulldowns?", "100 lb.")
    assert "Lat Pulldown [aliases: lat pulldowns]" in state["prompt"]
    assert "working weight 100 lb" in state["prompt"]


def test_alias_table_overrides_model_name(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    drift = {"workout_action": "log", "entries": [
        {"exercise": "Lat Pull-Down", "raw": "lat pulldowns", "sets": [{"weight": 110, "reps": 6}]}]}
    chat(client, state, "lat pulldowns 110 x 6", "ok" + directive(drift))
    names = {r[3] for r in sh.tabs[WS_WORKOUT_LOGS].rows[1:]}
    assert names == {"Lat Pulldown"}


def test_same_session_and_entry_ids(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    hang = {"workout_action": "log", "entries": [
        {"exercise": "Dead Hang", "raw": "dead hang", "sets": [{"duration_sec": 40}, {"duration_sec": 35}]}]}
    chat(client, state, "dead hang 40s, 35s", "ok" + directive(hang))
    rows = sh.tabs[WS_WORKOUT_LOGS].rows[1:]
    assert len(rows) == 3
    assert len({r[1] for r in rows}) == 1          # one session
    assert rows[1][2] == rows[2][2]                # both hang rows share an entry id
    assert [r[7] for r in rows[1:]] == [40, 35]


def test_replace_last_rewrites_whole_entry(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    original = list(sh.tabs[WS_WORKOUT_LOGS].rows[1])
    fix = {"workout_action": "replace_last", "entries": [
        {"exercise": "Lat Pulldown", "raw": "lat pulldowns", "sets": [{"weight": 110, "reps": 8, "count": 3}]}]}
    done = chat(client, state, "that was actually 110", "Fixed." + directive(fix))
    rows = sh.tabs[WS_WORKOUT_LOGS].rows[1:]
    assert done["workout"]["action"] == "replace_last"
    assert len(rows) == 1
    assert rows[0][5] == 110
    assert rows[0][:3] == original[:3]             # timestamp, session, entry id preserved
    assert "WORKOUT MODE" in state["prompt"]       # short follow-up kept workout mode


def test_delete_last(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    done = chat(client, state, "scratch that", "Deleted." +
                directive({"workout_action": "delete_last", "entries": []}))
    assert done["workout"]["action"] == "delete_last"
    assert len(sh.tabs[WS_WORKOUT_LOGS].rows) == 1


def test_mixed_food_and_workout_logs_nothing(env):
    sh, state, client = env
    meal = '\n```json\n[{"item": "Protein Bar", "calories": 200, "protein": 20, "density": "10.0%", "emoji": "🍫"}]\n```\n'
    done = chat(client, state, "did pulldowns 100x8x3 and ate a protein bar",
                "ok" + meal + directive(LOG_PULLDOWN))
    assert done["logged"] == [] and done["workout"] is None
    assert WS_WORKOUT_LOGS not in sh.tabs
    assert len(sh.sheet1.rows) == 1


def test_directive_hidden_from_stored_chat(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "Logged!" + directive(LOG_PULLDOWN))
    stored = sh.tabs["Chat_History"].rows[-1][2]
    assert "workout_action" not in stored


# ---------------------------------------------------------------------------
# Follow-up detection via the coach's last reply
# ---------------------------------------------------------------------------

def test_coach_reply_tagged_as_workout(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    chat(client, state, "chicken breast 6oz", "Nice.")
    rows = sh.tabs["Chat_History"].rows
    assert rows[0][3] == "Mode"
    coach = [r for r in rows if r[1] == "assistant"]
    assert coach[0][3] == "workout"
    assert len(coach[1]) == 3            # food reply carries no mode


def test_long_correction_after_workout_reply_keeps_workout_mode(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    chat(client, state, "sorry, I misread the stack on that one, it was actually one hundred ten not one hundred", "Fixed.")
    assert "WORKOUT MODE" in state["prompt"]


def test_food_after_workout_reply_is_food(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    chat(client, state, "chicken breast 6oz", "Nice.")
    assert "WORKOUT MODE" not in state["prompt"]


def test_workout_over_ends_workout_context(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    chat(client, state, "ok done with my workout", "Great session!")
    assert "WORKOUT MODE" not in state["prompt"]
    # The coach's reply to that was a non-workout turn, so a bare follow-up stays food
    chat(client, state, "yes", "ok")
    assert "WORKOUT MODE" not in state["prompt"]


def test_name_confirmation_follow_up(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    chat(client, state, "cable pulldown 80x10x3", "Is 'cable pulldown' your Lat Pulldown?")
    chat(client, state, "no, it's its own thing", "ok")
    assert "WORKOUT MODE" in state["prompt"]


def test_unusable_workout_block_warns_user(env):
    sh, state, client = env
    bad = {"workout_action": "log", "entries": [
        {"exercise": "Assisted Dip", "raw": "assisted dips", "sets": [{"weight": -50}, {"weight": -45}]}]}
    state["reply"] = "Logged!" + directive(bad)
    r = client.post("/api/chat", data={"text": "assisted dips 50/45/50", "user_id": "ed"})
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    streamed = "".join(e.get("token", "") for e in events)
    assert "Workout not saved" in streamed and "reps" in streamed
    assert next(e for e in events if e.get("done"))["workout"] is None
    assert WS_WORKOUT_LOGS not in sh.tabs
    # The warning is stored in history so the coach sees it next turn
    assert "Workout not saved" in sh.tabs["Chat_History"].rows[-1][2]


def test_assisted_negative_weight_round_trip(env):
    sh, state, client = env
    dips = {"workout_action": "log", "entries": [{"exercise": "Assisted Dip", "raw": "assisted dips", "sets": [
        {"weight": -50, "reps": 8}, {"weight": -45, "reps": 8}, {"weight": -50, "reps": 8}]}]}
    chat(client, state, "assisted dips 50/45/50 x 8", "ok" + directive(dips))
    rows = sh.tabs[WS_WORKOUT_LOGS].rows[1:]
    assert [r[5] for r in rows] == [-50, -45, -50]
    chat(client, state, "what's my PR on assisted dips?", "45 lb assist.")
    assert "PR -45×8" in state["prompt"]


# ---------------------------------------------------------------------------
# /api/workouts/today (Log-screen session card)
# ---------------------------------------------------------------------------

def test_workouts_today_empty(env):
    sh, state, client = env
    r = client.get("/api/workouts/today", params={"user_id": "ed"})
    assert r.status_code == 200
    assert r.json() == {"session": None, "markers": []}


def test_workouts_today_after_logging(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    data = client.get("/api/workouts/today", params={"user_id": "ed"}).json()
    s = data["session"]
    assert s["live"] is True and s["n_exercises"] == 1 and s["n_sets"] == 3
    assert s["exercises"][0]["name"] == "Lat Pulldown"
    assert s["exercises"][0]["sets"] == [{"label": "100×8", "count": 3, "pr": False}]
    assert s["exercises"][0]["delta"]["tone"] == "new"
    assert len(data["markers"]) == 1
    assert data["markers"][0]["title"] == "Training: Lat Pulldown"
    assert 0 <= data["markers"][0]["pos_pct"] <= 100


def test_workouts_today_refreshes_after_new_log(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    assert client.get("/api/workouts/today").json()["session"]["n_sets"] == 3
    hang = {"workout_action": "log", "entries": [
        {"exercise": "Dead Hang", "raw": "dead hang", "sets": [{"duration_sec": 40}]}]}
    chat(client, state, "dead hang 40s", "ok" + directive(hang))
    s = client.get("/api/workouts/today").json()["session"]       # cache was invalidated by the write
    assert s["n_exercises"] == 2 and s["n_sets"] == 4


# ---------------------------------------------------------------------------
# Train tab endpoints + in-app alias editing
# ---------------------------------------------------------------------------

def log_two_exercises(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    wrong = {"workout_action": "log", "entries": [
        {"exercise": "Lat Pulldown", "raw": "cable pulldown", "sets": [{"weight": 80, "reps": 10, "count": 3}]}]}
    chat(client, state, "cable pulldown 80x10x3", "ok" + directive(wrong))
    return sh, client


def test_summary_and_detail_endpoints(env):
    sh, state, client = env
    chat(client, state, "lat pulldowns 100x8x3", "ok" + directive(LOG_PULLDOWN))
    s = client.get("/api/workouts/summary").json()
    assert s["week"]["sessions"] == 1
    assert [e["name"] for e in s["exercises"]] == ["Lat Pulldown"]
    d = client.get("/api/workouts/exercise", params={"name": "Lat Pulldown"}).json()
    assert d["n_sessions"] == 1 and d["aliases"] == ["lat pulldown", "lat pulldowns"]
    assert client.get("/api/workouts/exercise", params={"name": "Nope"}).status_code == 404


def test_summary_empty(env):
    sh, state, client = env
    s = client.get("/api/workouts/summary").json()
    assert s["exercises"] == [] and s["week"]["sessions"] == 0


def test_remap_endpoint_moves_past_logs(env):
    sh, client = log_two_exercises(env)
    r = client.post("/api/workouts/remap", json={"alias": "cable pulldown", "exercise": "Cable Pulldown"})
    assert r.status_code == 200 and r.json()["exercise"] == "Cable Pulldown"
    names = [e["name"] for e in client.get("/api/workouts/summary").json()["exercises"]]
    assert sorted(names) == ["Cable Pulldown", "Lat Pulldown"]
    d = client.get("/api/workouts/exercise", params={"name": "Cable Pulldown"}).json()
    assert d["series"][0]["label"] == "80×10"                        # the earlier log followed the remap
    keys = [r[0] for r in sh.tabs[WS_EXERCISE_ALIASES].rows[1:]]
    assert keys.count("cable pulldown") == 1                          # re-pointed in place


def test_rename_endpoint_renames_everything(env):
    sh, client = log_two_exercises(env)
    r = client.post("/api/workouts/rename", json={"exercise": "Lat Pulldown", "new_name": "Wide Pulldown"})
    assert r.status_code == 200
    names = {e["name"] for e in client.get("/api/workouts/summary").json()["exercises"]}
    assert names == {"Wide Pulldown"}                                  # both phrases followed
    d = client.get("/api/workouts/exercise", params={"name": "Wide Pulldown"}).json()
    assert d["n_sessions"] == 1 and "cable pulldown" in d["aliases"]    # same session, merged top set
    # New logs under the old phrase now land on the new name
    chat(client, env[1], "lat pulldowns 110x6", "ok" + directive({"workout_action": "log", "entries": [
        {"exercise": "Lat Pulldown", "raw": "lat pulldowns", "sets": [{"weight": 110, "reps": 6}]}]}))
    assert sh.tabs[WS_WORKOUT_LOGS].rows[-1][3] == "Wide Pulldown"


def test_rename_merges_into_existing_exercise(env):
    sh, client = log_two_exercises(env)
    client.post("/api/workouts/remap", json={"alias": "cable pulldown", "exercise": "Cable Pulldown"})
    r = client.post("/api/workouts/rename", json={"exercise": "Cable Pulldown", "new_name": "Lat Pulldown"})
    assert r.status_code == 200
    names = {e["name"] for e in client.get("/api/workouts/summary").json()["exercises"]}
    assert names == {"Lat Pulldown"}


def test_edit_endpoint_validation(env):
    sh, client = log_two_exercises(env)
    assert client.post("/api/workouts/remap", json={"alias": "123", "exercise": "X"}).status_code == 400
    assert client.post("/api/workouts/remap", json={"alias": "cable pulldown", "exercise": "  "}).status_code == 400
    assert client.post("/api/workouts/remap",
                       json={"alias": "cable pulldown", "exercise": "x" * 61}).status_code == 400
    assert client.post("/api/workouts/rename",
                       json={"exercise": "Lat Pulldown", "new_name": "Lat Pulldown"}).status_code == 400
    assert client.post("/api/workouts/rename",
                       json={"exercise": "Nope", "new_name": "Something"}).status_code == 404


# ---------------------------------------------------------------------------
# Remap
# ---------------------------------------------------------------------------

def aliases_of(sh):
    return {r[0]: r[1] for r in sh.tabs[WS_EXERCISE_ALIASES].rows[1:]}


def test_permanent_remap_moves_past_logs(env):
    sh, state, client = env
    wrong = {"workout_action": "log", "entries": [
        {"exercise": "Lat Pulldown", "raw": "cable pulldown", "sets": [{"weight": 80, "reps": 10, "count": 3}]}]}
    chat(client, state, "cable pulldown 80x10x3", "ok" + directive(wrong))
    assert aliases_of(sh)["cable pulldown"] == "Lat Pulldown"

    remap = {"workout_action": "remap", "entries": [{"exercise": "Cable Pulldown", "raw": "cable pulldown"}]}
    done = chat(client, state, "cable pulldown isn't my lat pulldown, it's its own exercise",
                "Remapped, including past logs." + directive(remap))
    assert done["workout"]["remapped"] == [{"raw": "cable pulldown", "exercise": "Cable Pulldown"}]
    al = aliases_of(sh)
    assert al["cable pulldown"] == "Cable Pulldown"
    assert al["lat pulldown"] == "Lat Pulldown"       # untouched
    keys = [r[0] for r in sh.tabs[WS_EXERCISE_ALIASES].rows[1:]]
    assert keys.count("cable pulldown") == 1          # updated in place, no duplicate
    # The past row now resolves to the new exercise in the snapshot
    chat(client, state, "what's my PR on cable pulldown?", "80x10.")
    assert "Cable Pulldown" in state["prompt"]
    assert "PR 80×10" in state["prompt"]


def test_remap_flag_on_log_entry(env):
    sh, state, client = env
    chat(client, state, "cable pulldown 80x10x3", "ok" + directive({"workout_action": "log", "entries": [
        {"exercise": "Lat Pulldown", "raw": "cable pulldown", "sets": [{"weight": 80, "reps": 10}]}]}))
    chat(client, state, "cable pulldown 85x10, and that's NOT lat pulldown, always its own exercise",
         "ok" + directive({"workout_action": "log", "entries": [
             {"exercise": "Cable Pulldown", "raw": "cable pulldown", "remap": True,
              "sets": [{"weight": 85, "reps": 10}]}]}))
    assert sh.tabs[WS_WORKOUT_LOGS].rows[-1][3] == "Cable Pulldown"
    assert aliases_of(sh)["cable pulldown"] == "Cable Pulldown"


def test_without_remap_alias_still_wins(env):
    sh, state, client = env
    chat(client, state, "pulldown 100x8x3", "ok" + directive({"workout_action": "log", "entries": [
        {"exercise": "Lat Pulldown", "raw": "pulldown", "sets": [{"weight": 100, "reps": 8}]}]}))
    chat(client, state, "pulldown 110x8", "ok" + directive({"workout_action": "log", "entries": [
        {"exercise": "Something Else", "raw": "pulldown", "sets": [{"weight": 110, "reps": 8}]}]}))
    assert sh.tabs[WS_WORKOUT_LOGS].rows[-1][3] == "Lat Pulldown"


def test_one_off_variant_via_specific_raw(env):
    sh, state, client = env
    chat(client, state, "pulldown 100x8x3", "ok" + directive({"workout_action": "log", "entries": [
        {"exercise": "Lat Pulldown", "raw": "pulldown", "sets": [{"weight": 100, "reps": 8}]}]}))
    chat(client, state, "pulldown 80x10 but close grip today", "ok" + directive({"workout_action": "log", "entries": [
        {"exercise": "Close-Grip Pulldown", "raw": "close grip pulldown", "sets": [{"weight": 80, "reps": 10}]}]}))
    assert sh.tabs[WS_WORKOUT_LOGS].rows[-1][3] == "Close-Grip Pulldown"
    assert aliases_of(sh)["pulldown"] == "Lat Pulldown"
