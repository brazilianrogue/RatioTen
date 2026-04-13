"""RatioTen – FastAPI backend.

Replaces Streamlit entirely.  All business logic (scoring, sheets, persona)
is unchanged — only the serving layer is new.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import gspread
import pandas as pd
from fastapi import Body, FastAPI, Form, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google import genai

import persona
from constants import (
    DEFAULT_MODE,
    DEFAULT_USER,
    EASTERN,
    MODE_BULK,
    MODE_CUT,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    STABLE_MODEL,
    USER_CONFIGS,
    WS_CHAT_HISTORY,
    WS_WEIGHT_LOGS,
    WS_FASTING_SCHEDULE,
    WS_USER_GOALS,
    WS_CUSTOM_INSTRUCTIONS,
)
from scoring import calculate_plan_effectiveness, sync_plan_effectiveness_logs

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App & static files
# ---------------------------------------------------------------------------
app = FastAPI(title="RatioTen")

STATIC_DIR = Path("static")
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------------------------------------------------------------------------
# Simple TTL in-memory cache  (replaces @st.cache_data)
# ---------------------------------------------------------------------------
_cache: dict[str, tuple] = {}  # key → (value, expires_at)


def _cached(key: str, ttl: int, fn, empty_ttl: int = 0):
    """Cache fn() for ttl seconds.  If the result is falsy and empty_ttl > 0,
    use empty_ttl instead so transient failures retry quickly."""
    entry = _cache.get(key)
    if entry and entry[1] > time.time():
        return entry[0]
    val = fn()
    actual_ttl = (empty_ttl if (empty_ttl and not val) else ttl)
    _cache[key] = (val, time.time() + actual_ttl)
    return val


def _invalidate(key: str):
    _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Shared clients (created once at startup)
# ---------------------------------------------------------------------------
_gc: gspread.Client | None = None
_sh_cache: dict[str, gspread.Spreadsheet] = {}  # user_id → Spreadsheet
_gemini: genai.Client | None = None


def _get_sh(user_id: str = DEFAULT_USER) -> gspread.Spreadsheet:
    global _gc, _sh_cache
    if user_id not in _sh_cache:
        if _gc is None:
            creds = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
            _gc = gspread.service_account_from_dict(creds)
        spreadsheet_name = USER_CONFIGS.get(user_id, USER_CONFIGS[DEFAULT_USER])["spreadsheet"]
        _sh_cache[user_id] = _gc.open(spreadsheet_name)
    return _sh_cache[user_id]


def _get_gemini():
    global _gemini
    if _gemini is None:
        _gemini = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _gemini


# ---------------------------------------------------------------------------
# Sheet helpers  (ported 1-to-1 from app.py, st.cache_data → _cached)
# ---------------------------------------------------------------------------

DEFAULT_SCHEDULE = {
    "Monday":    {"start": None,    "end": None},
    "Tuesday":   {"start": "12:00", "end": "18:00"},
    "Wednesday": {"start": "12:00", "end": "18:00"},
    "Thursday":  {"start": "12:00", "end": "18:00"},
    "Friday":    {"start": "18:00", "end": "19:00"},
    "Saturday":  {"start": "12:00", "end": "18:00"},
    "Sunday":    {"start": "12:00", "end": "18:00"},
}
DEFAULT_GOALS = {"calories": 1500, "protein": 150, "mode": DEFAULT_MODE}


def _read_fasting_schedule(user_id: str = DEFAULT_USER) -> dict:
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_FASTING_SCHEDULE)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_FASTING_SCHEDULE, rows="10", cols="3")
            ws.append_row(["DayOfWeek", "WindowStart", "WindowEnd"])
            for day, t in DEFAULT_SCHEDULE.items():
                ws.append_row([day, t["start"] or "Skip", t["end"] or "Skip"])
            return DEFAULT_SCHEDULE
        data = ws.get_all_records()
        if not data:
            return DEFAULT_SCHEDULE
        sched: dict = {}
        for row in data:
            day = row.get("DayOfWeek")
            start = str(row.get("WindowStart", "")).strip()
            end = str(row.get("WindowEnd", "")).strip()
            if start.lower() in ["skip", "none", ""]:
                start = None
            if end.lower() in ["skip", "none", ""]:
                end = None
            sched[day] = {"start": start, "end": end}
        return sched
    except Exception:
        return DEFAULT_SCHEDULE


def _read_user_goals(user_id: str = DEFAULT_USER) -> dict:
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_USER_GOALS)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_USER_GOALS, rows="10", cols="2")
            ws.append_row(["Metric", "Value"])
            ws.append_row(["Calories", DEFAULT_GOALS["calories"]])
            ws.append_row(["Protein", DEFAULT_GOALS["protein"]])
            return DEFAULT_GOALS.copy()
        data = ws.get_all_records()
        if not data:
            return DEFAULT_GOALS.copy()
        goals = DEFAULT_GOALS.copy()
        for row in data:
            m = str(row.get("Metric", "")).strip().lower()
            v = row.get("Value", 0)
            if m == "calories":
                goals["calories"] = int(v)
            elif m == "protein":
                goals["protein"] = int(v)
            elif m == "mode":
                goals["mode"] = str(v).strip().lower() if v else DEFAULT_MODE
        # Validate mode value
        if goals["mode"] not in (MODE_CUT, MODE_BULK):
            goals["mode"] = DEFAULT_MODE
        return goals
    except Exception:
        return DEFAULT_GOALS.copy()


def _read_custom_instructions(user_id: str = DEFAULT_USER) -> str:
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_CUSTOM_INSTRUCTIONS)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_CUSTOM_INSTRUCTIONS, rows="100", cols="2")
            ws.append_row(["Label", "Instructions"])
            return ""
        data = ws.get_all_values()
        if len(data) <= 1:
            return ""
        parts = []
        for row in data[1:]:
            if len(row) >= 2 and str(row[1]).strip():
                parts.append(f"### {row[0]}\n{row[1]}")
        return "\n\n".join(parts)
    except Exception:
        return ""


def _read_lowest_weight(user_id: str = DEFAULT_USER) -> Optional[float]:
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_WEIGHT_LOGS)
        except gspread.WorksheetNotFound:
            return None
        data = ws.get_all_records()
        if not data:
            return None
        weights = [float(r.get("Weight (lbs)", 999)) for r in data if r.get("Weight (lbs)")]
        return min(weights) if weights else None
    except Exception:
        return None


def _parse_weight_date(val: str) -> Optional[str]:
    """Parse a weight log timestamp into a YYYY-MM-DD string, or None on failure."""
    from datetime import datetime as _dt
    s = str(val).strip()
    for fmt in (
        "%m/%d/%Y %H:%M:%S",   # Google Sheets default: 3/18/2026 14:30:00
        "%m/%d/%Y %I:%M:%S %p",# 12-hour with AM/PM:   3/18/2026 2:30:00 PM
        "%m/%d/%Y %H:%M",      # without seconds:      3/18/2026 14:30
        "%m/%d/%Y",            # date only
        "%Y-%m-%d %H:%M:%S",   # ISO with seconds
        "%Y-%m-%dT%H:%M:%S",   # ISO with T separator
        "%Y-%m-%d",            # ISO date only
    ):
        try:
            return _dt.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _read_weight_history(user_id: str = DEFAULT_USER) -> list:
    """Returns up to 30 daily weight entries (one per day, lowest reading), oldest first."""
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_WEIGHT_LOGS)
        except gspread.WorksheetNotFound:
            return []
        data = ws.get_all_records()
        if not data:
            log.warning("_read_weight_history: worksheet '%s' is empty", WS_WEIGHT_LOGS)
            return []
        log.info("_read_weight_history: %d rows, columns: %s", len(data), list(data[0].keys()))
        daily: dict = {}  # date_str -> lowest weight seen that day
        skipped_ts, skipped_col = 0, 0
        first_bad_ts = None
        for r in data:
            ts_val     = r.get("Timestamp") or r.get("Date") or r.get("date") or ""
            weight_val = r.get("Weight (lbs)")
            if not ts_val or not weight_val:
                skipped_col += 1
                continue
            try:
                weight   = float(weight_val)
                date_key = _parse_weight_date(ts_val)
                if not date_key:
                    if first_bad_ts is None:
                        first_bad_ts = ts_val
                    skipped_ts += 1
                    continue
                if date_key not in daily or weight < daily[date_key]:
                    daily[date_key] = weight
            except (ValueError, TypeError):
                skipped_col += 1
        if skipped_ts:
            log.warning("_read_weight_history: %d rows skipped — timestamp unparseable (first: %r)",
                        skipped_ts, first_bad_ts)
        if skipped_col:
            log.warning("_read_weight_history: %d rows skipped — missing/invalid column data", skipped_col)
        log.info("_read_weight_history: parsed %d unique days from %d rows", len(daily), len(data))
        entries = sorted(
            [{"date": d, "weight": w} for d, w in daily.items()],
            key=lambda x: x["date"],
        )
        return entries[-30:]
    except Exception as e:
        log.warning("_read_weight_history failed: %s", e)
        return []


def _read_today_logs(user_id: str = DEFAULT_USER) -> list:
    try:
        sh = _get_sh(user_id)
        ws = sh.sheet1
        values = ws.get_all_values()
        if len(values) <= 1:
            return []
        now = datetime.now(EASTERN)
        today_key = now.strftime("%Y-%m-%d")
        result = []
        for row in values[1:]:
            if not row or len(row) < 2:
                continue
            try:
                ts = datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")
                if ts.strftime("%Y-%m-%d") != today_key:
                    continue
                result.append({
                    "timestamp": ts,
                    "item":      str(row[1]),
                    "calories":  int(float(row[2])) if len(row) > 2 and row[2] else 0,
                    "protein":   int(float(row[3])) if len(row) > 3 and row[3] else 0,
                    "density":   str(row[4]).strip() if len(row) > 4 and row[4] else "0.0%",
                    "emoji":     str(row[6]).strip() if len(row) > 6 and row[6] else "🍽️",
                })
            except Exception:
                continue
        return result
    except Exception:
        return []


def _read_trailing_7_days(user_id: str = DEFAULT_USER) -> list[dict]:
    """Returns a list of {date, calories, protein, density} dicts, newest first."""
    try:
        sh = _get_sh(user_id)
        ws = sh.sheet1
        values = ws.get_all_values()
        if len(values) <= 1:
            return []
        cutoff = (datetime.now(EASTERN) - timedelta(days=7)).date()
        rows_by_date: dict = {}
        for row in values[1:]:
            if len(row) < 3:
                continue
            try:
                dt = datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S").date()
                if dt < cutoff:
                    continue
                key = dt.strftime("%Y-%m-%d")
                if key not in rows_by_date:
                    rows_by_date[key] = {"calories": 0, "protein": 0}
                rows_by_date[key]["calories"] += int(float(row[2])) if row[2] else 0
                rows_by_date[key]["protein"]  += int(float(row[3])) if len(row) > 3 and row[3] else 0
            except Exception:
                continue
        result = []
        for date_str, totals in sorted(rows_by_date.items(), reverse=True):
            cal = totals["calories"]
            prot = totals["protein"]
            density = f"{(prot / cal * 100):.1f}%" if cal else "0.0%"
            result.append({"date": date_str, "calories": cal, "protein": prot, "density": density})
        return result
    except Exception:
        return []


def _read_logs_history(days: int = 10, user_id: str = DEFAULT_USER) -> dict:
    """Returns {date_str: [log_entry, ...]} for the past `days` days (excluding today)."""
    try:
        sh = _get_sh(user_id)
        ws = sh.sheet1
        values = ws.get_all_values()
        if len(values) <= 1:
            return {}
        now = datetime.now(EASTERN)
        today_key = now.strftime("%Y-%m-%d")
        cutoff = (now - timedelta(days=days)).date()
        result: dict = {}
        for row in values[1:]:
            if len(row) < 2:
                continue
            try:
                ts = datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")
                dt = ts.date()
                if dt < cutoff:
                    continue
                key = dt.strftime("%Y-%m-%d")
                if key == today_key:
                    continue
                if key not in result:
                    result[key] = []
                result[key].append({
                    "timestamp": ts.strftime("%H:%M"),
                    "item":      str(row[1]),
                    "calories":  int(float(row[2])) if len(row) > 2 and row[2] else 0,
                    "protein":   int(float(row[3])) if len(row) > 3 and row[3] else 0,
                    "density":   str(row[4]).strip() if len(row) > 4 and row[4] else "0.0%",
                    "emoji":     str(row[6]).strip() if len(row) > 6 and row[6] else "🍽️",
                })
            except Exception:
                continue
        return result
    except Exception:
        return {}


def _read_wow(user_id: str = DEFAULT_USER) -> list[dict]:
    """Week-over-week averages."""
    try:
        sh = _get_sh(user_id)
        ws = sh.sheet1
        values = ws.get_all_values()
        if len(values) <= 1:
            return []
        headers = values[0]
        col_map = {h: i for i, h in enumerate(headers)}
        by_week: dict = {}
        for row in values[1:]:
            try:
                dt = pd.to_datetime(str(row[0]))
                cals = float(row[col_map.get("Calories", 2)] or 0)
                prot = float(row[col_map.get("Protein", 3)] or 0)
                if "Week Num" in col_map and len(row) > col_map["Week Num"]:
                    wn = str(row[col_map["Week Num"]])
                else:
                    y, w, _ = dt.isocalendar()
                    wn = f"{y}-W{w:02d}"
                if wn not in by_week:
                    by_week[wn] = {}
                day_key = dt.strftime("%Y-%m-%d")
                if day_key not in by_week[wn]:
                    by_week[wn][day_key] = {"cals": 0.0, "prot": 0.0}
                by_week[wn][day_key]["cals"] += cals
                by_week[wn][day_key]["prot"] += prot
            except Exception:
                continue
        result = []
        for wn in sorted(by_week.keys()):
            days_data = by_week[wn].values()
            avg_cal  = sum(d["cals"] for d in days_data) / len(days_data)
            avg_prot = sum(d["prot"] for d in days_data) / len(days_data)
            density  = f"{(avg_prot / avg_cal * 100):.1f}%" if avg_cal else "0.0%"
            result.append({"week": wn, "avg_calories": round(avg_cal), "avg_protein": round(avg_prot), "density": density})
        return result
    except Exception:
        return []


def _read_persistent_chat(user_id: str = DEFAULT_USER) -> list[dict]:
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_CHAT_HISTORY)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_CHAT_HISTORY, rows="1000", cols="3")
            ws.append_row(["Timestamp", "Role", "Parts"])
            return []
        data = ws.get_all_values()
        if len(data) <= 1:
            return []
        history = []
        for row in data[1:][-30:]:
            if len(row) < 3:
                continue
            try:
                parts = json.loads(row[2])
                content = [p.get("text", "") for p in parts]
                history.append({"role": str(row[1]), "content": content, "timestamp": str(row[0])})
            except Exception:
                continue
        return history
    except Exception:
        return []


def _log_chat_to_sheet(role: str, content, user_id: str = DEFAULT_USER):
    try:
        sh = _get_sh(user_id)
        try:
            ws = sh.worksheet(WS_CHAT_HISTORY)
        except Exception:
            return
        if isinstance(content, str):
            parts = [{"text": content}]
        else:
            parts = []
            for item in content:
                parts.append({"text": str(item) if not isinstance(item, bytes) else "📷 *Photo attached*"})
        ts = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
        ws.append_row([ts, role, json.dumps(parts)])
        _invalidate(f"chat_history_{user_id}")
    except Exception:
        pass


def _log_to_sheet(item: str, calories: int, protein: int, density: str, emoji: str = "🍽️", user_id: str = DEFAULT_USER, logged_at: str | None = None) -> bool:
    """Write a meal entry to the primary sheet.

    Parameters
    ----------
    logged_at:
        Optional "HH:MM" string for retroactive same-day logging.
        Must be earlier than the current time; silently falls back to
        now() if the value is missing, unparseable, or in the future.
    """
    try:
        sh = _get_sh(user_id)
        ws = sh.sheet1
        now = datetime.now(EASTERN)

        # Resolve the log timestamp — retroactive if valid, current otherwise
        log_dt = now
        if logged_at:
            try:
                t = datetime.strptime(logged_at, "%H:%M").time()
                candidate = datetime.combine(now.date(), t, tzinfo=EASTERN)
                if candidate < now:          # must be earlier today
                    log_dt = candidate
                else:
                    log.warning("_log_to_sheet: logged_at %r is in the future — using now()", logged_at)
            except ValueError:
                log.warning("_log_to_sheet: could not parse logged_at %r — using now()", logged_at)

        ts = log_dt.strftime("%Y-%m-%d %H:%M:%S")
        y, w, _ = log_dt.isocalendar()
        week_num = f"{y}-W{w:02d}"
        mode = _read_user_goals(user_id).get("mode", DEFAULT_MODE)
        ws.append_row([ts, item, calories, protein, density, week_num, emoji, mode])
        _invalidate(f"today_logs_{user_id}")
        _invalidate(f"trailing_7_{user_id}")
        return True
    except Exception as e:
        log.error("Failed to log meal: %s", e)
        return False


def _replace_log_entry(
    replaces_item: str,
    new_item: str,
    calories: int,
    protein: int,
    density: str,
    emoji: str,
    user_id: str = DEFAULT_USER,
) -> dict | None:
    """Overwrite the most recent today-row whose item matches *replaces_item*.

    Returns the old entry dict on success, or None if no match found
    (caller should fall back to a normal append).
    """
    try:
        sh = _get_sh(user_id)
        ws = sh.sheet1
        values = ws.get_all_values()
        if len(values) <= 1:
            return None
        now = datetime.now(EASTERN)
        today_key = now.strftime("%Y-%m-%d")
        target = replaces_item.strip().lower()
        match_idx = None   # index into values list (0 = header)
        old_entry = None
        for i, row in enumerate(values):
            if i == 0:
                continue  # skip header
            if not row or len(row) < 2:
                continue
            try:
                ts = datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S")
                if ts.strftime("%Y-%m-%d") != today_key:
                    continue
                if str(row[1]).strip().lower() == target:
                    match_idx = i
                    old_entry = {
                        "item":     str(row[1]),
                        "calories": int(float(row[2])) if len(row) > 2 and row[2] else 0,
                        "protein":  int(float(row[3])) if len(row) > 3 and row[3] else 0,
                        "density":  str(row[4]).strip() if len(row) > 4 and row[4] else "0.0%",
                    }
            except Exception:
                continue
        if match_idx is None:
            return None
        orig = values[match_idx]
        ts_str   = orig[0]
        week_num = orig[5] if len(orig) > 5 else ""
        mode     = orig[7] if len(orig) > 7 else DEFAULT_MODE
        sheet_row = match_idx + 1  # gspread rows are 1-based
        ws.update(f"A{sheet_row}:H{sheet_row}", [[ts_str, new_item, calories, protein, density, week_num, emoji, mode]])
        _invalidate(f"today_logs_{user_id}")
        _invalidate(f"trailing_7_{user_id}")
        return old_entry
    except Exception as e:
        log.error("_replace_log_entry failed: %s", e)
        return None


def get_fasting_status(schedule: dict) -> tuple[str, Optional[float]]:
    now = datetime.now(EASTERN)
    day_name = now.strftime("%A")
    today = schedule.get(day_name, {"start": None, "end": None})
    if today["start"] and today["end"]:
        try:
            start_t = datetime.strptime(today["start"], "%H:%M").time()
            end_t   = datetime.strptime(today["end"],   "%H:%M").time()
            if start_t <= now.time() < end_t:
                end_dt = datetime.combine(now.date(), end_t, tzinfo=EASTERN)
                return "Eating Window Active", end_dt.timestamp() * 1000
        except Exception:
            pass
    for i in range(8):
        check = now + timedelta(days=i)
        sched = schedule.get(check.strftime("%A"), {"start": None, "end": None})
        if sched["start"]:
            try:
                st_t = datetime.strptime(sched["start"], "%H:%M").time()
                st_dt = datetime.combine(check.date(), st_t, tzinfo=EASTERN)
                if st_dt > now:
                    return "Fasting Active", st_dt.timestamp() * 1000
            except Exception:
                continue
    return "No Schedule", None


# ---------------------------------------------------------------------------
# System prompt builder  (ported from app.py)
# ---------------------------------------------------------------------------

_CLOSING_PHRASES = (
    "ending the day",
    "ending my day",
    "kitchen closed",
    "all done logging",
    "done logging",
    "last log",
    "last meal",
    "last item",
    "that's it for today",
    "thats it for today",
    "wrapping up",
    "closing out",
    "closing the day",
)

def build_system_prompt(
    schedule: dict,
    goals: dict,
    custom_instructions: str = "",
    today_stats: Optional[dict] = None,
    today_logs: Optional[list] = None,
    weekly_summary: Optional[list] = None,
    user_message: str = "",
    mode: str = DEFAULT_MODE,
    user_id: str = DEFAULT_USER,
) -> str:
    now = datetime.now(EASTERN)
    formatted_schedule = "\n".join([
        f"- {day}: {t['start']} to {t['end']}" if t["start"] else f"- {day}: Fasting / Skip"
        for day, t in schedule.items()
    ])

    time_awareness = f"""
