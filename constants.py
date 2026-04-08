"""Named constants for the RatioTen application.

All magic numbers and configuration values live here so that the scoring
model is self-documenting and easy to tune without hunting through app code.
"""
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
EASTERN = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------
# AI Models
# ---------------------------------------------------------------------------
PRIMARY_MODEL   = "gemini-3-flash-preview"  # Best available: frontier intelligence, fast, great banter
SECONDARY_MODEL = "gemini-2.5-flash"         # Stable proven fallback, 1K RPM paid tier
STABLE_MODEL    = "gemini-2.5-flash-lite"    # Ultra-cheap last resort ($0.10/$0.40 per 1M tokens)

# ---------------------------------------------------------------------------
# Scoring: Weight Shift Component (0–5 pts)
# ---------------------------------------------------------------------------
WEIGHT_SCORE_MULTIPLIER = 2.9       # Scales lb-loss to points; maps 0–0.9 lb drop → 2.0–4.9 pts
WEIGHT_SCORE_BASE = 2.0             # Points for any non-negative weight shift (0–0.9 lb loss)
WEIGHT_LOSS_FULL_THRESHOLD = 1.0    # Lbs lost required for the maximum weight score
WEIGHT_GAIN_PENALTY_THRESHOLD = 0.5 # Lbs gained before the penalty applies
WEIGHT_GAIN_PENALTY = 2.0           # Points deducted for excessive weight gain
WEIGHT_MAX_POINTS = 5.0             # Maximum points from the weight shift component

# ---------------------------------------------------------------------------
# Scoring: Weight Shift Component — BULK MODE (0–5 pts)
# ---------------------------------------------------------------------------
BULK_WEIGHT_GAIN_SWEET_MIN = 0.25   # Minimum weekly lbs gained for full weight score
BULK_WEIGHT_GAIN_SWEET_MAX = 1.0    # Maximum weekly lbs gained for full weight score
BULK_WEIGHT_GAIN_EXCESS = 1.5       # Lbs gained above which excessive-gain penalty applies
BULK_WEIGHT_LOSS_PENALTY = 2.0      # Points deducted for losing weight while bulking
BULK_WEIGHT_SCORE_BASE = 2.0        # Points for minimal gain (0–0.24 lbs)
BULK_WEIGHT_SCORE_MULTIPLIER = 12.0 # Scales 0.25–1.0 lb gain range to 2.0–5.0 pts

# ---------------------------------------------------------------------------
# Scoring: Daily Adherence Points (per day, out of 10)
# ---------------------------------------------------------------------------
CALORIE_TARGET_BUFFER = 100         # Extra cals allowed above target for full calorie points
CALORIE_PARTIAL_BUFFER = 200        # Extra cals above the buffer before partial credit ends
CAL_FULL_POINTS = 4.0               # Points for staying within calorie target + buffer
CAL_PARTIAL_POINTS = 2.0            # Points for a slight calorie overage
PROTEIN_FULL_POINTS = 4.0           # Points for hitting the dynamic protein floor
PROTEIN_PARTIAL_POINTS = 2.0        # Points for reaching 80 % of the protein floor
TIMING_FULL_POINTS = 2.0            # Points for all meals within the eating window
TIMING_BUFFER_HOURS = 1             # Grace-period (hours) around eating-window edges
ADHERENCE_MAX_POINTS = 5.0          # Maximum points from the adherence component

# ---------------------------------------------------------------------------
# Scoring: Daily Adherence Points — BULK MODE (per day, out of 10)
# ---------------------------------------------------------------------------
BULK_CAL_FLOOR_BUFFER = 100         # Can be under calorie target by this much for full pts
BULK_CAL_SURPLUS_MAX = 300          # Can be over calorie target by this much for full pts
BULK_CAL_FULL_POINTS = 4.0         # Points for hitting the calorie surplus range
BULK_CAL_PARTIAL_POINTS = 2.0      # Points for being close to the surplus range

# ---------------------------------------------------------------------------
# Scoring: Timing/Fasting Component — BULK MODE
# IF is a secondary tool during bulk; eating-window drift is penalised lightly.
# ---------------------------------------------------------------------------
BULK_TIMING_BUFFER_HOURS = 2        # Wider grace-period (hrs) around window edges (vs 1 in cut)
BULK_TIMING_PARTIAL_POINTS = 1.0    # Points awarded when outside even the wider buffer
                                    # (timing is never a full zero in bulk — structure is
                                    #  a convenience, not a fat-loss lever here)

# ---------------------------------------------------------------------------
# Scoring: Dynamic Protein Floor
# ---------------------------------------------------------------------------
PROTEIN_FLOOR_FULL_HOURS = 6.0      # Eating-window hours at which the full floor applies
PROTEIN_FLOOR_MIN_HOURS = 1.0       # Below this → minimum protein floor fraction
PROTEIN_FLOOR_MIN_FRACTION = 0.30   # Minimum protein floor as a fraction of the goal

# ---------------------------------------------------------------------------
# Scoring: Global
# ---------------------------------------------------------------------------
SCORE_MIN = 1.0
SCORE_MAX = 10.0
SCORE_WINDOW_DAYS = 14              # Rolling days evaluated for the effectiveness score
MIN_DAYS_FOR_SCORE = 7              # Minimum logged days required to compute a score
MIN_WEIGH_INS_FOR_SCORE = 4         # Minimum weigh-ins required in the scoring window

# ---------------------------------------------------------------------------
# Scoring: Sync Engine
# ---------------------------------------------------------------------------
SCORE_BACKFILL_DAYS = 14            # Default backfill range (days before today)
SCORE_FORCE_BACKFILL_DAYS = 21      # Extended backfill range on force-resync
SCORE_MAX_DAYS_PER_RUN = 20         # Maximum dates processed per sync run
SCORE_APPEND_SLEEP = 1.0            # Seconds to sleep between sheet appends (rate-limit guard)

# ---------------------------------------------------------------------------
# Training Modes
# ---------------------------------------------------------------------------
MODE_CUT = "cut"
MODE_BULK = "bulk"
DEFAULT_MODE = MODE_CUT

# ---------------------------------------------------------------------------
# Timeline Layout
# ---------------------------------------------------------------------------
TIMELINE_LANE_BASE_OFFSET_PX = 15   # Pixel distance from the bar to the first stagger lane
TIMELINE_LANE_HEIGHT_PX = 25        # Pixel step added per additional stagger lane

# ---------------------------------------------------------------------------
# System Prompt Cache
# ---------------------------------------------------------------------------
SYSTEM_PROMPT_TTL = 300             # Seconds to cache the system prompt (5 minutes)

# ---------------------------------------------------------------------------
# Protein Density Target
# ---------------------------------------------------------------------------
TARGET_DENSITY      = 10.0          # Minimum protein density % goal (cut)
TARGET_DENSITY_BULK =  8.0          # Minimum protein density % goal (bulk — higher
                                    # calorie denominator makes 10 % unachievable)

# ---------------------------------------------------------------------------
# Google Sheets
# ---------------------------------------------------------------------------
SPREADSHEET_NAME = "Nutrition_Logs"
WS_CHAT_HISTORY = "Chat_History"
WS_WEIGHT_LOGS = "Weight_Logs"
WS_FASTING_SCHEDULE = "Fasting_Schedule"
WS_USER_GOALS = "User_Goals"
WS_CUSTOM_INSTRUCTIONS = "Custom_Instructions"
WS_PLAN_EFFECTIVENESS = "Plan_Effectiveness_Logs"
