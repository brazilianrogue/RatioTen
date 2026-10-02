"""RatioTen – workout tracking logic.

Pure functions only (no sheet I/O) so everything here is unit-testable.
server.py owns reading/writing the Workout_Logs and Exercise_Aliases tabs and
calls into this module for normalisation, set collapsing, session assignment,
stats and prompt building.

Exercise identity is resolved at READ time: each row's Raw Input is reduced to
an alias key and looked up in the Exercise_Aliases tab, so re-pointing an alias
in the sheet regroups history retroactively.  The stored Exercise column is the
fallback when a row's alias key isn't in the table.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Optional

from constants import SESSION_GAP_HOURS, WORKING_WEIGHT_SESSIONS

TS_FMT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Alias normalisation
# ---------------------------------------------------------------------------

# A number plus any unit glued to it: "100", "40s", "2.5kg", "90 sec", "8x3"
_NUM_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:lbs?|kgs?|secs?|seconds?|mins?|minutes?|reps?|sets?|s|m)?\b",
    re.IGNORECASE,
)
# Filler words left behind once the numbers are gone
_FILLER_WORDS = {"x", "of", "for", "at", "reps", "rep", "sets", "set", "lb", "lbs",
                 "kg", "s", "sec", "secs", "seconds", "min", "mins", "minutes",
                 "each", "and", "with", "did", "just"}


def alias_key(raw: str) -> str:
    """Reduce a raw exercise phrase to a lookup key.

    "Lat pulldowns, 100 x 8 x 3" -> "lat pulldowns"
    "floor pushups 20x4"         -> "floor pushups"
    "dead hang 40s, 35s"         -> "dead hang"
    """
    text = str(raw or "").lower().replace("×", " x ")
    text = re.sub(r"(\d)\s*x\s*(?=\d)", r"\1 x ", text)   # "100x8x3" -> "100 x 8 x 3"
    text = _NUM_UNIT_RE.sub(" ", text)
    text = re.sub(r"[^a-z\s-]", " ", text)
    words = [w.strip("-") for w in text.split()]
    words = [w for w in words if w and w not in _FILLER_WORDS]
    return " ".join(words)


# ---------------------------------------------------------------------------
# Set handling
# ---------------------------------------------------------------------------

def _num(val) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _int(val) -> Optional[int]:
    f = _num(val)
    return int(round(f)) if f is not None else None


def _weight(val) -> Optional[float]:
    """Weight in lb.  NEGATIVE = machine assistance (assisted dip 50 → -50), so
    "bigger is better" holds for every stat: -45 beats -50.  Zero/blank → None."""
    if val is None or val == "":
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None


class IncompleteSetError(ValueError):
    """A set had a weight but neither reps nor a duration."""


def expand_sets(sets: list) -> list[tuple]:
    """Directive set dicts -> one (weight, reps, duration) tuple per performed set.

    Each dict may carry weight / reps / duration_sec and an optional count.
    Reps and duration are mutually exclusive; if both arrive, reps win.
    A set with neither reps nor duration raises IncompleteSetError — the coach
    is told to ask for reps instead, so this only fires on model error and
    must never be silently dropped.
    """
    out = []
    for s in sets or []:
        if not isinstance(s, dict):
            continue
        weight = _weight(s.get("weight"))
        reps = _int(s.get("reps"))
        duration = None if reps else _int(s.get("duration_sec"))
        if not reps and not duration:
            raise IncompleteSetError("set without reps or duration")
        count = _int(s.get("count")) or 1
        out.extend([(weight, reps, duration)] * count)
    return out


def collapse_sets(expanded: list[tuple]) -> list[tuple]:
    """Hybrid row grain: consecutive identical sets collapse into one row.

    [(100,8,None)]*3                -> [(100,8,None,3)]
    [(100,8,None),(110,6,None)]     -> [(100,8,None,1),(110,6,None,1)]
    """
    rows: list[list] = []
    for s in expanded:
        if rows and tuple(rows[-1][:3]) == s:
            rows[-1][3] += 1
        else:
            rows.append([*s, 1])
    return [tuple(r) for r in rows]


# ---------------------------------------------------------------------------
# Sessions & IDs
# ---------------------------------------------------------------------------

def assign_session(last_ts: Optional[datetime], last_session: str, now: datetime,
                   gap_hours: float = SESSION_GAP_HOURS) -> str:
    """Reuse the previous session if the last set was within the gap, else start one."""
    if last_ts is not None and last_session and (now - last_ts) <= timedelta(hours=gap_hours):
        return last_session
    return now.strftime("S-%Y%m%d-%H%M")


def new_entry_id(now: datetime) -> str:
    return now.strftime("E-%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------

def parse_rows(values: list[list]) -> list[dict]:
    """Workout_Logs get_all_values() output -> list of row dicts (header skipped).

    Each dict also carries `row` (1-based sheet row number) for delete/replace.
    """
    rows = []
    for i, r in enumerate(values[1:], start=2):
        r = list(r) + [""] * (9 - len(r))
        try:
            ts = datetime.strptime(str(r[0]).strip(), TS_FMT)
        except ValueError:
            continue
        rows.append({
            "row": i,
            "ts": ts,
            "session": str(r[1]).strip(),
            "entry": str(r[2]).strip(),
            "exercise": str(r[3]).strip(),
            "raw": str(r[4]).strip(),
            "weight": _weight(r[5]),
            "reps": _int(r[6]),
            "duration": _int(r[7]),
            "sets": _int(r[8]) or 1,
        })
    return rows


def parse_aliases(values: list[list]) -> dict[str, str]:
    """Exercise_Aliases get_all_values() -> {alias_key: canonical}.  Later rows win."""
    out = {}
    for r in values[1:]:
        if len(r) < 2:
            continue
        key = alias_key(r[0])
        canon = str(r[1]).strip()
        if key and canon:
            out[key] = canon
    return out


def resolve_exercise(row: dict, aliases: dict[str, str]) -> str:
    return aliases.get(alias_key(row.get("raw", "")), "") or row.get("exercise", "")


def resolve_canonical(raw: str, model_exercise: str, aliases: dict[str, str]) -> str:
    """At write time the alias table wins over the model's suggestion.

    A deliberate remap updates `aliases` BEFORE this runs (see remap_pairs), so
    the user's chat correction flows through the same path.
    """
    key = alias_key(raw)
    if key and key in aliases:
        return aliases[key]
    return str(model_exercise or "").strip() or key.title()


def canonical_names(rows: list[dict], aliases: dict[str, str]) -> set[str]:
    names = set(aliases.values())
    names.update(resolve_exercise(r, aliases) for r in rows)
    names.discard("")
    return names


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _fmt_num(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")


def _fmt_dur(sec: int) -> str:
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    return f"{m}m{s:02d}s" if s else f"{m}m"


def fmt_set(row: dict) -> str:
    """Compact set notation: 100×8 ×3 | 20 reps ×4 | 40s | 25 lb × 30s."""
    parts = []
    if row.get("weight") and row.get("reps"):
        body = f"{_fmt_num(row['weight'])}×{row['reps']}"
    elif row.get("reps"):
        body = f"{row['reps']} reps"
    elif row.get("weight") and row.get("duration"):
        body = f"{_fmt_num(row['weight'])} lb × {_fmt_dur(row['duration'])}"
    else:
        body = _fmt_dur(row.get("duration") or 0)
    parts.append(body)
    if row.get("sets", 1) > 1:
        parts.append(f"×{row['sets']}")
    return " ".join(parts)


def exercise_kind(rows: list[dict]) -> str:
    if any(r["weight"] for r in rows):
        return "weighted"
    if any(r["duration"] for r in rows):
        return "timed"
    return "bodyweight"


def _top_key(kind: str):
    if kind == "weighted":
        return lambda r: (r["weight"] or 0, r["reps"] or 0, r["duration"] or 0)
    if kind == "timed":
        return lambda r: (r["duration"] or 0,)
    return lambda r: (r["reps"] or 0,)


def _fmt_top(row: dict, kind: str) -> str:
    if kind == "weighted":
        if row["reps"]:
            return f"{_fmt_num(row['weight'])}×{row['reps']}"
        return f"{_fmt_num(row['weight'])} lb × {_fmt_dur(row['duration'] or 0)}"
    if kind == "timed":
        return _fmt_dur(row["duration"] or 0)
    return f"{row['reps']} reps"


def _group_sessions(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    """Rows for one exercise -> [(session_id, rows)] oldest first."""
    order: list[str] = []
    by: dict[str, list[dict]] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        sid = r["session"] or r["ts"].strftime("S-%Y%m%d")
        if sid not in by:
            by[sid] = []
            order.append(sid)
        by[sid].append(r)
    return [(sid, by[sid]) for sid in order]


def current_session_id(rows: list[dict], now: datetime,
                       gap_hours: float = SESSION_GAP_HOURS) -> Optional[str]:
    """Session that a set logged right now would join, if any."""
    if not rows:
        return None
    last = max(rows, key=lambda r: r["ts"])
    if (now - last["ts"]) <= timedelta(hours=gap_hours):
        return last["session"]
    return None


def exercise_stats(rows: list[dict], aliases: dict[str, str], now: datetime,
                   n_sessions: int = WORKING_WEIGHT_SESSIONS) -> dict[str, dict]:
    """Per-exercise stats keyed by canonical name.

    working  — avg top-set weight over the last n completed-or-current sessions
               (weighted exercises only; None for bodyweight/timed)
    pr       — best top set ever, with its date
    last     — most recent session BEFORE the current one (for "vs last time")
    today    — sets in the current session, if any
    """
    current = current_session_id(rows, now)
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(resolve_exercise(r, aliases), []).append(r)
    grouped.pop("", None)

    stats = {}
    for name, ex_rows in grouped.items():
        kind = exercise_kind(ex_rows)
        key = _top_key(kind)
        sessions = _group_sessions(ex_rows)
        tops = [(sid, max(srows, key=key), srows) for sid, srows in sessions]

        working = None
        if kind == "weighted":
            weights = [t["weight"] for _, t, _ in tops if t["weight"]][-n_sessions:]
            if weights:
                working = sum(weights) / len(weights)

        pr_sid, pr_row, _ = max(tops, key=lambda t: key(t[1]))
        prior = [t for t in tops if t[0] != current]
        last = prior[-1] if prior else None
        today = next((srows for sid, _, srows in tops if sid == current), [])

        stats[name] = {
            "kind": kind,
            "assisted": kind == "weighted" and all(r["weight"] < 0 for r in ex_rows if r["weight"]),
            "working": working,
            "pr": _fmt_top(pr_row, kind),
            "pr_date": pr_row["ts"].strftime("%Y-%m-%d"),
            "last_date": last[2][0]["ts"].strftime("%Y-%m-%d") if last else None,
            "last_sets": ", ".join(fmt_set(r) for r in last[2]) if last else None,
            "last_top": _fmt_top(last[1], kind) if last else None,
            "today_sets": ", ".join(fmt_set(r) for r in today) if today else None,
            "sessions": len(sessions),
            "last_ts": max(r["ts"] for r in ex_rows),
        }
    return stats


def build_snapshot(rows: list[dict], aliases: dict[str, str], now: datetime) -> str:
    """Compact per-exercise block injected into the prompt on workout turns."""
    stats = exercise_stats(rows, aliases, now)
    alias_by_canon: dict[str, list[str]] = {}
    for k, c in aliases.items():
        alias_by_canon.setdefault(c, []).append(k)

    if not stats and not aliases:
        return "No exercises logged yet — every exercise will be new."

    lines = []
    for name in sorted(stats, key=lambda n: stats[n]["last_ts"], reverse=True):
        s = stats[name]
        tag = {"weighted": "", "bodyweight": " (bodyweight)", "timed": " (timed)"}[s["kind"]]
        if s["assisted"]:
            tag = " (assisted: negative weight = lb of assistance; closer to 0 is better)"
        al = sorted(a for a in alias_by_canon.get(name, []) if a != name.lower())
        bits = [f"{name}{tag}" + (f" [aliases: {', '.join(al)}]" if al else "")]
        if s["working"] is not None:
            bits.append(f"working weight {_fmt_num(round(s['working'], 1))} lb")
        else:
            bits.append("working weight N/A")
        bits.append(f"PR {s['pr']} ({s['pr_date']})")
        if s["last_sets"]:
            bits.append(f"last session {s['last_date']}: {s['last_sets']} (top {s['last_top']})")
        else:
            bits.append("no prior session")
        if s["today_sets"]:
            bits.append(f"THIS SESSION: {s['today_sets']}")
        lines.append(" | ".join(bits))
    # Aliased names with no logged rows (e.g. after manual sheet edits)
    for canon in sorted(set(alias_by_canon) - set(stats)):
        lines.append(f"{canon} [aliases: {', '.join(sorted(alias_by_canon[canon]))}] | no sets logged")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

# STRONG signals: set notation, rep/time counts, stat questions. These win over
# food signals so a mixed message still reaches workout mode (case C → split).
_STRONG_WORKOUT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*[x×]\s*\d+"                       # 100 x 8, 8x3
    r"|\b\d+\s*(?:s|sec|secs|seconds)\b"                # 40s, 30 sec
    r"|\bsets?\s+of\b|\b\d+\s*reps?\b"                  # 3 sets of 12, 12 reps
    r"|\bworking\s+weight\b|\bprs?\b|\bpersonal\s+(?:best|record)\b",
    re.IGNORECASE,
)
# WEAK signals: general gym words. Food / end-of-workout signals override these
# ("post-workout meal", "done with my workout").
_WEAK_WORKOUT_RE = re.compile(
    r"\bworkouts?\b|\bexercis|\blift(?:ed|ing)?\b|\bgym\b|\btrain(?:ed|ing)\b",
    re.IGNORECASE,
)

# Cold-start vocabulary so "pushups 80" is recognised before any aliases exist.
# False positives are cheap: the prompt section tells the model to ignore it for food.
_COMMON_EXERCISE_RE = re.compile(
    r"\b(?:push[\s-]?ups?|pull[\s-]?ups?|chin[\s-]?ups?|sit[\s-]?ups?|squats?|deadlifts?"
    r"|bench(?:\s*press)?|overhead\s+press|pulldowns?|pull[\s-]?downs?|rows?\b(?=\s*\d)"
    r"|curls?|planks?|dead\s*hangs?|lunges?|dips?\b(?=\s*\d)|crunch(?:es)?|burpees?"
    r"|lateral\s+raises?|face\s+pulls?|tricep|bicep|hip\s+thrusts?|leg\s+press|kettlebell|dumbbell)\b",
    re.IGNORECASE,
)

# The user says the workout is over: "done with my workout", "workout's over",
# "finished at the gym", "leaving the gym".
_WORKOUT_OVER_RE = re.compile(
    r"\b(?:done|finished|wrapped(?:\s+up)?|over|through)\b[\w\s']{0,20}?\b(?:workout|gym|lifting|training|session)\b"
    r"|\b(?:workout|session|training|lifting)(?:'s|\s+is|\s+was)?\s+(?:done|over|finished|complete|wrapped)\b"
    r"|\bleaving\s+the\s+gym\b|\bthat'?s\s+a\s+wrap\b",
    re.IGNORECASE,
)
# Clear food intent: meal words, eating verbs, food units, macro questions.
_FOOD_RE = re.compile(
    r"\b(?:food\s+time|time\s+to\s+eat|time\s+for\s+(?:food|a\s+meal|lunch|dinner|breakfast|a\s+snack)"
    r"|break(?:ing)?\s+(?:my\s+|the\s+)?fast|post[\s-]?workout\s+(?:meal|shake|food|snack)"
    r"|breakfast|lunch|dinner|snack|meal|ate|eat|eating|drank|drinking"
    r"|protein|calories?|macros?|kcal)\b"
    r"|\b\d+(?:\.\d+)?\s*(?:oz|g|grams?|ml|cups?|tbsp|tsp|slices?|cal|kcal)\b",
    re.IGNORECASE,
)


def _mentions_alias(text: str, known_keys) -> bool:
    low = " " + re.sub(r"\s+", " ", re.sub(r"[^a-z\s-]", " ", str(text or "").lower())) + " "
    return any(k and f" {k} " in low for k in known_keys or ())


def strong_workout_signal(text: str, known_keys: set[str] | None = None) -> bool:
    t = str(text or "")
    return bool(_STRONG_WORKOUT_RE.search(t) or _COMMON_EXERCISE_RE.search(t)
                or _mentions_alias(t, known_keys))


def food_or_done_signal(text: str) -> bool:
    t = str(text or "")
    return bool(_WORKOUT_OVER_RE.search(t) or _FOOD_RE.search(t))


def looks_like_workout(text: str, known_keys: set[str] | None = None) -> bool:
    """Context-free check (no conversation state)."""
    if strong_workout_signal(text, known_keys):
        return True
    return bool(_WEAK_WORKOUT_RE.search(str(text or ""))) and not food_or_done_signal(text)


def is_workout_turn(text: str, known_keys: set[str] | None, prev_coach_mode: str,
                    prev_coach_ts: Optional[datetime], now: datetime,
                    gap_hours: float = SESSION_GAP_HOURS) -> bool:
    """Decide whether this message gets the workout prompt section.

    Priority:
      1. Strong workout signal (sets, reps, exercise names, stat questions) → yes.
         A mixed food+exercise message lands here on purpose (case C: split it).
      2. Clear food intent or "workout's over" → no.
      3. Weak gym words ("workout", "gym") → yes.
      4. Follow-up: the coach's LAST reply was a workout turn (logged a set,
         asked a name question, answered a workout question) within the
         session gap → yes. Covers "yes", "fix that", long corrections.
    """
    if strong_workout_signal(text, known_keys):
        return True
    if food_or_done_signal(text):
        return False
    if _WEAK_WORKOUT_RE.search(str(text or "")):
        return True
    return (prev_coach_mode == "workout" and prev_coach_ts is not None
            and (now - prev_coach_ts) <= timedelta(hours=gap_hours))


# ---------------------------------------------------------------------------
# Directive parsing
# ---------------------------------------------------------------------------

_FENCED_OBJ_RE = re.compile(r"```[a-zA-Z]*\s*(\{[\s\S]*?\})\s*```")
_VALID_ACTIONS = {"log", "replace_last", "delete_last", "remap"}


def parse_workout_directive(raw: str) -> Optional[dict]:
    """Find the {"workout_action": ...} object in the model response.

    Returns {"action": str, "entries": [{"exercise", "raw", "sets", "remap"}]},
    or {"error": str} when a workout block is present but unusable (so the
    caller can tell the user nothing was saved), or None when there's no block.
    A "remap" action only re-points aliases, so its entries carry no sets.
    """
    candidates = [m.group(1) for m in _FENCED_OBJ_RE.finditer(raw or "")]
    # Unfenced fallback: a bare object on its own lines
    candidates += re.findall(r"(?m)^\s*(\{[^\n]*\"workout_action\"[\s\S]*?\})\s*$", raw or "")
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if not isinstance(obj, dict) or "workout_action" not in obj:
            continue
        action = str(obj.get("workout_action", "")).strip().lower()
        if action not in _VALID_ACTIONS:
            return {"error": f"unknown workout action '{action}'"}
        entries = []
        for e in obj.get("entries") or []:
            if not isinstance(e, dict):
                continue
            exercise = str(e.get("exercise", "")).strip()
            raw_phrase = str(e.get("raw", "") or exercise).strip()
            if action == "remap":
                if exercise and alias_key(raw_phrase):
                    entries.append({"exercise": exercise, "raw": raw_phrase, "sets": [], "remap": True})
                continue
            try:
                exp = expand_sets(e.get("sets") or [])
            except IncompleteSetError:
                return {"error": "a set was missing its reps (or time)"}
            if not exp:
                continue
            entries.append({
                "exercise": exercise,
                "raw": raw_phrase,
                "sets": exp,
                "remap": bool(e.get("remap")) and bool(exercise),
            })
        if action != "delete_last" and not entries:
            return {"error": "no valid sets were found"}
        return {"action": action, "entries": entries}
    if "workout_action" in (raw or ""):
        return {"error": "the workout data couldn't be read"}
    return None


def remap_pairs(entries: list[dict], aliases: dict[str, str]) -> dict[str, str]:
    """{alias_key: new canonical} for remap-flagged entries whose mapping changes.

    Remapping is retroactive by design: every past row typed with that phrase
    resolves to the new exercise at read time.
    """
    out = {}
    for e in entries:
        if not e.get("remap"):
            continue
        key = alias_key(e["raw"])
        if key and aliases.get(key) != e["exercise"]:
            out[key] = e["exercise"]
    return out


def build_rows(entries: list[dict], aliases: dict[str, str], ts: str,
               session: str, entry_id: str) -> tuple[list[list], list[dict]]:
    """Directive entries -> (sheet rows, per-entry summary).

    The summary carries the resolved canonical name so the caller can add any
    new alias rows and report what was logged.
    """
    sheet_rows, summary = [], []
    for e in entries:
        canon = resolve_canonical(e["raw"], e["exercise"], aliases)
        for weight, reps, duration, count in collapse_sets(e["sets"]):
            sheet_rows.append([
                ts, session, entry_id, canon, e["raw"],
                (int(weight) if float(weight).is_integer() else weight) if weight else "",
                reps or "", duration or "", count,
            ])
        summary.append({"exercise": canon, "raw": e["raw"], "sets": len(e["sets"])})
    return sheet_rows, summary


def new_alias_rows(summary: list[dict], aliases: dict[str, str], today: str) -> list[list]:
    """Alias rows to append so both the raw phrase and the canonical name map."""
    out, seen = [], set(aliases)
    for s in summary:
        for k in (alias_key(s["raw"]), alias_key(s["exercise"])):
            if k and k not in seen:
                out.append([k, s["exercise"], today])
                seen.add(k)
    return out


# ---------------------------------------------------------------------------
# Prompt section
# ---------------------------------------------------------------------------

def build_workout_prompt(snapshot: str, known_names: list[str]) -> str:
    names = ", ".join(sorted(known_names)) or "(none yet)"
    return f"""