### CURRENT TIME AWARENESS:
- **Today is:** {now.strftime("%A")}, {now.strftime("%Y-%m-%d")}
- **Current Time:** {now.strftime("%I:%M %p")} (Eastern Time)
"""

    is_bulk = mode == MODE_BULK
    cal_label = "Surplus Target" if is_bulk else "Lid"
    cal_remaining_label = "Surplus Remaining" if is_bulk else "Remaining Calorie Room"

    stats_context = ""
    if today_stats:
        stats_context = f"""
### CURRENT DAY SITUATION REPORT:
- **Calories Ingested:** {today_stats['cals']} / {goals['calories']} ({cal_label})
- **Protein Ingested:** {today_stats['protein']}g / {goals['protein']}g (Floor)
- **Current Density:** {today_stats['density']}
- **{cal_remaining_label}:** {max(0, goals['calories'] - today_stats['cals'])}
- **Remaining Protein Needed:** {max(0, goals['protein'] - today_stats['protein'])}g
"""

    if today_logs:
        rows = "\n".join(
            f"| {l['item']} ({l['emoji']}) | {l.get('calories', 0)} | {l.get('protein', 0)} | {l.get('density', '0.0%')} |"
            for l in today_logs
        )
        logs_context = f"""
### TODAY'S EXPLICIT FOOD LOGS (authoritative source of truth):
| Item | Cals | Protein | Density |
|------|------|---------|---------|
{rows}
"""
    else:
        logs_context = """
