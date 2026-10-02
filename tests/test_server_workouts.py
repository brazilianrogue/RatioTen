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

    def update(self, *_a, **_k):
        pass


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