### WORKOUT MODE (this turn may be workout-related — read carefully)
This message looks like it may be about exercise. Decide which case applies:

A) FOOD log or food question → IGNORE this entire WORKOUT MODE section and respond with the normal food rules above.

B) WORKOUT log, workout correction/deletion, an answer to your earlier exercise-name question, or a workout question →
   follow the rules below and OVERRIDE the food response format: NO food item table, NO Cals/Protein/Density totals line,
   NO food-log JSON array this turn.

C) The message contains BOTH food and exercise → log NOTHING (no JSON of either kind). Briefly ask the user to send the
   food and the workout as separate messages.

If the user says the workout is over ("done with my workout", "leaving the gym") or that it's food time
("time to eat", "breaking my fast"), the workout is finished: respond to that briefly and treat what follows as food.

#### Known exercises (canonical names): {names}

#### WORKOUT HISTORY (server-computed — quote these numbers exactly; never calculate stats yourself)
{snapshot}

#### Parsing rules
- Notation is always weight x reps x sets. "lat pulldowns 100 x 8 x 3" = 3 sets of 8 reps at 100 lb.
- "8 x 3" with no weight = 3 sets of 8 reps, bodyweight. A bare number ("pushups 80") = ONE set of that many reps.
- Times: "dead hang 40s, 35s, 30s" = three timed sets. Convert minutes to seconds (2 min = 120).
- Weights are always lbs. Bodyweight exercises have no weight — omit the weight field.
- A set has reps OR duration_sec, never both.
- SLASH LISTS ("50/45/50") = one value PER SET, in order — never a sum, never a single huge set.
  Decide from the exercise type and WORKOUT HISTORY whether the values are weights or reps:
    • bodyweight exercise ("pushups 20/15/12") → reps per set: 20, 15, 12.
    • weighted or assisted exercise whose values sit in its usual weight range ("assisted dips 50/45/50") →
      weight per set.
    • per-set pairs ("bench 135x8/145x6/155x4") → weight×reps per set.
    • "50/45/50 x 8" → those weights, 8 reps each.
  If you genuinely can't tell whether the numbers are weights or reps → ask, and emit no JSON.