### TODAY'S EXPLICIT FOOD LOGS (authoritative source of truth):
**Nothing logged today yet.** The day is fresh — the running item table is empty.
DO NOT pull food items from previous days' conversation history into today's table. Any food references in chat history from prior dates are reference material only, not today's intake.
"""

    coaching_mode = ""
    if today_stats:
        protein_done  = today_stats['protein'] >= goals['protein']
        cals_near_lid = today_stats['cals'] >= int(goals['calories'] * 0.88)
        is_evening    = now.hour >= 18

        if is_bulk:
            # Bulk mode coaching modes
            cals_under_target = today_stats['cals'] < int(goals['calories'] * 0.70)
            if protein_done and is_evening:
                mode_text = (
                    "CLOSE-OF-DAY MODE (RECOMP): Protein floor is ACHIEVED and it is evening. "
                    "Shift to a warm wrap-up tone. Review whether the calorie surplus target was hit. "
                    "If surplus was missed, note it as a growth opportunity left on the table. "
                    "If surplus was hit, celebrate the consistency."
                )
            elif protein_done:
                mode_text = (
                    "PROTEIN COMPLETE (RECOMP): The protein floor has been hit. "
                    "Focus coaching on whether the calorie surplus target is being met. "
                    "Encourage eating to the target if there's still room."
                )
            elif cals_under_target and is_evening:
                mode_text = (
                    "CALORIE FLOOR ALERT (RECOMP): It's evening and calories are significantly "
                    "under the surplus target. The user is under-fueling for growth. "
                    "Suggest calorie-dense, protein-rich options to close the gap."
                )
            else:
                mode_text = (
                    "ACTIVE LOGGING MODE (RECOMP): Acknowledge the log warmly and show running totals. "
                    "If protein is behind, suggest high-protein options. "
                    "If calories are under target, gently encourage eating to fuel growth. "
                    "Keep the response brief."
                )
        else:
            # Cut mode coaching modes (original)
            if protein_done and is_evening:
                mode_text = (
                    "CLOSE-OF-DAY MODE: Protein floor is ACHIEVED and it is evening. "
                    "Shift to a warm wrap-up tone. Do NOT suggest eating more protein or additional meals. "
                    "Celebrate the day's wins with a brief, energetic close-of-day summary."
                )
            elif protein_done:
                mode_text = (
                    "PROTEIN COMPLETE: The protein floor has already been hit for today. "
                    "Do NOT suggest more protein sources or pivot strategies. "
                    "Focus coaching on calorie headroom and density quality only."
                )
            elif cals_near_lid:
                mode_text = (
                    "CALORIE CEILING ALERT: Calories are close to the lid. "
                    "Only suggest very high-density, low-calorie protein sources. "
                    "Do not recommend any calorie-heavy foods."
                )
            else:
                mode_text = (
                    "ACTIVE LOGGING MODE: Acknowledge the log warmly and show the running totals. "
                    "Only suggest upcoming food choices if protein is behind AND less than half the calorie budget remains — "
                    "otherwise just affirm and keep the response brief."
                )
        coaching_mode = f"\n### COACHING MODE (apply this to your response tone and suggestions):\n{mode_text}\n"

    # Trigger close-of-day mode when the user signals they're done eating,
    # regardless of clock time — "ending the day with..." is a stronger signal than 6 PM.
    msg_lower = user_message.lower()
    user_signaled_close = any(phrase in msg_lower for phrase in _CLOSING_PHRASES)
    is_close_of_day = (
        today_stats is not None
        and (user_signaled_close or now.hour >= 18)
    )
    weekly_context = ""
    if weekly_summary and is_close_of_day:
        today_str = now.strftime("%Y-%m-%d")
        completed = [r for r in weekly_summary if r['date'] != today_str]
        if completed:
            header = "| Date | Calories | Protein | Density |\n|------|----------|---------|---------|"
            rows_str = "\n".join(
                f"| {r['date']} | {r['calories']} | {r['protein']} | {r['density']} |"
                for r in completed
            )
            weekly_context = f"\n### ROLLING 7-DAY TREND (completed days only):\n{header}\n{rows_str}"

    return f"""
