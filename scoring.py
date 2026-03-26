"""Plan Effectiveness scoring engine for RatioTen.

Public API
----------
calculate_plan_effectiveness(calc_date, pre_sh, pre_goals, pre_fasting, demo_mode)
    → (score | None, error_msg | None, drivers | None)

sync_plan_effectiveness_logs(force_resync, goals, fasting, demo_mode)
    → None  (writes results back to Google Sheets)

All magic numbers come from constants.py so thresholds are self-documenting
and easy to tune.  Errors are emitted through Python's logging module
(visible in Streamlit Cloud logs) instead of a local flat file.
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

import gspread
import pandas as pd

from constants import (
    ADHERENCE_MAX_POINTS,
    BULK_CAL_FLOOR_BUFFER,
    BULK_CAL_FULL_POINTS,
    BULK_CAL_PARTIAL_POINTS,
    BULK_CAL_SURPLUS_MAX,
    BULK_WEIGHT_GAIN_EXCESS,
    BULK_WEIGHT_GAIN_SWEET_MAX,
    BULK_WEIGHT_GAIN_SWEET_MIN,
    BULK_WEIGHT_LOSS_PENALTY,
    BULK_WEIGHT_SCORE_BASE,
    BULK_WEIGHT_SCORE_MULTIPLIER,
    CAL_FULL_POINTS,
    CAL_PARTIAL_POINTS,
    CALORIE_PARTIAL_BUFFER,
    CALORIE_TARGET_BUFFER,
    DEFAULT_MODE,
    EASTERN,
    MIN_DAYS_FOR_SCORE,
    MIN_WEIGH_INS_FOR_SCORE,
    MODE_BULK,
    PROTEIN_FLOOR_FULL_HOURS,
    PROTEIN_FLOOR_MIN_FRACTION,
    PROTEIN_FLOOR_MIN_HOURS,
    PROTEIN_FULL_POINTS,
    PROTEIN_PARTIAL_POINTS,
    SCORE_APPEND_SLEEP,
    SCORE_BACKFILL_DAYS,
    SCORE_FORCE_BACKFILL_DAYS,
    SCORE_MAX,
    SCORE_MAX_DAYS_PER_RUN,
    SCORE_MIN,
    SCORE_WINDOW_DAYS,
    TIMING_BUFFER_HOURS,
    TIMING_FULL_POINTS,
    WEIGHT_GAIN_PENALTY,
    WEIGHT_GAIN_PENALTY_THRESHOLD,
    WEIGHT_LOSS_FULL_THRESHOLD,
    WEIGHT_MAX_POINTS,
    WEIGHT_SCORE_BASE,
    WEIGHT_SCORE_MULTIPLIER,
    WS_PLAN_EFFECTIVENESS,
    WS_WEIGHT_LOGS,
)
from sheets_client import open_sheet

log = logging.getLogger(__name__)


def calculate_plan_effectiveness(
    calc_date: "datetime.date | None" = None,
    pre_sh: "gspread.Spreadsheet | None" = None,
    pre_goals: "dict | None" = None,
    pre_fasting: "dict | None" = None,
    demo_mode: bool = False,
    mode: str = DEFAULT_MODE,
) -> "tuple[float | None, str | None, dict | None]":
    """Compute a plan effectiveness score (1–10) for the 14-day window ending on *calc_date*.

    Returns
    -------
    (score, error_message, drivers)
        *score* is ``None`` when there is insufficient data.
        *drivers* may be populated even when *score* is ``None`` (partial data).
    """
    try:
        if calc_date is None:
            calc_date = datetime.now(EASTERN).date()

        # --- Demo Mode ---
        if demo_mode:
            drivers: dict[str, Any] = {
                "adherent_days": 11,
                "total_days": 13,
                "avg_density": 10.8,
                "weight_shift": 1.4,
            }
            return 8.7, None, drivers

        if pre_goals is None:
            return None, "pre_goals is required", None
        if pre_fasting is None:
            return None, "pre_fasting is required", None

        sh = pre_sh if pre_sh is not None else open_sheet()
        goals = pre_goals
        fasting_schedule = pre_fasting

        is_bulk = mode == MODE_BULK
        target_cals = int(goals.get("calories", 1500))
        target_cal_limit = target_cals + CALORIE_TARGET_BUFFER
        weight_delta = 0.0

        thirteen_days_ago = calc_date - timedelta(days=SCORE_WINDOW_DAYS - 1)
        seven_days_ago = calc_date - timedelta(days=6)

        # -----------------------------------------------------------------
        # 1. Food Logs
        # -----------------------------------------------------------------
        try:
            food_ws = sh.sheet1
            values = food_ws.get_all_values()
            if not values or len(values) <= 1:
                return None, "No food log data found.", None

            headers = values[0]
            col_map = {h.strip(): i for i, h in enumerate(headers)}
            date_idx = col_map.get("Date", 0)
            cal_idx = col_map.get("Calories", 2)
            prot_idx = col_map.get("Protein", 3)

            daily_data: dict = {}
            for row in values[1:]:
                try:
                    if len(row) <= max(date_idx, cal_idx, prot_idx):
                        continue
                    dt = pd.to_datetime(row[date_idx])
                    log_date = dt.date()
                    if log_date < thirteen_days_ago or log_date > calc_date:
                        continue
                    cals = float(row[cal_idx]) if row[cal_idx] else 0.0
                    prot = float(row[prot_idx]) if row[prot_idx] else 0.0
                    if log_date not in daily_data:
                        daily_data[log_date] = {"cals": 0.0, "prot": 0.0, "logs": []}
                    daily_data[log_date]["cals"] += cals
                    daily_data[log_date]["prot"] += prot
                    daily_data[log_date]["logs"].append(dt)
                except Exception:
                    continue

            # Fill missing days so we always evaluate exactly SCORE_WINDOW_DAYS
            for i in range(SCORE_WINDOW_DAYS):
                eval_date = calc_date - timedelta(days=i)
                if eval_date not in daily_data:
                    day_name = eval_date.strftime("%A")
                    sched = fasting_schedule.get(day_name, {"start": None, "end": None})
                    is_fasting_day = not (sched["start"] and sched["end"])
                    daily_data[eval_date] = {
                        "cals": 0.0,
                        "prot": 0.0,
                        "logs": [],
                        "is_missing": not is_fasting_day,
                    }

            total_days_eval = SCORE_WINDOW_DAYS
            adherence_score_total = 0.0
            sum_cals = 0.0
            sum_prot = 0.0
            daily_breakdown: dict = {}

            for eval_date, nums in daily_data.items():
                day_name = eval_date.strftime("%A")
                sched = fasting_schedule.get(day_name, {"start": None, "end": None})

                # Determine eating-window hours
                if sched["start"] and sched["end"]:
                    try:
                        start_t = datetime.strptime(sched["start"], "%H:%M")
                        end_t = datetime.strptime(sched["end"], "%H:%M")
                        eating_hours = (end_t - start_t).total_seconds() / 3600.0
                        if eating_hours < 0:
                            eating_hours += 24.0  # cross-midnight window
                    except Exception:
                        eating_hours = 8.0
                else:
                    eating_hours = 0.0  # fast / skip day

                # Dynamic protein floor
                target_protein = float(goals.get("protein", 150))
                if eating_hours >= PROTEIN_FLOOR_FULL_HOURS:
                    dynamic_floor = target_protein
                elif eating_hours <= PROTEIN_FLOOR_MIN_HOURS:
                    dynamic_floor = (
                        0.0 if eating_hours == 0.0
                        else target_protein * PROTEIN_FLOOR_MIN_FRACTION
                    )
                else:
                    fraction = PROTEIN_FLOOR_MIN_FRACTION + (
                        (eating_hours - PROTEIN_FLOOR_MIN_HOURS)
                        / (PROTEIN_FLOOR_FULL_HOURS - PROTEIN_FLOOR_MIN_HOURS)
                    ) * (1.0 - PROTEIN_FLOOR_MIN_FRACTION)
                    dynamic_floor = target_protein * fraction

                sum_cals += nums["cals"]
                sum_prot += nums["prot"]

                # Calorie points (4 pts) — mode-dependent
                if is_bulk:
                    # Bulk: reward hitting a surplus range (target-100 to target+300)
                    cal_floor = target_cals - BULK_CAL_FLOOR_BUFFER
                    cal_ceiling = target_cals + BULK_CAL_SURPLUS_MAX
                    if cal_floor <= nums["cals"] <= cal_ceiling:
                        cal_pts = BULK_CAL_FULL_POINTS
                    elif nums["cals"] > cal_ceiling and nums["cals"] <= cal_ceiling + CALORIE_PARTIAL_BUFFER:
                        cal_pts = BULK_CAL_PARTIAL_POINTS  # slightly over surplus
                    elif nums["cals"] < cal_floor and nums["cals"] >= cal_floor - CALORIE_PARTIAL_BUFFER:
                        cal_pts = BULK_CAL_PARTIAL_POINTS  # slightly under target
                    else:
                        cal_pts = 0.0
                else:
                    # Cut: reward staying under the lid
                    if nums["cals"] <= target_cal_limit:
                        cal_pts = CAL_FULL_POINTS
                    elif nums["cals"] <= target_cal_limit + CALORIE_PARTIAL_BUFFER:
                        cal_pts = CAL_PARTIAL_POINTS
                    else:
                        cal_pts = 0.0

                # Protein points (4 pts)
                if nums["prot"] >= dynamic_floor:
                    prot_pts = PROTEIN_FULL_POINTS
                elif nums["prot"] >= dynamic_floor * 0.8:
                    prot_pts = PROTEIN_PARTIAL_POINTS
                else:
                    prot_pts = 0.0

                # Fasting timing points (2 pts)
                timing_pts = 0.0
                if eating_hours == 0.0:
                    if nums["cals"] == 0:
                        timing_pts = TIMING_FULL_POINTS
                elif len(nums["logs"]) > 0:
                    try:
                        s_time = datetime.strptime(sched["start"], "%H:%M").time()
                        e_time = datetime.strptime(sched["end"], "%H:%M").time()
                        buf_start = (
                            datetime.combine(eval_date, s_time)
                            - timedelta(hours=TIMING_BUFFER_HOURS)
                        )
                        buf_end = (
                            datetime.combine(eval_date, e_time)
                            + timedelta(hours=TIMING_BUFFER_HOURS)
                        )
                        if buf_end < buf_start:
                            buf_end += timedelta(days=1)
                        all_in_window = all(
                            buf_start <= log_dt.replace(tzinfo=None) <= buf_end
                            for log_dt in nums["logs"]
                        )
                        if all_in_window:
                            timing_pts = TIMING_FULL_POINTS
                    except Exception:
                        timing_pts = TIMING_FULL_POINTS  # benefit of the doubt

                day_score = cal_pts + prot_pts + timing_pts
                adherence_score_total += day_score / 10.0
                daily_breakdown[eval_date] = {
                    "cal_pts": cal_pts,
                    "prot_pts": prot_pts,
                    "time_pts": timing_pts,
                    "day_score": day_score,
                }

            avg_density = (sum_prot / sum_cals * 100) if sum_cals > 0 else 0.0
            adherence_rate = adherence_score_total / total_days_eval

            drivers = {
                "adherent_days": round(adherence_score_total, 1),
                "total_days": total_days_eval,
                "avg_density": avg_density,
                "adherence_rate": adherence_rate,
                "daily_breakdown": daily_breakdown,
                "weight_shift": 0.0,
            }

            days_with_data = sum(
                1 for d in daily_data.values() if not d.get("is_missing", False)
            )
            drivers["logging_pct"] = (days_with_data / total_days_eval * 100) if total_days_eval > 0 else 0.0

            if days_with_data < MIN_DAYS_FOR_SCORE:
                return (
                    None,
                    f"Need {MIN_DAYS_FOR_SCORE}+ days of data. Have {days_with_data}.",
                    drivers,
                )

        except Exception as exc:
            return None, f"Error parsing food logs: {exc}", None

        # -----------------------------------------------------------------
        # 2. Weight Logs
        # -----------------------------------------------------------------
        try:
            try:
                weight_ws = sh.worksheet(WS_WEIGHT_LOGS)
            except gspread.WorksheetNotFound:
                return None, "Weight_Logs sheet not found.", drivers

            weight_records = weight_ws.get_all_records()
            if not weight_records:
                return None, "No weight data found.", drivers

            df_weight = pd.DataFrame(weight_records)
            col_map_w = {col.lower().strip(): col for col in df_weight.columns}
            date_col = next(
                (col_map_w[c] for c in ["date", "timestamp", "time"] if c in col_map_w),
                None,
            )
            weight_col = next(
                (
                    col_map_w[c]
                    for c in ["weight (lbs)", "weight", "lbs"]
                    if c in col_map_w
                ),
                None,
            )

            if not date_col or not weight_col:
                return (
                    None,
                    f"Weight_Logs columns not found. Got: {list(df_weight.columns)}",
                    drivers,
                )

            df_weight["Date"] = pd.to_datetime(
                df_weight[date_col], errors="coerce"
            ).dt.date
            df_weight["Weight"] = pd.to_numeric(df_weight[weight_col], errors="coerce")
            df_recent = df_weight[
                (df_weight["Date"] >= thirteen_days_ago)
                & (df_weight["Date"] <= calc_date)
            ].dropna(subset=["Weight"])

            if len(df_recent) < MIN_WEIGH_INS_FOR_SCORE:
                return (
                    None,
                    f"Need {MIN_WEIGH_INS_FOR_SCORE}+ weigh-ins in 14 days. Have {len(df_recent)}.",
                    drivers,
                )

            first_half = df_recent[df_recent["Date"] < seven_days_ago]
            second_half = df_recent[df_recent["Date"] >= seven_days_ago]

            if first_half.empty or second_half.empty:
                return (
                    None,
                    "Need weigh-ins in both weeks of the 14-day window.",
                    drivers,
                )

            weight_delta = float(first_half["Weight"].min()) - float(
                second_half["Weight"].min()
            )
            drivers["weight_shift"] = weight_delta

        except Exception as exc:
            return None, f"Error parsing weight logs: {exc}", drivers

        # -----------------------------------------------------------------
        # 3. Score Calculation — mode-dependent weight shift scoring
        # -----------------------------------------------------------------
        score = adherence_rate * ADHERENCE_MAX_POINTS

        if is_bulk:
            # Bulk mode: weight_delta is first_half_min - second_half_min
            # Negative delta = weight GAINED (good in bulk)
            weight_gain = -weight_delta  # flip sign: positive = gained
            if BULK_WEIGHT_GAIN_SWEET_MIN <= weight_gain <= BULK_WEIGHT_GAIN_SWEET_MAX:
                # Sweet spot — full points
                score += WEIGHT_MAX_POINTS
            elif weight_gain > BULK_WEIGHT_GAIN_SWEET_MAX:
                if weight_gain >= BULK_WEIGHT_GAIN_EXCESS:
                    # Excessive gain — partial credit only
                    score += BULK_WEIGHT_SCORE_BASE
                else:
                    # Above sweet spot but not excessive — scale down
                    frac = 1.0 - (weight_gain - BULK_WEIGHT_GAIN_SWEET_MAX) / (
                        BULK_WEIGHT_GAIN_EXCESS - BULK_WEIGHT_GAIN_SWEET_MAX
                    )
                    score += BULK_WEIGHT_SCORE_BASE + frac * (WEIGHT_MAX_POINTS - BULK_WEIGHT_SCORE_BASE)
            elif 0 < weight_gain < BULK_WEIGHT_GAIN_SWEET_MIN:
                # Some gain but below sweet spot — partial credit
                frac = weight_gain / BULK_WEIGHT_GAIN_SWEET_MIN
                score += BULK_WEIGHT_SCORE_BASE + frac * (WEIGHT_MAX_POINTS - BULK_WEIGHT_SCORE_BASE)
            elif weight_gain <= 0:
                # Lost weight while bulking — not fueling growth
                if weight_gain < -0.5:
                    score -= BULK_WEIGHT_LOSS_PENALTY
                # No penalty for staying flat (0 to -0.5)
        else:
            # Cut mode: original logic — reward weight loss
            if weight_delta >= WEIGHT_LOSS_FULL_THRESHOLD:
                score += WEIGHT_MAX_POINTS
            elif weight_delta >= 0:
                score += WEIGHT_SCORE_BASE + (weight_delta * WEIGHT_SCORE_MULTIPLIER)
            elif weight_delta < -WEIGHT_GAIN_PENALTY_THRESHOLD:
                score -= WEIGHT_GAIN_PENALTY

        score = max(SCORE_MIN, min(SCORE_MAX, score))
        return score, None, drivers

    except Exception as exc:
        return None, f"System Error: {exc}", None


def sync_plan_effectiveness_logs(
    force_resync: bool = False,
    goals: "dict | None" = None,
    fasting: "dict | None" = None,
    demo_mode: bool = False,
    mode: str = DEFAULT_MODE,
) -> None:
    """Backfill and update daily plan effectiveness scores in Google Sheets.

    Parameters
    ----------
    force_resync:
        If ``True``, overwrites existing entries for the extended backfill window.
    goals:
        Pre-fetched user goals dict (required).
    fasting:
        Pre-fetched fasting schedule dict (required).
    demo_mode:
        Skip the sync when running in demo mode.
    """
    if demo_mode:
        return

    if goals is None or fasting is None:
        log.warning(
            "sync_plan_effectiveness_logs: goals and fasting are required; skipping."
        )
        return

    try:
        sh = open_sheet()

        try:
            log_ws = sh.worksheet(WS_PLAN_EFFECTIVENESS)
        except gspread.WorksheetNotFound:
            log_ws = sh.add_worksheet(
                title=WS_PLAN_EFFECTIVENESS, rows="100", cols="8"
            )
            log_ws.append_row(
                [
                    "Date",
                    "Calorie Pts",
                    "Protein Pts",
                    "Fast Timing Pts",
                    "Ad Score",
                    "Weight Shift",
                    "Plan Score",
                    "Mode",
                ]
            )

        data = log_ws.get_all_values()
        date_row_map = {row[0]: i + 1 for i, row in enumerate(data)}
        logged_dates = set(date_row_map.keys())

        now = datetime.now(EASTERN).date()
        backfill_range = (
            SCORE_FORCE_BACKFILL_DAYS if force_resync else SCORE_BACKFILL_DAYS
        )

        days_logged_this_run = 0
        for i in range(1, backfill_range + 1):
            target_date = now - timedelta(days=i)
            date_str = target_date.strftime("%Y-%m-%d")

            if date_str not in logged_dates or force_resync:
                score, msg, drivers = calculate_plan_effectiveness(
                    calc_date=target_date,
                    pre_sh=sh,
                    pre_goals=goals,
                    pre_fasting=fasting,
                    mode=mode,
                )

                if drivers:
                    daily_breakdown = drivers.get("daily_breakdown", {})
                    day_data = daily_breakdown.get(target_date, {})

                    cal_pts = day_data.get("cal_pts", 0.0)
                    prot_pts = day_data.get("prot_pts", 0.0)
                    time_pts = day_data.get("time_pts", 0.0)
                    day_score = day_data.get("day_score", 0.0)

                    day_ad_score = day_score / 2.0
                    weight_shift = drivers.get("weight_shift", 0.0)
                    final_score = score if score is not None else 0.0

                    row_content = [
                        date_str,
                        cal_pts,
                        prot_pts,
                        time_pts,
                        round(day_ad_score, 2),
                        round(weight_shift, 2),
                        round(final_score, 2),
                        mode,
                    ]

                    if date_str in date_row_map:
                        if force_resync:
                            log_ws.update(
                                f"A{date_row_map[date_str]}:H{date_row_map[date_str]}",
                                [row_content],
                            )
                    else:
                        log_ws.append_row(row_content)
                        time.sleep(SCORE_APPEND_SLEEP)

                    days_logged_this_run += 1
                    if days_logged_this_run >= SCORE_MAX_DAYS_PER_RUN:
                        break
                else:
                    log.warning("Skipped %s: %s", date_str, msg)

    except Exception as exc:
        log.exception("Sync error: %s\n%s", exc, traceback.format_exc())
