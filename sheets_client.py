"""Typed data structures and sheet connection helper for RatioTen.

This module provides:
- TypedDicts representing each entity stored in Google Sheets
- open_sheet() — a single place to open the shared spreadsheet

All column names and sheet names come from constants.py so a schema
change only requires editing one file.
"""
from __future__ import annotations

from typing import TypedDict

import gspread
import streamlit as st

from constants import SPREADSHEET_NAME


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class NutritionEntry(TypedDict):
    """One food-log row from the primary nutrition worksheet."""
    date: str        # "YYYY-MM-DD HH:MM:SS"
    item: str
    calories: int
    protein: int
    density: str     # e.g. "10.5%"
    week_num: str    # e.g. "2026-W06"
    emoji: str


class WeightEntry(TypedDict):
    """One row from the Weight_Logs worksheet."""
    date: str        # date string
    weight_lbs: float


class ChatMessage(TypedDict):
    """One row from the Chat_History worksheet."""
    timestamp: str
    role: str        # "user" | "assistant"
    parts_json: str  # JSON-serialised list of part dicts


class MealLogEntry(TypedDict):
    """Validated structure returned by the AI for a single logged food item."""
    item: str
    calories: int
    protein: int
    density: str     # e.g. "10.5%"
    emoji: str


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def open_sheet() -> gspread.Spreadsheet:
    """Open the shared RatioTen spreadsheet using service-account credentials."""
    credentials_dict = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(credentials_dict)
    return gc.open(SPREADSHEET_NAME)