- EVERY set needs reps or a time. If the user gave weights but no reps ("assisted dips 50/45/50"), do NOT log:
  restate what you understood and ask for the reps ("Got 3 sets at 50/45/50 lb assist — how many reps each?").
  When they answer, log the complete sets. Never guess reps and never send a set without reps/duration.
- ASSISTED machines (assisted dip, assisted pull-up): weight = NEGATIVE assistance — "50 lb assist" → "weight": -50.
  The canonical name starts with "Assisted" (Assisted Dip, Assisted Pull-up) — a separate exercise from the
  unassisted one. In replies always say "50 lb assist", never "-50". Less assistance (closer to 0) = progress;
  the PR is the LEAST assistance.
- Real-time only: everything is logged as happening now.

#### Exercise names
- Canonical names are Title Case, singular: "Lat Pulldown", "Push-up", "Dead Hang", "Incline Dumbbell Press".
- If the user's phrasing clearly matches a known exercise or alias (wording differences only, e.g. "pushups" vs
  "floor pushups"), use that canonical name.
- Different equipment (dumbbell vs barbell), angle (incline vs flat) or grip (wide vs close) = DIFFERENT exercises.
- If it PLAUSIBLY matches a known exercise but you are NOT sure → do NOT log. Ask one short question
  ("Is 'cable pulldown' your Lat Pulldown, or a new exercise?") and emit no JSON. When the user answers, log it then
  using the numbers from their earlier message, with "raw" set to their ORIGINAL phrase.
