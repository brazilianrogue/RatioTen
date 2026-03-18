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
from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google import genai

import persona
from constants import (
    EASTERN,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    STABLE_MODEL,
    WS_CHAT_HISTORY,
    SPREADSHEET_NAME,
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


def _cached(key: str, ttl: int, fn):
    entry = _cache.get(key)
    if entry and entry[1] > time.time():
        return entry[0]
    val = fn()
    _cache[key] = (val, time.time() + ttl)
    return val


def _invalidate(key: str):
    _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Shared clients (created once at startup)
# ---------------------------------------------------------------------------
_gc: gspread.Client | None = None
_sh: gspread.Spreadsheet | None = None
_gemini: genai.Client | None = None


def _get_sh():
    global _gc, _sh
    if _sh is None:
        creds = json.loads(os.environ["GCP_SERVICE_ACCOUNT_JSON"])
        _gc = gspread.service_account_from_dict(creds)
        _sh = _gc.open(SPREADSHEET_NAME)
    return _sh


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
DEFAULT_GOALS = {"calories": 1500, "protein": 150}


def _read_fasting_schedule() -> dict:
    try:
        sh = _get_sh()
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


def _read_user_goals() -> dict:
    try:
        sh = _get_sh()
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
        return goals
    except Exception:
        return DEFAULT_GOALS.copy()


def _read_custom_instructions() -> str:
    try:
        sh = _get_sh()
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


def _read_lowest_weight() -> Optional[float]:
    try:
        sh = _get_sh()
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


def _read_today_logs() -> list:
    try:
        sh = _get_sh()
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


def _read_trailing_7_days() -> list[dict]:
    """Returns a list of {date, calories, protein, density} dicts, newest first."""
    try:
        sh = _get_sh()
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


def _read_logs_history(days: int = 10) -> dict:
    """Returns {date_str: [log_entry, ...]} for the past `days` days (excluding today)."""
    try:
        sh = _get_sh()
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


def _read_wow() -> list[dict]:
    """Week-over-week averages."""
    try:
        sh = _get_sh()
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


def _read_persistent_chat() -> list[dict]:
    try:
        sh = _get_sh()
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


def _log_chat_to_sheet(role: str, content):
    try:
        sh = _get_sh()
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
        _invalidate("chat_history")
    except Exception:
        pass


def _log_to_sheet(item: str, calories: int, protein: int, density: str, emoji: str = "🍽️") -> bool:
    try:
        sh = _get_sh()
        ws = sh.sheet1
        now = datetime.now(EASTERN)
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        y, w, _ = now.isocalendar()
        week_num = f"{y}-W{w:02d}"
        ws.append_row([ts, item, calories, protein, density, week_num, emoji])
        _invalidate("today_logs")
        _invalidate("trailing_7")
        return True
    except Exception as e:
        log.error("Failed to log meal: %s", e)
        return False


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

def build_system_prompt(
    schedule: dict,
    goals: dict,
    custom_instructions: str = "",
    today_stats: Optional[dict] = None,
    today_logs: Optional[list] = None,
    weekly_summary: Optional[list] = None,
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

    stats_context = ""
    if today_stats:
        stats_context = f"""
### CURRENT DAY SITUATION REPORT:
- **Calories Ingested:** {today_stats['cals']} / {goals['calories']} (Lid)
- **Protein Ingested:** {today_stats['protein']}g / {goals['protein']}g (Floor)
- **Current Density:** {today_stats['density']}
- **Remaining Calorie Room:** {max(0, goals['calories'] - today_stats['cals'])}
- **Remaining Protein Needed:** {max(0, goals['protein'] - today_stats['protein'])}g
"""

    logs_context = ""
    if today_logs:
        rows = "\n".join(
            f"| {l['item']} ({l['emoji']}) | {l.get('calories', 0)} | {l.get('protein', 0)} | {l.get('density', '0.0%')} |"
            for l in today_logs
        )
        logs_context = f"""
### TODAY'S EXPLICIT FOOD LOGS:
| Item | Calories | Protein (g) | Density |
|------|----------|-------------|---------|
{rows}
"""

    weekly_context = ""
    if weekly_summary:
        header = "| Date | Calories | Protein | Density |\n|------|----------|---------|---------|"
        rows_str = "\n".join(
            f"| {r['date']} | {r['calories']} | {r['protein']} | {r['density']} |"
            for r in weekly_summary
        )
        weekly_context = f"\n### ROLLING 7-DAY TREND:\n{header}\n{rows_str}"

    return f"""
### CRITICAL: USER PREFERENCES & CONSTRAINTS (HIGHEST PRIORITY):
{custom_instructions}
- **Negative Constraint:** NEVER call the user "Commander" or use military/warlike terminology (e.g., "Sitreps", "Tactical", "Mission").
- **Persona Alignment:** Always respect the user's explicit requests in the chat history over the base persona directives.

You are the RatioTen Assistant, acting as an **Enthusiastic Nutrition & Fitness Coach**.
You are precise, analytical, supportive, and deeply encouraging.

{persona.BIO_DATA}
{persona.TONE_GUIDANCE}
{persona.VOCABULARY}
{persona.BANTER_INSTRUCTIONS}
{persona.RELATIONSHIP_CLOSING}

{time_awareness}

Core Logic:
- Primary Quality Metric: Protein Density (Goal: 10.0%).
- Calculated explicitly as: (Protein in grams / Total Calories).

{stats_context}
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
- When logging food, display ONLY ONE table with the items, but you MUST also include conversational banter.
  1. Current Day's Items: (Item Name, Cals, Protein, Density)

Daily Targets & Banter (REQUIRED):
- Goal: <= {goals['calories']} Calories, >= {goals['protein']}g Protein, Density Target: >= 10.0%.
- Below the data table, you MUST evaluate each logged item and the overall progress for the day.
- Use "Shred Language" and maintain the persona.
- Ending: Always end with a "Verdict" or "Strategy" for the next meal.

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
        role = msg["role"]
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
    return FileResponse("index.html")


@app.get("/api/dashboard")
async def dashboard():
    schedule      = _cached("schedule",      600, _read_fasting_schedule)
    goals         = _cached("goals",         600, _read_user_goals)
    today_logs    = _cached("today_logs",     60, _read_today_logs)
    lowest_weight = _cached("lowest_weight", 3600, _read_lowest_weight)

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
        "lowest_weight":  lowest_weight,
        "fasting_status": fasting_status,
        "target_ts":      target_ts,
    }


@app.get("/api/logs/today")
async def logs_today():
    today_logs = _cached("today_logs", 60, _read_today_logs)
    schedule   = _cached("schedule",   600, _read_fasting_schedule)
    now        = datetime.now(EASTERN)
    day_sched  = schedule.get(now.strftime("%A"), {"start": None, "end": None})

    result = []
    for l in today_logs:
        entry = {k: v for k, v in l.items() if k != "timestamp"}
        entry["time"] = l["timestamp"].strftime("%H:%M") if isinstance(l.get("timestamp"), datetime) else ""
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
async def chat_history():
    msgs = _cached("chat_history", 600, _read_persistent_chat)
    return {"messages": msgs}


@app.delete("/api/chat/history")
async def clear_chat():
    try:
        sh = _get_sh()
        ws = sh.worksheet(WS_CHAT_HISTORY)
        ws.clear()
        ws.append_row(["Timestamp", "Role", "Parts"])
        _invalidate("chat_history")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/chat")
async def chat(
    text:  str        = Form(...),
    image: UploadFile = File(None),
):
    schedule     = _cached("schedule",     600, _read_fasting_schedule)
    goals        = _cached("goals",        600, _read_user_goals)
    today_logs   = _cached("today_logs",    60, _read_today_logs)
    custom_instr = _cached("custom_instr", 600, _read_custom_instructions)
    history      = _cached("chat_history", 600, _read_persistent_chat)
    trailing     = _cached("trailing_7",   600, _read_trailing_7_days)

    cals    = sum(l["calories"] for l in today_logs)
    protein = sum(l["protein"]  for l in today_logs)
    density = f"{(protein / cals * 100):.1f}%" if cals else "0.0%"
    today_stats = {"cals": cals, "protein": protein, "density": density}

    system_prompt = build_system_prompt(
        schedule, goals, custom_instr,
        today_stats=today_stats,
        today_logs=today_logs,
        weekly_summary=trailing,
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
    _log_chat_to_sheet("user", user_content)
    _invalidate("chat_history")

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
        _log_chat_to_sheet("assistant", full_response)
        _invalidate("chat_history")

        # Write meal entries to sheet
        logged_items = []
        if meal_log:
            for entry in meal_log:
                ok = _log_to_sheet(
                    entry["item"],
                    int(entry.get("calories", 0)),
                    int(entry.get("protein", 0)),
                    str(entry.get("density", "0.0%")),
                    str(entry.get("emoji", "🍽️")),
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
async def get_goals():
    return _cached("goals", 600, _read_user_goals)


@app.post("/api/goals")
async def save_goals(calories: int = Form(...), protein: int = Form(...)):
    try:
        sh = _get_sh()
        try:
            ws = sh.worksheet(WS_USER_GOALS)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=WS_USER_GOALS, rows="10", cols="2")
        ws.clear()
        ws.append_row(["Metric", "Value"])
        ws.append_row(["Calories", calories])
        ws.append_row(["Protein", protein])
        _invalidate("goals")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schedule")
async def get_schedule():
    return _cached("schedule", 600, _read_fasting_schedule)


@app.post("/api/schedule")
async def save_schedule(body: dict):
    try:
        sh = _get_sh()
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
        _invalidate("schedule")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/analyze")
async def analyze():
    schedule = _cached("schedule", 600, _read_fasting_schedule)
    goals    = _cached("goals",    600, _read_user_goals)
    trailing = _cached("trailing_7", 300, _read_trailing_7_days)
    wow      = _cached("wow",        300, _read_wow)
    history  = _cached("log_history", 300, lambda: _read_logs_history(10))

    score, err, drivers = calculate_plan_effectiveness(
        pre_sh=_get_sh(), pre_goals=goals, pre_fasting=schedule
    )

    return {
        "effectiveness": {"score": score, "error": err, "drivers": drivers},
        "trailing_7":    trailing,
        "wow":           wow,
        "log_history":   history,
        "schedule":      schedule,
    }


@app.post("/api/effectiveness/sync")
async def effectiveness_sync():
    schedule = _cached("schedule", 600, _read_fasting_schedule)
    goals    = _cached("goals",    600, _read_user_goals)
    # Run in a thread pool so it doesn't block the event loop
    await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: sync_plan_effectiveness_logs(force_resync=True, goals=goals, fasting=schedule)
    )
    return {"ok": True}