### CRITICAL: USER PREFERENCES & CONSTRAINTS (HIGHEST PRIORITY):
{custom_instructions}
- **Negative Constraint:** NEVER call the user "Commander" or use military/warlike terminology (e.g., "Sitreps", "Tactical", "Mission").
- **Persona Alignment:** Always respect the user's explicit requests in the chat history over the base persona directives.
- **No Duplicate Warnings:** NEVER generate "System Notice" messages or ask the user to confirm they are logging the same item twice. Duplicate items are completely expected (e.g., two shakes in one day). Log every item the user reports immediately, without any confirmation prompts.

You are the RatioTen Assistant — a knowledgeable, friendly nutrition coach. You are accurate with numbers, genuinely interested in the user's progress, and conversational in tone. Match your energy to the moment: brief and punchy for routine logs, warm and reflective at day's end. Reserve big energy for big moments.

{persona.get_bio_data(user_id)}
{persona.TONE_GUIDANCE}
{persona.VOCABULARY}
{persona.BANTER_INSTRUCTIONS}
{persona.RESPONSE_TEMPLATES}
{"" if not is_close_of_day else (persona.BULK_RELATIONSHIP_CLOSING if is_bulk else persona.RELATIONSHIP_CLOSING)}

{persona.BULK_MODE_CONTEXT if is_bulk else ""}