- If it matches nothing known → log it immediately as a new exercise and say so: "New exercise: Face Pull".
- The server maps "raw" through the alias list, so the alias list wins over the "exercise" name you send. Two ways to
  change what gets saved:
  1) ONE-OFF VARIANT — the user says a known phrase means a different exercise just this time
     ("pulldown" is normally Lat Pulldown, but today it was close-grip): set "raw" to the specific phrase that
     distinguishes it ("close grip pulldown") and "exercise" to the variant's name. Do NOT set remap.
  2) PERMANENT REMAP — the user says a mapping is WRONG and that phrase should ALWAYS mean another exercise
     ("cable pulldown isn't my lat pulldown, it's its own exercise"): add "remap": true to the entry, with "raw" =
     that phrase and "exercise" = the correct name. If there are no new sets to log, use workout_action "remap"
     with entries [{{"exercise": "Cable Pulldown", "raw": "cable pulldown"}}]. Remapping also moves every PAST log
     typed with that phrase — tell the user that in your reply ("Remapped 'cable pulldown' → Cable Pulldown,
     including past logs."). Only remap on an explicit user correction, never on your own initiative.

#### Response style for a logged set
- One short acknowledgement of what was logged, then compare to the LAST SESSION numbers above
  (e.g. "Up 10 lb from last time 💪", "Same as last session — 100×8"). If there is no prior session, say it's the first one.
- If the new top set beats the PR listed above, celebrate it as a new PR 🏆.
- 1–3 sentences total. No tables, no headers.

#### Answering questions
- "Working weight" = the working weight value above (avg top set, last 3 sessions). For bodyweight or timed
  exercises it does not apply — say so, and offer their PR / last session instead. For assisted exercises phrase
  it as assistance ("working assistance ≈ 48 lb").
- PRs and "what did I do last time" come straight from the history above. If an exercise isn't listed, say there's no data.

#### Corrections
- "That was actually 110" / "fix that" → workout_action "replace_last" with the FULL corrected entry (all sets).
- "Scratch that" / "delete that" → workout_action "delete_last" with an empty entries list.
- Only the most recent workout message can be fixed or deleted.

#### JSON directive (append at the very end of the response, ONLY when logging/fixing/deleting)
```json
{{"workout_action": "log", "entries": [{{"exercise": "Lat Pulldown", "raw": "lat pulldowns", "sets": [{{"weight": 100, "reps": 8, "count": 3}}]}}]}}
```
- workout_action: "log" | "replace_last" | "delete_last" | "remap".
- Optional per-entry "remap": true — ONLY for a permanent remap the user explicitly asked for (see Exercise names).
- One entry per exercise in the message. "raw" = the user's exercise phrase without the numbers.
- sets: list of {{"weight"?, "reps"?, "duration_sec"?, "count"?}}. Use count for repeated identical sets;
  list differing sets separately: [{{"weight":100,"reps":8}}, {{"weight":110,"reps":6}}].
- Timed example: {{"exercise": "Dead Hang", "raw": "dead hang", "sets": [{{"duration_sec": 40}}, {{"duration_sec": 35}}]}}
- Assisted example ("assisted dips 50/45/50 x 8"): {{"exercise": "Assisted Dip", "raw": "assisted dips",
  "sets": [{{"weight": -50, "reps": 8}}, {{"weight": -45, "reps": 8}}, {{"weight": -50, "reps": 8}}]}}
- Never emit this block for questions, or while waiting for an exercise-name confirmation.
"""