{time_awareness}

Core Logic:
- Primary Quality Metric: Protein Density (Goal: 10.0%).
- Calculated explicitly as: (Protein in grams / Total Calories).
- **Current Training Mode:** {"RECOMP (Muscle Growth / Maintenance Phase)" if is_bulk else "CUT (Fat Loss Phase)"}

{stats_context}{coaching_mode}
{logs_context}
{weekly_context}

Fasting Protocol Strict Adherence:
{formatted_schedule}

Multimodal Capabilities (Image Analysis):
- You can identify food items and estimate portion sizes from images.
- Nutritional Labels: When a nutritional label is provided in an image, parse the calories and protein from the label. These values are the source of truth.
- User Adjustments: Always take into account additional user input that might adjust the value (e.g., "Ate half of this").

Formatting Constraints (Mobile Optimized):
- Use standard Markdown tables. Do not use raw ASCII formatting.
- The "Density" column must always be displayed as a percentage with exactly one decimal place (e.g., 11.5%, 5.0%).
- NEVER include Date or Date-Range columns in visible output.
- Do NOT include today's partial data in any trend table. Today's progress belongs in the inline totals line only.
- NEVER construct, approximate, or recreate a rolling trend table from conversation history. The trend table is provided by the system — if there is no ROLLING 7-DAY TREND section in this prompt, the table does not exist and must not be displayed.
- When logging food, display ONE table using exactly these short headers: `| Item | Cals | Protein | Density |`. Do NOT use "Calories" or "Protein (g)" — the short headers fit better on mobile.
- When the message is conversational (no new food being logged — strategy questions, reflections, corrections that don't add an item, planning), DO NOT show the item table or running totals line. Reply conversationally only.
- **Source of Truth for Item Table:** The "TODAY'S EXPLICIT FOOD LOGS" section above is the authoritative record of everything logged today. If that section says "Nothing logged today yet," then today is empty — DO NOT pull items from prior days' conversation history into today's table. When food IS being logged, include ALL items from that injected list PLUS any new item(s) from the current message.

Response Format After the Item Table:
- Goals: {"~" if is_bulk else "<="} {goals['calories']} cal{"" if is_bulk else ""} | >= {goals['protein']}g protein | >= 10.0% density.
- After the item table, output ONE inline totals line in exactly this format:
  **Cals:** X / {goals['calories']} | **Protein:** Xg / {goals['protein']}g | **Density:** X.X%
- Then 1–3 sentences of natural conversational response. No headers. No bullet lists. No sub-sections.
- DO NOT reproduce the situation report stats block in your response. The inline totals line is sufficient.
- See the RESPONSE TEMPLATES section for the expected format by coaching mode.

Daily 6:00 PM Wrap-Up (Creatine Check):
- Check logs for "protein shake" or "ultra-filtered shake".
- If present: Assume creatine was taken.
- If missing: Remind me to "clear the supplement" (Creatine Watchdog).

### CREATIVE EMOJI SELECTION PROTOCOL (CRITICAL):
- Rule 1: Abstract Reasoning — "Marinated Mozzarella" → 🧀
- Rule 2: NEVER use generic plate/cutlery (🍽️, 🍴) for identifiable meals.
- Rule 3: Ingredient Decomposition — "Spicy Beef Bowl" → 🥩🌶️
- Rule 4: Shake/Supplement Logic — 🥤/🥛 for shakes, 💊 for vitamins.

Calibration: "Double Espresso" → ☕⚡  |  "Core Power Shake" → 🥤💪  |  "Sashimi" → 🍣🍱

JSON Output for Database Logging:
- When the user logs food, append a JSON block at the very end of your response.
- Format:
```json
[
  {{"item": "Food Name", "calories": 150, "protein": 30, "density": "20.0%", "emoji": "🍗🌿"}}
]
```
- Only include the JSON block if new food is being logged.
- If the user explicitly states they ate something at a specific earlier time today (e.g. "I had eggs at 8am", "logging lunch from noon"), include an optional `"logged_at": "HH:MM"` field (24-hour format) in that entry. Only include it when a past time is clearly stated — never guess or infer a time. Do not include it for food being logged now.
"""


# ---------------------------------------------------------------------------
# Gemini chat session builder
# ---------------------------------------------------------------------------

def _make_chat_session(model_id: str, system_prompt: str, history: list):
    client = _get_gemini()
    config_params = {"system_instruction": system_prompt}
    if "2.5" in model_id or "3" in model_id:
        try:
            config_params["thinking_config"] = genai.types.ThinkingConfig(include_thoughts=True)
        except Exception:
            pass
    cfg = genai.types.GenerateContentConfig(**config_params)
    # Convert history to genai Content objects
    genai_history = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else msg["role"]
        parts = []
        for c in msg.get("content", []):
            if isinstance(c, str) and c.strip():
                parts.append(genai.types.Part.from_text(text=c))
        if parts:
            genai_history.append(genai.types.Content(role=role, parts=parts))
    return client.chats.create(model=model_id, config=cfg, history=genai_history)


def _parse_meal_log(raw: str):
    try:
        m = re.search(r"```json\s*(\[.*?\])\s*```", raw, re.DOTALL)
        if not m:
            return None
        data = json.loads(m.group(1))
        if not isinstance(data, list):
            return None
        required = {"item", "calories", "protein", "density", "emoji"}
        validated = []
        for entry in data:
            if isinstance(entry, dict) and required.issubset(entry.keys()):
                validated.append(entry)
        return validated if validated else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse("index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/version")
async def version():
    commit = os.environ.get("RENDER_GIT_COMMIT", "")
    short  = commit[:7] if commit else "local"
    return {"commit": short}


@app.get("/api/dashboard")
async def dashboard(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    schedule       = _cached(f"schedule_{uid}",       600,  lambda: _read_fasting_schedule(uid))
    goals          = _cached(f"goals_{uid}",          600,  lambda: _read_user_goals(uid))
    today_logs     = _cached(f"today_logs_{uid}",      60,  lambda: _read_today_logs(uid))
    lowest_weight  = _cached(f"lowest_weight_{uid}", 3600,  lambda: _read_lowest_weight(uid),  empty_ttl=30)
    weight_history = _cached(f"weight_history_{uid}", 3600, lambda: _read_weight_history(uid), empty_ttl=30)

    # Today's totals
    cals    = sum(l["calories"] for l in today_logs)
    protein = sum(l["protein"]  for l in today_logs)
    density = f"{(protein / cals * 100):.1f}%" if cals else "0.0%"

    fasting_status, target_ts = get_fasting_status(schedule)

    return {
        "cals":           cals,
        "protein":        protein,
        "density":        density,
        "goals":          goals,
        "mode":           goals.get("mode", DEFAULT_MODE),
        "lowest_weight":  lowest_weight,
        "weight_history": weight_history,
        "fasting_status": fasting_status,
        "target_ts":      target_ts,
    }


@app.get("/api/logs/today")
async def logs_today(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    today_logs = _cached(f"today_logs_{uid}", 60,  lambda: _read_today_logs(uid))
    schedule   = _cached(f"schedule_{uid}",   600, lambda: _read_fasting_schedule(uid))
    now        = datetime.now(EASTERN)
    day_sched  = schedule.get(now.strftime("%A"), {"start": None, "end": None})

    result = []
    for l in today_logs:
        entry = {k: v for k, v in l.items() if k != "timestamp"}
        entry["time"] = l["timestamp"].strftime("%H:%M") if isinstance(l.get("timestamp"), datetime) else ""
        entry["ts"]   = l["timestamp"].strftime("%Y-%m-%d %H:%M:%S") if isinstance(l.get("timestamp"), datetime) else ""
        # Pre-compute position % for timeline rendering on the client
        if day_sched["start"] and day_sched["end"]:
            try:
                s = datetime.strptime(day_sched["start"], "%H:%M").time()
                e = datetime.strptime(day_sched["end"],   "%H:%M").time()
                ts_t = l["timestamp"].time() if isinstance(l.get("timestamp"), datetime) else None
                if ts_t:
                    total = (datetime.combine(now.date(), e) - datetime.combine(now.date(), s)).total_seconds()
                    elapsed = (datetime.combine(now.date(), ts_t) - datetime.combine(now.date(), s)).total_seconds()
                    entry["pos_pct"] = max(0.0, min(100.0, (elapsed / total) * 100)) if total > 0 else 0.0
                else:
                    entry["pos_pct"] = 50.0
            except Exception:
                entry["pos_pct"] = 50.0
        else:
            entry["pos_pct"] = 50.0
        result.append(entry)

    # Also return window info for progress marker
    window_info = {"start": day_sched["start"], "end": day_sched["end"]}
    return {"logs": result, "window": window_info}


@app.get("/api/chat/history")
async def chat_history(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    msgs = _cached(f"chat_history_{uid}", 600, lambda: _read_persistent_chat(uid))
    return {"messages": msgs}


@app.delete("/api/chat/history")
async def clear_chat(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    try:
        sh = _get_sh(uid)
        ws = sh.worksheet(WS_CHAT_HISTORY)
        ws.clear()
        ws.append_row(["Timestamp", "Role", "Parts"])
        _invalidate(f"chat_history_{uid}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class DeleteLogsRequest(BaseModel):
    timestamps: list[str]
    user_id: str = DEFAULT_USER


@app.post("/api/logs/delete")
async def delete_logs(req: DeleteLogsRequest):
    uid = req.user_id if req.user_id in USER_CONFIGS else DEFAULT_USER
    try:
        sh = _get_sh(uid)
        ws = sh.sheet1
        values = ws.get_all_values()
        ts_set = set(req.timestamps)
        rows_to_delete = []
        deleted_items = []
        for i, row in enumerate(values):
            if i == 0:
                continue  # skip header
            if not row:
                continue
            if str(row[0]).strip() in ts_set:
                rows_to_delete.append(i + 1)  # gspread rows are 1-based
                deleted_items.append({
                    "item":     str(row[1]) if len(row) > 1 else "",
                    "calories": int(float(row[2])) if len(row) > 2 and row[2] else 0,
                    "protein":  int(float(row[3])) if len(row) > 3 and row[3] else 0,
                })
        if not rows_to_delete:
            return {"ok": True, "deleted": 0}
        for row_idx in sorted(rows_to_delete, reverse=True):
            ws.delete_rows(row_idx)
        _invalidate(f"today_logs_{uid}")
        _invalidate(f"trailing_7_{uid}")
        # Inject a context message into chat history so the AI stays aware
        if deleted_items:
            items_str = ", ".join(
                f"{d['item']} ({d['calories']} cal / {d['protein']}g)"
                for d in deleted_items
            )
            new_logs  = _read_today_logs(uid)
            new_cals  = sum(l["calories"] for l in new_logs)
            new_prot  = sum(l["protein"]  for l in new_logs)
            new_dens  = f"{(new_prot / new_cals * 100):.1f}%" if new_cals else "0.0%"
            ctx_msg = (
                f"[System: User removed the following items from today's log via the log manager: "
                f"{items_str}. Updated day totals: {new_cals} cal / {new_prot}g protein / {new_dens} density.]"
            )
            _log_chat_to_sheet("user", ctx_msg, uid)
            _invalidate(f"chat_history_{uid}")
        return {"ok": True, "deleted": len(rows_to_delete)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat")
async def chat(
    text:    str        = Form(...),
    image:   UploadFile = File(None),
    user_id: str        = Form(DEFAULT_USER),
):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    schedule     = _cached(f"schedule_{uid}",     600, lambda: _read_fasting_schedule(uid))
    goals        = _cached(f"goals_{uid}",        600, lambda: _read_user_goals(uid))
    today_logs   = _cached(f"today_logs_{uid}",    60, lambda: _read_today_logs(uid))
    custom_instr = _cached(f"custom_instr_{uid}", 600, lambda: _read_custom_instructions(uid))
    history      = _cached(f"chat_history_{uid}", 600, lambda: _read_persistent_chat(uid))
    trailing     = _cached(f"trailing_7_{uid}",   600, lambda: _read_trailing_7_days(uid))

    cals    = sum(l["calories"] for l in today_logs)
    protein = sum(l["protein"]  for l in today_logs)
    density = f"{(protein / cals * 100):.1f}%" if cals else "0.0%"
    today_stats = {"cals": cals, "protein": protein, "density": density}

    active_mode = goals.get("mode", DEFAULT_MODE)
    system_prompt = build_system_prompt(
        schedule, goals, custom_instr,
        today_stats=today_stats,
        today_logs=today_logs,
        weekly_summary=trailing,
        user_message=text,
        mode=active_mode,
        user_id=uid,
    )

    # Read image bytes if provided
    image_bytes = None
    if image and image.filename:
        image_bytes = await image.read()

    # Log user message to sheet
    user_content = []
    if image_bytes:
        user_content.append("📷 *Photo attached*")
    user_content.append(text)
    _log_chat_to_sheet("user", user_content, uid)
    _invalidate(f"chat_history_{uid}")

    async def generate():
        full_response = ""
        meal_log = None
        models = [PRIMARY_MODEL, SECONDARY_MODEL, STABLE_MODEL]

        for model_id in models:
            try:
                session = _make_chat_session(model_id, system_prompt, history)
                # Build message parts
                parts = []
                if image_bytes:
                    parts.append(genai.types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))
                parts.append(genai.types.Part.from_text(text=text))

                for chunk in session.send_message_stream(parts):
                    token = ""
                    try:
                        for part in chunk.candidates[0].content.parts:
                            if hasattr(part, "text") and part.text and not getattr(part, "thought", False):
                                token += part.text
                    except Exception:
                        try:
                            token = chunk.text or ""
                        except Exception:
                            token = ""
                    if token:
                        full_response += token
                        yield f"data: {json.dumps({'token': token})}\n\n"

                break  # success — don't try next model

            except Exception as e:
                err_str = str(e).lower()
                is_retryable = any(x in err_str for x in ["503", "unavailable", "429", "resource_exhausted"])
                if is_retryable and model_id != models[-1]:
                    continue  # try next model
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

        # Parse meal log JSON from response
        meal_log = _parse_meal_log(full_response)

        # Log assistant response to sheet
        _log_chat_to_sheet("assistant", full_response, uid)
        _invalidate(f"chat_history_{uid}")

        # Write meal entries to sheet
        logged_items = []
        if meal_log:
            for entry in meal_log:
                replaces = entry.get("replaces")
                if replaces:
                    # Correction flow: overwrite the most recent matching row
                    old = _replace_log_entry(
                        replaces,
                        entry["item"],
                        int(entry.get("calories", 0)),
                        int(entry.get("protein", 0)),
                        str(entry.get("density", "0.0%")),
                        str(entry.get("emoji", "🍽️")),
                        uid,
                    )
                    if old is not None:
                        logged_items.append({**entry, "_replaced": old})
                        continue
                    # No match found — fall through to normal append
                ok = _log_to_sheet(
                    entry["item"],
                    int(entry.get("calories", 0)),
                    int(entry.get("protein", 0)),
                    str(entry.get("density", "0.0%")),
                    str(entry.get("emoji", "🍽️")),
                    uid,
                    logged_at=entry.get("logged_at"),  # optional "HH:MM", same-day only
                )
                if ok:
                    logged_items.append(entry)

        yield f"data: {json.dumps({'done': True, 'logged': logged_items})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/goals")
async def get_goals(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    return _cached(f"goals_{uid}", 600, lambda: _read_user_goals(uid))


@app.post("/api/goals")
async def save_goals(
    calories: int = Form(...),
    protein:  int = Form(...),
    mode:     str = Form(DEFAULT_MODE),
    user_id:  str = Form(DEFAULT_USER),
):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    clean_mode = mode.strip().lower()
    if clean_mode not in (MODE_CUT, MODE_BULK):
        clean_mode = DEFAULT_MODE
    try:
        sh = _get_sh(uid)
        try:
            ws = sh.worksheet(WS_USER_GOALS)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_USER_GOALS, rows="10", cols="2")
        ws.clear()
        ws.append_row(["Metric", "Value"])
        ws.append_row(["Calories", calories])
        ws.append_row(["Protein", protein])
        ws.append_row(["Mode", clean_mode])
        _invalidate(f"goals_{uid}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schedule")
async def get_schedule(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    return _cached(f"schedule_{uid}", 600, lambda: _read_fasting_schedule(uid))


@app.post("/api/schedule")
async def save_schedule(body: dict, user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    try:
        sh = _get_sh(uid)
        try:
            ws = sh.worksheet(WS_FASTING_SCHEDULE)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_FASTING_SCHEDULE, rows="10", cols="3")
        ws.clear()
        ws.append_row(["DayOfWeek", "WindowStart", "WindowEnd"])
        for day, times in body.items():
            start_val = times.get("start") or "Skip"
            end_val   = times.get("end")   or "Skip"
            ws.append_row([day, start_val, end_val])
        _invalidate(f"schedule_{uid}")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analyze")
async def analyze(user_id: str = Query(DEFAULT_USER)):
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    schedule = _cached(f"schedule_{uid}",    600, lambda: _read_fasting_schedule(uid))
    goals    = _cached(f"goals_{uid}",       600, lambda: _read_user_goals(uid))
    trailing = _cached(f"trailing_7_{uid}",  300, lambda: _read_trailing_7_days(uid))
    wow      = _cached(f"wow_{uid}",         300, lambda: _read_wow(uid))
    history  = _cached(f"log_history_{uid}", 300, lambda: _read_logs_history(10, uid))

    mode = goals.get("mode", DEFAULT_MODE)
    score, err, drivers = calculate_plan_effectiveness(
        pre_sh=_get_sh(uid), pre_goals=goals, pre_fasting=schedule, mode=mode,
    )

    return {
        "effectiveness": {"score": score, "error": err, "drivers": drivers},
        "trailing_7":    trailing,
        "wow":           wow,
        "log_history":   history,
        "schedule":      schedule,
        "mode":          mode,
    }


@app.post("/api/effectiveness/sync")
async def effectiveness_sync(user_id: str = Query(DEFAULT_USER)):
    uid      = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    schedule = _cached(f"schedule_{uid}", 600, lambda: _read_fasting_schedule(uid))
    goals    = _cached(f"goals_{uid}",    600, lambda: _read_user_goals(uid))
    mode     = goals.get("mode", DEFAULT_MODE)
    sh       = _get_sh(uid)
    # Run in a thread pool so it doesn't block the event loop
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: sync_plan_effectiveness_logs(
            force_resync=True, goals=goals, fasting=schedule, mode=mode, pre_sh=sh,
        )
    )
    return {"ok": True}


@app.get("/api/debug/weight")
async def debug_weight(user_id: str = Query(DEFAULT_USER)):
    """Temporary debug endpoint — shows raw Weight_Logs sheet state."""
    uid = user_id if user_id in USER_CONFIGS else DEFAULT_USER
    try:
        sh = _get_sh(uid)
        try:
            ws = sh.worksheet(WS_WEIGHT_LOGS)
        except Exception as e:
            return {"error": f"worksheet not found: {e}", "ws_name": WS_WEIGHT_LOGS}
        raw = ws.get_all_records()
        # Show column headers and first/last few rows
        headers = list(raw[0].keys()) if raw else []
        sample  = raw[:3] + (raw[-3:] if len(raw) > 6 else [])
        # Also run the actual parse so we can see what's produced
        _invalidate(f"weight_history_{uid}")
        _invalidate(f"lowest_weight_{uid}")
        parsed_history = _read_weight_history(uid)
        parsed_lowest  = _read_lowest_weight(uid)
        return {
            "ws_name":        WS_WEIGHT_LOGS,
            "row_count":      len(raw),
            "headers":        headers,
            "sample_rows":    sample,
            "parsed_history": parsed_history,
            "parsed_lowest":  parsed_lowest,
            "parse_count":    len(parsed_history),
        }
    except Exception as e:
        return {"error": str(e)}
