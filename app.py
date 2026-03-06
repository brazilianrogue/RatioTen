import streamlit as st
import streamlit.components.v1 as components
from google import genai
import gspread
import pandas as pd
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re
import time
import persona

EASTERN = ZoneInfo("America/New_York")
PRIMARY_MODEL = "gemini-3-pro-preview"
SECONDARY_MODEL = "gemini-2.5-flash"
STABLE_MODEL = "gemini-2.0-flash"

# --- 1. Page Configuration & Custom CSS ---
st.set_page_config(page_title="RatioTen", page_icon="🔟", layout="centered")

# --- Initialize Session State ---
if "messages" not in st.session_state:
    st.session_state.messages = []

if "current_model" not in st.session_state:
    st.session_state.current_model = PRIMARY_MODEL

if "show_camera" not in st.session_state:
    st.session_state.show_camera = False

if "pending_image" not in st.session_state:
    st.session_state.pending_image = None

if "view_selection" not in st.session_state:
    st.session_state.view_selection = "🍽️ Log"

if "show_timeline_always" not in st.session_state:
    st.session_state.show_timeline_always = False

# --- Function Definitions ---
@st.cache_resource
def get_chat_session(model_id, system_prompt, history=None):
    # Determine the best thinking configuration for the model
    config_params = {"system_instruction": system_prompt}
    
    # gemini-3 and gemini-2.5 support thinking configs in this environment
    if "2.5" in model_id or "3" in model_id:
        try:
            config_params["thinking_config"] = genai.types.ThinkingConfig(include_thoughts=True)
        except:
            # Fallback for unexpected SDK versioning
            pass

    config = genai.types.GenerateContentConfig(**config_params)
    return client.chats.create(
        model=model_id,
        config=config,
        history=history
    )

st.markdown("""
<style>
    /* Hide the Streamlit Header completely */
    header[data-testid="stHeader"], [data-testid="stHeader"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
    }
    
    /* Reduce the default Streamlit padding for better vertical use */
    .block-container {
        padding-top: 1px !important; /* Hug the top as requested */
        padding-bottom: 0rem !important;
        margin-top: 0px !important;
    }

    /* Target the gaps between blocks */
    div[data-testid="stVerticalBlock"] {
        gap: 5px !important; /* Tighter 5px gap as requested */
    }

    /* Specific override for the custom nav container spacing */
    div[data-testid="stVerticalBlock"] > div:has(div[data-testid="stHtml"]) {
        margin-bottom: 0px !important;
        margin-top: 0px !important;
    }

    /* Robustly hide the H_ bridge buttons and their containers */
    div[data-testid="stBaseButton-secondary"]:has(p:contains("H_")),
    div[data-testid="stHorizontalBlock"] > div:has(button p:contains("H_")),
    div.stButton:has(button p:contains("H_")),
    button[kind="secondary"]:has(p:contains("H_")) {
        display: none !important;
        height: 0 !important;
        width: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        visibility: hidden !important;
    }
    div[data-testid="stVerticalBlock"] > div:has(button p:contains("H_")) {
        display: none !important;
    }
    
    .main {
        background-color: #0e1117;
    }
    div[data-testid="stExpander"] {
        border: none !important;
        box-shadow: none !important;
        background-color: transparent !important;
    }
    .stButton button {
        border-radius: 20px;
        font-weight: 600;
    }
    /* Custom Dashboard CSS */
    .metric-container {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 20px;
    }
    .metric-card {
        background-color: #31333F;
        color: white;
        padding: 15px 10px;
        border-radius: 10px;
        flex: 1;
        min-width: 0;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        justify-content: flex-start;
        min-height: 110px;
    }
    .metric-label {
        font-size: 0.75rem;
        color: #e0e0e0;
        margin-bottom: 4px;
        white-space: nowrap;
        width: 100%;
    }
    .metric-value {
        font-size: 1.2rem;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .metric-delta {
        font-size: 0.7rem;
        padding: 2px 6px;
        border-radius: 4px;
        font-weight: 600;
    }
    .delta-green { background-color: rgba(0, 166, 255, 0.2); color: #00A6FF; }
    .delta-red { background-color: rgba(220, 53, 69, 0.2); color: #dc3545; }
    
    /* Timeline styles */
    .timeline-wrapper {
        width: 100%;
        padding: 60px 0 60px 0; /* Balanced padding for bimodal tracks */
        margin-bottom: 20px;
        position: relative;
    }
    .timeline-bar {
        height: 18px; /* Thicker bar to house labels */
        background: rgba(255, 255, 255, 0.08);
        border-radius: 9px;
        position: relative;
        width: 100%;
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0 10px;
        box-sizing: border-box;
        border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .timeline-progress {
        position: absolute;
        top: 0;
        left: 0;
        height: 100%;
        background: linear-gradient(90deg, rgba(0, 166, 255, 0.3), rgba(0, 255, 204, 0.3));
        border-radius: 9px;
        z-index: 1;
    }
    .timeline-marker {
        position: absolute;
        top: 50%;
        transform: translate(-50%, -50%);
        width: 12px;
        height: 24px;
        background: #00A6FF;
        border-radius: 2px;
        box-shadow: 0 0 10px rgba(0, 166, 255, 0.6);
        z-index: 10;
        border: 1px solid white;
    }
    .timeline-emoji {
        position: absolute;
        font-size: 0.95rem; /* ~25% smaller */
        cursor: help;
        transition: all 0.2s cubic-bezier(0.175, 0.885, 0.32, 1.275);
        z-index: 5;
        display: flex;
        flex-direction: column;
        align-items: center;
        white-space: nowrap;
    }
    .timeline-emoji-cluster-badge {
        position: absolute;
        top: -6px;
        right: -8px;
        background: #fca311;
        color: #161821;
        font-size: 0.6rem;
        padding: 1px 4px;
        border-radius: 6px;
        font-weight: 800;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }
    .timeline-stem {
        width: 1px;
        background: rgba(255, 255, 255, 0.2);
        transition: all 0.2s;
    }
    .timeline-emoji:hover {
        transform: scale(1.4) !important;
        z-index: 30;
    }
    .timeline-emoji:hover .timeline-stem {
        background: #00A6FF;
        width: 2px;
    }
    .timeline-bar-label {
        font-size: 0.65rem;
        color: rgba(255, 255, 255, 0.4);
        font-family: monospace;
        z-index: 2;
        pointer-events: none;
    }
    
    /* Section Header Styles */
    .section-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-top: 25px;
        margin-bottom: 15px;
        padding-bottom: 8px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    }
    .section-header svg {
        color: #fca311; /* Tixx yellow */
        width: 22px;
        height: 22px;
    }
    .section-header span {
        color: white;
        font-size: 1.1rem;
        font-weight: 700;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }
    </style>
    """, unsafe_allow_html=True)

def render_section_header(icon_svg, title):
    """Renders a premium, consistent section header."""
    header_html = f"""
    <div class="section-header">
        {icon_svg}
        <span>{title}</span>
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)

# Inject theme color and status bar style into the head of the main document
components.html("""
    <script>
        (function() {
            function applyExtremePWATheme() {
                try {
                    // Climb as high as possible in the DOM hierarchy
                    let current = window;
                    const docs = [];
                    while (current !== current.parent && docs.length < 5) {
                        docs.push(current.document);
                        current = current.parent;
                    }
                    docs.push(current.document); // Add the top-most document

                    docs.forEach(doc => {
                        // 1. Viewport Fit - Key for status bar blending on iPhone
                        let viewport = doc.querySelector('meta[name="viewport"]');
                        if (viewport) {
                            let content = viewport.getAttribute('content') || "";
                            if (!content.includes('viewport-fit=cover')) {
                                viewport.setAttribute('content', content + (content ? ',' : '') + 'viewport-fit=cover');
                            }
                        }

                        // 2. Apple PWA capabilities
                        const metas = {
                            'apple-mobile-web-app-capable': 'yes',
                            'apple-mobile-web-app-status-bar-style': 'black-translucent', // translucent + dark body usually works best
                            'theme-color': '#161821',
                            'mobile-web-app-capable': 'yes'
                        };

                        for (let [name, val] of Object.entries(metas)) {
                            let m = doc.querySelector(`meta[name="${name}"]`);
                            if (!m) {
                                m = doc.createElement('meta');
                                m.name = name;
                                doc.head.appendChild(m);
                            }
                            m.content = val;
                        }

                        // 3. Force Styles on the shell
                        if (doc.body) {
                            doc.body.style.setProperty('background-color', '#161821', 'important');
                        }
                        if (doc.documentElement) {
                            doc.documentElement.style.setProperty('background-color', '#161821', 'important');
                        }
                    });
                } catch (e) {
                    console.log("PWA theme override attempt:", e);
                }
            }

            // Continuous reinforcement for the first few seconds
            applyExtremePWATheme();
            let timer = 0;
            const iv = setInterval(() => {
                applyExtremePWATheme();
                if (timer++ > 10) clearInterval(iv);
            }, 1000);
        })();
    </script>
    """, height=0, width=0)

# --- 2. API Setup ---
@st.cache_resource
def get_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

client = get_client()

def render_timeline_html(start_time_str, end_time_str, logs, progress_pct=None, title=None):
    """
    Renders a bimodal, clustered timeline with reduced emoji sizes.
    """
    try:
        ref_date = datetime.now(EASTERN).date()
        start_dt = datetime.combine(ref_date, datetime.strptime(start_time_str, "%H:%M").time()).replace(tzinfo=EASTERN)
        end_dt = datetime.combine(ref_date, datetime.strptime(end_time_str, "%H:%M").time()).replace(tzinfo=EASTERN)
        total_duration = (end_dt - start_dt).total_seconds()

        # 1. Smart Clustering (15-minute window)
        sorted_logs = sorted(logs, key=lambda x: x["timestamp"])
        clusters = [] # List of { "pos": float, "emojis": [], "items": [] }
        
        for log in sorted_logs:
            log_ts = log["timestamp"].replace(tzinfo=EASTERN)
            log_norm = datetime.combine(ref_date, log_ts.time()).replace(tzinfo=EASTERN)
            
            # Boundary logic
            one_hour = timedelta(hours=1)
            is_valid = False
            pos = 0.0
            
            if start_dt <= log_norm <= end_dt:
                is_valid = True
                pos = ((log_norm - start_dt).total_seconds() / total_duration) * 100
            elif (start_dt - one_hour) <= log_norm < start_dt:
                is_valid = True
                pos = 0.0
            elif end_dt < log_norm <= (end_dt + one_hour):
                is_valid = True
                pos = 100.0
                
            if is_valid:
                pos = max(0.0, min(100.0, pos))
                # Check if it fits in recent cluster
                if clusters and abs(pos - clusters[-1]["pos"]) < 3.0: # ~10-15 min window depending on total dur
                    clusters[-1]["emojis"].append(log["emoji"])
                    clusters[-1]["items"].append(log["item"])
                else:
                    clusters.append({
                        "pos": pos, 
                        "emojis": [log["emoji"]], 
                        "items": [log["item"]]
                    })

        # 2. Bimodal & Lane Rendering
        emoji_markers = ""
        lanes_top = []
        lanes_bottom = []
        
        for i, cluster in enumerate(clusters):
            pos = cluster["pos"]
            primary_emoji = cluster["emojis"][0]
            count = len(cluster["emojis"])
            display_title = " / ".join(cluster["items"])
            
            # Alternate top/bottom
            side = "top" if i % 2 == 0 else "bottom"
            lanes = lanes_top if side == "top" else lanes_bottom
            
            # Lane staggering within the side
            lane_idx = 0
            threshold = 10.0 # Cluster width threshold
            for l_i, right_edge in enumerate(lanes):
                if pos > right_edge + threshold:
                    lane_idx = l_i
                    lanes[l_i] = pos
                    break
            else:
                lane_idx = len(lanes)
                lanes.append(pos)
                
            # Positioning
            badge_html = f'<div class="timeline-emoji-cluster-badge">+{count-1}</div>' if count > 1 else ""
            
            # Vertical math
            # lane 0: 15px from bar, lane 1: 40px from bar
            offset_val = 15 + (lane_idx * 25)
            
            if side == "top":
                stem_html = f'<div class="timeline-stem" style="height: {offset_val}px; margin-top: 2px;"></div>'
                transform = "translate(-50%, -100%)"
                top_style = f"bottom: 100%; margin-bottom: 0px;"
                content = f"<div>{primary_emoji}{badge_html}</div>{stem_html}"
            else:
                stem_html = f'<div class="timeline-stem" style="height: {offset_val}px; margin-bottom: 2px;"></div>'
                transform = "translate(-50%, 0)"
                top_style = f"top: 100%; margin-top: 0px;"
                content = f"{stem_html}<div>{primary_emoji}{badge_html}</div>"

            # Edge adjustments
            left_style = f"{pos:.1f}%"
            final_transform = transform
            if pos < 5:
                left_style = "0%"
                final_transform = transform.replace("-50%", "0")
            elif pos > 95:
                left_style = "100%"
                final_transform = transform.replace("-50%", "-100%")

            group_style = f"left: {left_style}; {top_style} transform: {final_transform};"
            emoji_markers += f'<div class="timeline-emoji" style="{group_style}" title="{display_title}">{content}</div>'
        
        marker_html = ""
        if progress_pct is not None:
            marker_html = f'<div class="timeline-progress" style="width: {progress_pct:.1f}%;"></div><div class="timeline-marker" style="left: {progress_pct:.1f}%;"></div>'

        header_html = f'<div style="font-size: 0.85rem; color: #00A6FF; margin-bottom: 8px; font-weight: 700;">{title}</div>' if title else ""

        html = f"""<div style="margin-top: 20px; margin-bottom: 20px;">{header_html}<div class="timeline-wrapper"><div class="timeline-bar"><div class="timeline-bar-label">{start_time_str}</div><div class="timeline-bar-label">{end_time_str}</div>{marker_html}{emoji_markers}</div></div></div>"""
        return html.strip()
    except Exception as e:
        import traceback
        return f"<!-- Timeline Error: {e} \n {traceback.format_exc()} -->"

# --- Database Setup ---
@st.cache_data(ttl=600)
def get_trailing_7_days_data():
    # Define expected columns
    expected_cols = ["Date", "Item", "Calories", "Protein", "Density"]
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.sheet1
        
        values = worksheet.get_all_values()
        if not values:
            return pd.DataFrame(columns=expected_cols)
            
        df = pd.DataFrame(values)
        if df.iloc[0, 0] in ["Date", "date", "Time", "timestamp", "today"]:
            df.columns = df.iloc[0]
            df = df[1:]
        else:
            df.columns = expected_cols
            
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.date
        df['Calories'] = pd.to_numeric(df['Calories'], errors='coerce').fillna(0)
        df['Protein'] = pd.to_numeric(df['Protein'], errors='coerce').fillna(0)
        
        seven_days_ago = (datetime.now(EASTERN) - timedelta(days=7)).date()
        df = df[df['Date'] >= seven_days_ago]
        
        if df.empty:
            return pd.DataFrame(columns=expected_cols)
            
        daily_summary = df.groupby('Date')[['Calories', 'Protein']].sum().reset_index()
        daily_summary['Date'] = daily_summary['Date'].astype(str)
        daily_summary['Density'] = (daily_summary['Protein'] / daily_summary['Calories']) * 100
        daily_summary['Density'] = daily_summary['Density'].fillna(0).apply(lambda x: f"{x:.1f}%")
        
        daily_summary = daily_summary.sort_values(by='Date', ascending=False)
        
        # Ensure all expected columns are present
        for col in expected_cols:
            if col not in daily_summary.columns:
                daily_summary[col] = 0 if col in ["Calories", "Protein"] else "0.0%" if col == "Density" else ""

        return daily_summary
        
    except Exception as e:
        st.error(f"Error fetching logs: {e}")
        return pd.DataFrame(columns=expected_cols)

@st.cache_data(ttl=60)
def get_today_log_for_timeline():
    return get_logs_for_history(days=0)

@st.cache_data(ttl=300)
def get_logs_for_history(days=10):
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.sheet1
        
        values = worksheet.get_all_values()
        if len(values) <= 1:
            return [] if days == 0 else {}
            
        now = datetime.now(EASTERN)
        cutoff_date = (now - timedelta(days=days)).date()
        
        logs_by_date = {}
        
        for row in values[1:]:
            if not isinstance(row, (list, tuple)) or len(row) < 2: continue
            ts_str = str(row[0])
            try:
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                log_date = ts.date()
                
                if log_date >= cutoff_date:
                    date_key = log_date.strftime("%Y-%m-%d")
                    if date_key not in logs_by_date:
                        logs_by_date[date_key] = []
                    
                    item = str(row[1])
                    emoji = str(row[6]).strip() if len(row) > 6 and str(row[6]).strip() else "🍽️"
                    logs_by_date[date_key].append({"timestamp": ts, "item": item, "emoji": emoji})
            except:
                continue
        
        if days == 0:
            today_key = now.strftime("%Y-%m-%d")
            return logs_by_date.get(today_key, [])
            
        return logs_by_date
    except:
        return [] if days == 0 else {}

@st.cache_data(ttl=600)
def get_wow_data(enable_demo=False):
    expected_cols = ["Week", "Avg Calories", "Avg Protein", "Density"]
    if enable_demo:
        # Generate 4 weeks of demo data
        return pd.DataFrame([
            {"Week": "2026-W06", "Avg Calories": 1420, "Avg Protein": 155, "Density": "10.9%"},
            {"Week": "2026-W07", "Avg Calories": 1650, "Avg Protein": 140, "Density": "8.5%"},
            {"Week": "2026-W08", "Avg Calories": 1480, "Avg Protein": 160, "Density": "10.8%"},
            {"Week": "2026-W09", "Avg Calories": 1390, "Avg Protein": 165, "Density": "11.9%"}
        ])

    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.sheet1
        
        values = worksheet.get_all_values()
        if len(values) <= 1:
            return pd.DataFrame(columns=expected_cols)
            
        df = pd.DataFrame(values[1:])
        headers = values[0]
        # Map headers flexible to indices
        col_map = {h: i for i, h in enumerate(headers)}
        
        # We need Date, Calories, Protein, and optionally Week Num
        # If Week Num missing, calculate it
        processed_data = []
        for row in values[1:]:
            try:
                date_str = row[col_map.get("Date", 0)]
                dt = pd.to_datetime(date_str)
                cals = pd.to_numeric(row[col_map.get("Calories", 2)], errors='coerce') or 0
                prot = pd.to_numeric(row[col_map.get("Protein", 3)], errors='coerce') or 0
                
                # Week Num handling
                if "Week Num" in col_map and len(row) > col_map["Week Num"]:
                    wn = row[col_map["Week Num"]]
                else:
                    year, week, _ = dt.isocalendar()
                    wn = f"{year}-W{week:02d}"
                
                processed_data.append({"Date": dt.date(), "Week": wn, "Calories": cals, "Protein": prot})
            except:
                continue
        
        if not processed_data:
            return pd.DataFrame(columns=expected_cols)
            
        df_proc = pd.DataFrame(processed_data)
        
        # 1. Group by Date and Week to get DAILY totals first
        daily_totals = df_proc.groupby(['Week', 'Date'])[['Calories', 'Protein']].sum().reset_index()
        
        # 2. Group by Week to get AVERAGE daily totals
        weekly_summary = daily_totals.groupby('Week')[['Calories', 'Protein']].mean().reset_index()
        weekly_summary.columns = ["Week", "Avg Calories", "Avg Protein"]
        
        # Calculate Weekly Density
        weekly_summary['Density'] = (weekly_summary['Avg Protein'] / weekly_summary['Avg Calories']) * 100
        weekly_summary['Density_Val'] = weekly_summary['Density'].fillna(0)
        weekly_summary['Density'] = weekly_summary['Density_Val'].apply(lambda x: f"{x:.1f}%")
        
        return weekly_summary.sort_values('Week', ascending=True)
        
    except Exception as e:
        return pd.DataFrame(columns=expected_cols)

@st.cache_data(ttl=3600)
def get_lowest_weight():
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet("Weight_Logs")
        except gspread.WorksheetNotFound:
            return None
            
        data = worksheet.get_all_records()
        if not data:
            return None
            
        # Extract numeric weight from column B (Weight (lbs))
        weights = [float(row.get("Weight (lbs)", 999)) for row in data]
        return min(weights) if weights else None
    except Exception as e:
        return None

@st.cache_data(ttl=600)
def get_fasting_schedule():
    default_schedule = {
        "Monday": {"start": None, "end": None},
        "Tuesday": {"start": "12:00", "end": "18:00"},
        "Wednesday": {"start": "12:00", "end": "18:00"},
        "Thursday": {"start": "12:00", "end": "18:00"},
        "Friday": {"start": "18:00", "end": "19:00"},
        "Saturday": {"start": "12:00", "end": "18:00"},
        "Sunday": {"start": "12:00", "end": "18:00"}
    }
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet("Fasting_Schedule")
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title="Fasting_Schedule", rows="10", cols="3")
            worksheet.append_row(["DayOfWeek", "WindowStart", "WindowEnd"])
            for day, times in default_schedule.items():
                worksheet.append_row([day, times["start"] or "Skip", times["end"] or "Skip"])
            return default_schedule
            
        data = worksheet.get_all_records()
        if not data: return default_schedule
            
        schedule = {}
        for row in data:
            day = row.get("DayOfWeek")
            start = str(row.get("WindowStart", "")).strip()
            end = str(row.get("WindowEnd", "")).strip()
            if start.lower() in ["skip", "none", ""]: start = None
            if end.lower() in ["skip", "none", ""]: end = None
            schedule[day] = {"start": start, "end": end}
        return schedule
    except Exception as e:
        return default_schedule

def get_fasting_status(schedule):
    now = datetime.now(EASTERN)
    day_name = now.strftime("%A")
    today_sched = schedule.get(day_name, {"start": None, "end": None})
    
    if today_sched["start"] and today_sched["end"]:
        try:
            start_time = datetime.strptime(today_sched["start"], "%H:%M").time()
            end_time = datetime.strptime(today_sched["end"], "%H:%M").time()
            current_time = now.time()
            
            if start_time <= current_time < end_time:
                end_dt = datetime.combine(now.date(), end_time, tzinfo=EASTERN)
                return "Eating Window Active", end_dt.timestamp() * 1000
        except:
            pass

    for i in range(8):
        check_date = now + timedelta(days=i)
        check_day = check_date.strftime("%A")
        sched = schedule.get(check_day, {"start": None, "end": None})
        if sched["start"]:
            try:
                start_time = datetime.strptime(sched["start"], "%H:%M").time()
                start_dt = datetime.combine(check_date.date(), start_time, tzinfo=EASTERN)
                if start_dt > now:
                    return "Fasting Active", start_dt.timestamp() * 1000
            except:
                continue
    
    return "No Schedule", None

def log_to_sheet(item, calories, protein, density, emoji="🍽️"):
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.sheet1
        
        now = datetime.now(EASTERN)
        today = now.strftime("%Y-%m-%d %H:%M:%S")
        
        # Calculate Week Num (ISO Week: Monday to Sunday)
        year, week, _ = now.isocalendar()
        week_num = f"{year}-W{week:02d}"
        
        worksheet.append_row([today, item, calories, protein, density, week_num, emoji])
        get_trailing_7_days_data.clear()
        return True
    except Exception as e:
        st.error(f"Failed to log to database: {e}")
        return False

# --- Chat Persistence Helpers ---
CHAT_HISTORY_WS = "Chat_History"

@st.cache_data(ttl=600)
def get_persistent_chat():
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet(CHAT_HISTORY_WS)
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title=CHAT_HISTORY_WS, rows="1000", cols="3")
            worksheet.append_row(["Timestamp", "Role", "Parts"])
            return []
            
        data = worksheet.get_all_values()
        if len(data) <= 1:
            return []
            
        rows = data[1:]
        history = []
        for row in rows[-30:]: # Last 30 messages for context
            if not isinstance(row, (list, tuple)) or len(row) < 3: continue
            ts = str(row[0])
            role = str(row[1])
            parts_json = str(row[2])
            try:
                parts = json.loads(parts_json)
                content = [p.get("text", "") for p in parts]
                history.append({"role": role, "content": content})
            except:
                continue
        return history
    except Exception as e:
        return []

def log_chat_to_sheet(role, content):
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet(CHAT_HISTORY_WS)
        except:
            return
            
        if isinstance(content, str):
            parts = [{"text": content}]
        else:
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append({"text": item})
                else:
                    parts.append({"text": "📷 *Photo attached*"})
                    
        today = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
        worksheet.append_row([today, role, json.dumps(parts)])
        get_persistent_chat.clear()
    except:
        pass

def clear_persistent_chat():
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.worksheet(CHAT_HISTORY_WS)
        worksheet.clear()
        worksheet.append_row(["Timestamp", "Role", "Parts"])
        get_persistent_chat.clear()
        return True
    except:
        return False

@st.cache_data(ttl=600)
def get_custom_instructions():
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet("Custom_Instructions")
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title="Custom_Instructions", rows="100", cols="2")
            worksheet.append_row(["Label", "Instructions"])
            worksheet.append_row(["Static Food Items", "Add specific food data here to persist forever."])
            return ""
            
        data = worksheet.get_all_values()
        if len(data) <= 1: return ""
        
        instructions = []
        for row in data[1:]:
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                label = str(row[0])
                content = str(row[1])
                if content.strip():
                    instructions.append(f"### {label}\n{content}")
        return "\n\n".join(instructions)
    except:
        return ""

def save_user_goals(calories, protein):
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet("User_Goals")
        except:
            worksheet = sh.add_worksheet(title="User_Goals", rows="10", cols="2")
        worksheet.clear()
        worksheet.append_row(["Metric", "Value"])
        worksheet.append_row(["Calories", calories])
        worksheet.append_row(["Protein", protein])
        get_user_goals.clear()
        return True
    except:
        return False

def save_fasting_schedule(schedule_dict):
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet("Fasting_Schedule")
        except:
            worksheet = sh.add_worksheet(title="Fasting_Schedule", rows="10", cols="3")
        worksheet.clear()
        worksheet.append_row(["DayOfWeek", "WindowStart", "WindowEnd"])
        for day, times in schedule_dict.items():
            start_val = times["start"] if times["start"] else "Skip"
            end_val = times["end"] if times["end"] else "Skip"
            worksheet.append_row([day, start_val, end_val])
        get_fasting_schedule.clear()
        return True
    except:
        return False

@st.cache_data(ttl=600)
def get_user_goals():
    default_goals = {"calories": 1500, "protein": 150}
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        try:
            worksheet = sh.worksheet("User_Goals")
        except gspread.WorksheetNotFound:
            worksheet = sh.add_worksheet(title="User_Goals", rows="10", cols="2")
            worksheet.append_row(["Metric", "Value"])
            worksheet.append_row(["Calories", default_goals["calories"]])
            worksheet.append_row(["Protein", default_goals["protein"]])
            return default_goals
            
        data = worksheet.get_all_records()
        if not data: return default_goals
            
        goals = {"calories": default_goals["calories"], "protein": default_goals["protein"]}
        for row in data:
            metric = str(row.get("Metric", "")).strip().lower()
            val = row.get("Value", 0)
            if metric == "calories":
                goals["calories"] = int(val)
            elif metric == "protein":
                goals["protein"] = int(val)
        
        return {**default_goals, **goals}
    except Exception as e:
        return default_goals

@st.cache_data(ttl=3600)
def calculate_plan_effectiveness():
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        
        # 1. Get User Goals
        goals = get_user_goals()
        target_density = 10.0
        
        # 2. Get last 14 days of food logs for adherence
        try:
            food_ws = sh.sheet1
            food_values = food_ws.get_all_values()
            expected_cols = ["Date", "Item", "Calories", "Protein", "Density"]
            df_food = pd.DataFrame(food_values)
            if df_food.iloc[0, 0] in ["Date", "date", "Time", "timestamp", "today"]:
                df_food.columns = df_food.iloc[0]
                df_food = df_food[1:]
            else:
                df_food.columns = expected_cols
                
            df_food['Date'] = pd.to_datetime(df_food['Date'], errors='coerce').dt.date
            df_food['Calories'] = pd.to_numeric(df_food['Calories'], errors='coerce').fillna(0)
            df_food['Protein'] = pd.to_numeric(df_food['Protein'], errors='coerce').fillna(0)
            
            fourteen_days_ago = (datetime.now(EASTERN) - timedelta(days=14)).date()
            seven_days_ago = (datetime.now(EASTERN) - timedelta(days=7)).date()
            
            df_food = df_food[df_food['Date'] >= fourteen_days_ago]
            if df_food.empty:
                return None, "Insufficient food log data (need logs from last 14 days)."
                
            daily_summary = df_food.groupby('Date')[['Calories', 'Protein']].sum().reset_index()
            daily_summary['Density'] = (daily_summary['Protein'] / daily_summary['Calories']) * 100
            
            # Adherence Check: Count days where density >= 10.0 and calories <= target + 100
            adherent_days = 0
            target_cal_limit = int(goals.get('calories', 1500)) + 100
            for _, row in daily_summary.iterrows():
                if float(row['Density']) >= float(target_density) and int(row['Calories']) <= target_cal_limit:
                    adherent_days += 1
            
            total_days_logged = int(len(daily_summary))
            if total_days_logged < 7:
                return None, "Insufficient food log data (need at least 7 days of logs)."
                
            adherence_rate = float(adherent_days) / float(total_days_logged)
            
        except Exception as e:
            return None, "Error parsing food logs."

        # 3. Get last 14 days of weight logs
        try:
            weight_ws = sh.worksheet("Weight_Logs")
            weight_records = weight_ws.get_all_records()
            if not weight_records:
                return None, "No weight data found."
                
            df_weight = pd.DataFrame(weight_records)
            df_weight['Date'] = pd.to_datetime(df_weight['Date'], errors='coerce').dt.date
            df_weight['Weight'] = pd.to_numeric(df_weight['Weight (lbs)'], errors='coerce')
            
            df_recent = df_weight[df_weight['Date'] >= fourteen_days_ago]
            if len(df_recent) < 4:
                return None, "Insufficient weight data (need at least 4 weigh-ins in last 14 days)."
                
            # Delta between first 7 days min and last 7 days min
            first_half = df_recent[df_recent['Date'] < seven_days_ago]
            second_half = df_recent[df_recent['Date'] >= seven_days_ago]
            
            if first_half.empty or second_half.empty:
               return None, "Insufficient weight distribution (need weigh-ins in both weeks)."
               
            w1_min = first_half['Weight'].min()
            w2_min = second_half['Weight'].min()
            weight_delta = w1_min - w2_min # Positive means weight loss
            
        except Exception as e:
            return None, "Error parsing weight logs."

        # 4. Calculate Score 
        # Base score on adherence (0 to 5 pts)
        score = adherence_rate * 5.0
        
        # Add points for weight loss (up to 5 pts)
        # If weight loss is >= 1.0 lbs, give full 5 pts. If 0 lbs, give 2 points (maintenance). If gained, 0 pts.
        if weight_delta >= 1.0:
            score += 5.0
        elif weight_delta >= 0:
            score += 2.0 + (weight_delta * 3.0) # Scale between 0 and 1
        elif weight_delta < -0.5:
             # subtract points for significant gain
             score -= 2.0
             
        score = max(1.0, min(10.0, score))
        return score, None
        
    except Exception as e:
        return None, f"Calculation error: {e}"

# --- 3. System Prompt (The Rules Engine) ---
def get_system_prompt(schedule, goals, custom_instructions="", today_stats=None, weekly_summary=None):
    now = datetime.now(EASTERN)
    current_day = now.strftime("%A")
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%I:%M %p")
    
    time_awareness = f"""
### CURRENT TIME AWARENESS:
- **Today is:** {current_day}, {current_date}
- **Current Time:** {current_time} (Eastern Time)

*Note: Use this time for strategic planning and calculating remaining windows, but do not over-index on exact minutes for fasting compliance (flexibility of a few minutes is expected).*
"""

    formatted_schedule = "\n".join([f"- {day}: {times['start']} to {times['end']}" if times['start'] else f"- {day}: Fasting / Skip" for day, times in schedule.items()])
    
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

    weekly_context = ""
    if weekly_summary is not None and not weekly_summary.empty:
        weekly_context = f"\n### ROLLING 7-DAY TREND:\n{weekly_summary.to_markdown(index=False)}"

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
- For example: an item with 150 calories and 30g protein has a density of 30 / 150 = 0.20 = 20.0%.

{stats_context}
{weekly_context}

Fasting Protocol Strict Adherence:
Here is the user's current fasting schedule (UTC-5 Eastern Time):
{formatted_schedule}

Multimodal Capabilities (Image Analysis):
- You can identify food items and estimate portion sizes from images.
- Nutritional Labels: When a nutritional label is provided in an image, you MUST parse the calories and protein from the label. These values should ALWAYS be used as the source of truth, rather than an estimated or researched value.
- User Adjustments: Always take into account additional user input that might adjust the value (e.g., "Ate half of this" means you should divide the label's total calories and protein by two).
- Assistance in Calorie Reduction: Identify individual parts of the meal that can be left uneaten or removed to help reach daily goals or stay within daily limits. Suggest what to leave behind to optimize macro ratios.

Formatting Constraints (Mobile Optimized):
- Use standard, properly formatted Markdown tables (which Streamlit renders as nice HTML tables). Do not use raw ASCII formatting.
- The "Density" column must always be displayed as a percentage with exactly one decimal place (e.g., 11.5%, 5.0%, 12.3%).
- NEVER include Date or Date-Range columns in visible output (track silently).
- When logging food, display ONLY ONE table with the items, but you MUST also include conversational banter.
  1. Current Day's Items: (Item Name, Cals, Protein, Density)

Daily Targets & Banter (REQUIRED):
- Goal: <= {goals['calories']} Calories, >= {goals['protein']}g Protein, Density Target: >= 10.0%.
- Below the data table, you MUST evaluate each logged item and the overall progress for the day against these targets.
- Use "Shred Language" and maintain the persona in your evaluation.
- Ending: Always end with a "Verdict" or "Strategy" for the next meal. Always look for the "next play."


Daily 6:00 PM Wrap-Up (Creatine Check):
- Check logs for "protein shake" or "ultra-filtered shake".
- If present: Assume creatine was taken. No reminder.
- If missing: Remind me to "clear the supplement" (Creatine Watchdog) to maintain saturation.

### CREATIVE EMOJI SELECTION PROTOCOL (CRITICAL):
For every logged item, you must select one or more emojis that represent the "core essence" of the food.
- **Rule 1: Abstract Reasoning**: Do not just look for a literal match. If it's a "Marinated Mozzarella", use 🧀. If it's "Aji Verde Turkey", use 🍗🌿.
- **Rule 2: Forbidden Generic**: NEVER use the generic plate/cutlery emoji (🍽️, 🍴, 🍳) for identifiable meals. Use the most specific icons available.
- **Rule 3: Ingredient Decomposition**: If a meal is complex, use emojis for the primary protein and a defining characteristic (e.g., "Spicy Beef Bowl" -> 🥩🌶️).
- **Rule 4: Shake/Supplement Logic**: Always use 🥤 or 🥛 for shakes. Use 💊 for vitamins/supplements.

### CALIBRATION EXAMPLES:
- "Double Espresso" -> ☕⚡
- "Core Power Protein Shake" -> 🥤💪
- "Factor Marinated Mozzarella" -> 🧀🥗
- "Thin Sliced Salami and Provolone" -> 🍖🧀
- "Aji Verde Turkey" -> 🍗🌿
- "The Usual (Vitamins)" -> 💊🥛
- "Sushi Sashimi" -> 🍣🍱
- "Egg White Bites" -> 🥚🍳

JSON Output for Database Logging:
- When the user logs food items, you MUST append a JSON block at the very end of your response containing a list of objects for EACH item logged.
- The "emoji" field must strictly follow the Creative Emoji Selection Protocol above.
- Format exactly like this:
```json
[
  {{"item": "Food Name", "calories": 150, "protein": 30, "density": "20.0%", "emoji": "🍗🌿"}}
]
```
- Only include the JSON block if new food is being logged.
"""

fasting_schedule = get_fasting_schedule()
custom_instructions = get_custom_instructions()
user_goals = get_user_goals()
SYSTEM_PROMPT = get_system_prompt(fasting_schedule, user_goals, custom_instructions)

# --- 4. Top Navigation (Custom UI) ---

if "view_selection" not in st.session_state:
    st.session_state.view_selection = "🍽️ Log"
current_view = st.session_state.view_selection

def get_image_base64(path):
    try:
        with open(path, "rb") as image_file:
            return f"data:image/png;base64,{base64.b64encode(image_file.read()).decode()}"
    except:
        return ""
logo_b64 = get_image_base64("modern_ratioten_logo.png")

nav_html = f"""
<!DOCTYPE html>
<html>
<head>
<style>
  body {{
    margin: 0;
    padding: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background-color: transparent;
  }}
  .nav-bar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    background-color: #161821; /* Dark Tixx like */
    border-radius: 12px;
    padding: 0 20px;
    height: 70px;
    box-shadow: 0 4px 6px rgba(0,0,0,0.3);
  }}
  .nav-left {{
    display: flex;
    align-items: center;
  }}
  .nav-left img {{
    height: 32px;
    margin-right: 12px;
  }}
  .nav-left span {{
    color: white;
    font-weight: bold;
    font-size: 18px;
    letter-spacing: 0.5px;
  }}
  .nav-items {{
    display: flex;
    gap: 30px; 
  }}
  .nav-item {{
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    color: #6a6a8a;
    text-decoration: none;
    cursor: pointer;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    transition: all 0.2s;
    height: 70px;
    border-bottom: 3px solid transparent;
    box-sizing: border-box;
    padding-top: 10px;
  }}
  .nav-item:hover {{
    color: #a9a9b8;
  }}
  .nav-item.active {{
    color: #fca311; /* Tixx yellow */
    border-bottom: 3px solid #fca311;
  }}
  .nav-item svg {{
    margin-bottom: 6px;
    width: 20px;
    height: 20px;
  }}
  .nav-item.active svg {{
    stroke: #fca311;
  }}
</style>
<script>
  function switchTab(tab_label) {{
    const buttons = window.parent.document.querySelectorAll('button p');
    buttons.forEach(p => {{
        if (p.textContent === tab_label) {{
            p.parentElement.click();
        }}
    }});
  }}
  
  // Bridge is now handled in the main st.markdown block for cleaner scope

  // Hide the invisible Streamlit buttons in the parent DOM
  setInterval(() => {{
     const buttons = window.parent.document.querySelectorAll('button p');
     buttons.forEach(p => {{
         if (p.textContent.includes('H_')) {{
             // The structure in Streamlit is often button > div > span > p or similar
             // We go up until we find the stButton container
             let el = p;
             while (el && !el.classList.contains('stButton') && el !== window.parent.document.body) {{
                 el = el.parentElement;
             }}
             if (el && el.classList.contains('stButton')) {{
                 el.style.display = 'none';
                 el.style.height = '0';
                 el.style.margin = '0';
                 el.style.padding = '0';
                 el.style.visibility = 'hidden';
             }}
         }}
     }});
  }}, 50);
</script>
</head>
<body>
  <div class="nav-bar">
    <div class="nav-left">
      <img src="{logo_b64}" onerror="this.style.display='none'">
      <span>RatioTen</span>
    </div>
    <div class="nav-items">
      <div class="nav-item {'active' if current_view == '🍽️ Log' else ''}" onclick="switchTab('H_LOG')">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-house"><path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
        <span>Home</span>
      </div>
      <div class="nav-item {'active' if current_view == '📊 Analyze' else ''}" onclick="switchTab('H_ANALYZE')">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-bar-chart-2"><line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/></svg>
        <span>Stats</span>
      </div>
      <div class="nav-item {'active' if current_view == '⚙️ Plan' else ''}" onclick="switchTab('H_PLAN')">
        <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-sliders-horizontal"><line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/></svg>
        <span>Plan</span>
      </div>
    </div>
  </div>
</body>
</html>
"""
components.html(nav_html, height=72)

# Hidden callback bridge
def set_view(view):
    st.session_state.view_selection = view

# No more duplicate CSS block here


with st.container():
    if st.button("H_LOG", on_click=set_view, args=("🍽️ Log",)): pass
    if st.button("H_ANALYZE", on_click=set_view, args=("📊 Analyze",)): pass
    if st.button("H_PLAN", on_click=set_view, args=("⚙️ Plan",)): pass


# --- 4.5 Modals & Tools ---
if "enable_demo" not in st.session_state:
    st.session_state.enable_demo = False

# --- Main View Logic ---
if st.session_state.view_selection == "🍽️ Log":
    # --- Modernized Dashboard (Log View) ---

    # Fasting Status & Weight row
    status, target_timestamp = get_fasting_status(fasting_schedule)
    lowest_w = get_lowest_weight()

    dashboard_html = f"""
    <div class="metric-container">
        <div class="metric-card" style="border: 1px solid #00A6FF;">
            <div class="metric-label">Status</div>
            <div class="metric-value" style="font-size: 1rem; color: #00A6FF;">{status}</div>
            <div id="countdown-timer" style="font-size: 1.1rem; font-weight: 700; color: white;">--:--:--</div>
        </div>
        <div class="metric-card" style="border: 1px solid #00A6FF;">
            <div class="metric-label">Record Low</div>
            <div class="metric-value" style="font-size: 1.5rem;">{f"{lowest_w:.1f}" if lowest_w else "--"}</div>
            <div class="metric-delta delta-green">lbs</div>
        </div>
    </div>
    """
    st.markdown(dashboard_html, unsafe_allow_html=True)

    # Inject JS for timer separately
    if target_timestamp:
        st.components.v1.html(f"""
        <script>
        (function() {{
            const targetTime = {target_timestamp};
            const timerElement = window.parent.document.getElementById('countdown-timer') || document.getElementById('countdown-timer');
            
            function updateTimer() {{
                const now = new Date().getTime();
                const difference = targetTime - now;
                
                if (difference <= 0) {{
                    if(timerElement) timerElement.innerHTML = "00:00:00";
                    return;
                }}
                
                const hours = Math.floor((difference % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
                const minutes = Math.floor((difference % (1000 * 60 * 60)) / (1000 * 60));
                const seconds = Math.floor((difference % (1000 * 60)) / 1000);
                
                if(timerElement) {{
                    timerElement.innerHTML = 
                        String(hours).padStart(2, '0') + ":" + 
                        String(minutes).padStart(2, '0') + ":" + 
                        String(seconds).padStart(2, '0');
                }}
            }}
            
            updateTimer();
            setInterval(updateTimer, 1000);
        }})();
        </script>
        """, height=0)

    df_7days = get_trailing_7_days_data()

    # Calculate today's metrics
    today_str = datetime.now(EASTERN) .strftime("%Y-%m-%d")
    if not df_7days.empty and 'Date' in df_7days.columns:
        today_data = df_7days[df_7days['Date'] == today_str]
        if not today_data.empty:
            cals = int(today_data.iloc[0].get('Calories', 0))
            protein = int(today_data.iloc[0].get('Protein', 0))
            density = today_data.iloc[0].get('Density', '0.0%')
        else:
            cals, protein, density = 0, 0, "0.0%"
    else:
        cals, protein, density = 0, 0, "0.0%"

    # Metric Row (Daily Targets)
    metric_html = f"""
    <div class="metric-container">
        <div class="metric-card">
            <div class="metric-label">Calories</div>
            <div class="metric-value">{cals}</div>
            <div class="metric-delta {'delta-green' if cals <= user_goals['calories'] else 'delta-red'}">
                {f'↑ {user_goals["calories"] - cals} left' if cals <= user_goals['calories'] else f'↓ {cals - user_goals["calories"]} over'}
            </div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Protein</div>
            <div class="metric-value">{protein}g</div>
            <div class="metric-delta {'delta-green' if protein >= user_goals['protein'] else 'delta-red'}">
                {f'↑ {protein - user_goals["protein"]}g' if protein >= user_goals['protein'] else f'↓ {user_goals["protein"] - protein}g left'}
            </div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Density</div>
            <div class="metric-value">{density}</div>
            <div class="metric-delta delta-green">Target: 10%</div>
        </div>
    </div>
    """
    st.markdown(metric_html, unsafe_allow_html=True)
    
    # --- 4.5 Timeline Dashboard (New Feature) ---
    if status == "Eating Window Active":
        now = datetime.now(EASTERN)
        day_name = now.strftime("%A")
        sched = fasting_schedule.get(day_name)
        if sched and sched["start"] and sched["end"]:
            try:
                start_dt = datetime.combine(now.date(), datetime.strptime(sched["start"], "%H:%M").time()).replace(tzinfo=EASTERN)
                end_dt = datetime.combine(now.date(), datetime.strptime(sched["end"], "%H:%M").time()).replace(tzinfo=EASTERN)
                
                total_duration = float((end_dt - start_dt).total_seconds())
                elapsed = float((now - start_dt).total_seconds())
                progress_pct = max(0.0, min(100.0, (elapsed / total_duration) * 100.0))
                
                # Fetch today's food logs for timeline
                today_logs = get_today_log_for_timeline()
                timeline_html = render_timeline_html(sched["start"], sched["end"], today_logs, progress_pct=progress_pct)
                st.markdown(timeline_html, unsafe_allow_html=True)
            except Exception as e:
                pass # Fail silently for UI element
    elif st.session_state.get("show_timeline_always", False):
        # Developer override to show timeline during fasting
        now = datetime.now(EASTERN)
        day_name = now.strftime("%A")
        sched = fasting_schedule.get(day_name, {"start": "12:00", "end": "18:00"})
        if sched["start"] and sched["end"]:
            try:
                today_logs = get_today_log_for_timeline()
                timeline_html = render_timeline_html(sched["start"], sched["end"], today_logs, title="Timeline Override (Developer Mode)")
                st.markdown(timeline_html, unsafe_allow_html=True)
            except:
                pass

    # --- 5. Initialize Chat History & Session State (Log View Only) ---
    if "messages" not in st.session_state or not st.session_state.messages:
        with st.spinner("Syncing history from cloud..."):
            persistent_history = get_persistent_chat()
            if persistent_history:
                st.session_state.messages = persistent_history
            else:
                st.session_state.messages = [
                    {"role": "assistant", "content": "Ready to log. What are we eating?"}
                ]

    if "chat_session" not in st.session_state:
        current_stats = {'cals': cals, 'protein': protein, 'density': density}
        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days)
        st.session_state.chat_session = get_chat_session(st.session_state.current_model, fresh_prompt)

    # Display previous chat messages in a fixed-height container (optimized for Pro Max)
    with st.container(height=450):
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                content = message["content"]
                if isinstance(content, list):
                    for item in content:
                        st.write(item)
                else:
                    st.markdown(content)

    # --- 6. Chat Input Support (Log View Only) ---
    # Moved the camera logic directly above the chat input
    with st.container():
        # Status indicator for pending image
        if st.session_state.pending_image:
            st.markdown("""
            <div style="background-color: #1E3A5F; padding: 10px; border-radius: 5px; margin-bottom: 10px; border-left: 5px solid #00A6FF;">
                📷 <b>Photo Attached:</b> Describing your meal below will submit both the text and the photo.
            </div>
            """, unsafe_allow_html=True)
            
        if st.session_state.show_camera:
            captured_file = st.camera_input("Capture your meal", label_visibility="collapsed")
            if captured_file:
                st.session_state.pending_image = captured_file.getvalue()
                st.success("Photo attached!")
                st.session_state.show_camera = False # Hide camera after capture
                st.rerun()
                
        # Camera button is directly above the chat input, without large headers
        if not st.session_state.show_camera and not st.session_state.pending_image:
            if st.button("📷 Open Camera", use_container_width=True):
                st.session_state.show_camera = True
                st.rerun()

    user_input = st.chat_input("Describe your meal...")

    if user_input:
        # 1. Prepare segments for UI and Gemini
        message_content = []
        ui_content = []
        
        # Check for pending image in session state
        image_to_send = st.session_state.pending_image
        
        if image_to_send:
            message_content.append(genai.types.Part.from_bytes(data=image_to_send, mime_type="image/jpeg"))
            ui_content.append("📷 *Photo attached*")
        
        message_content.append(user_input)
        ui_content.append(user_input)

        # 2. Add user message to UI state
        st.session_state.messages.append({"role": "user", "content": ui_content})
        log_chat_to_sheet("user", ui_content)
        
        # Refresh current message display
        with st.chat_message("user"):
            for seg in ui_content:
                st.write(seg)
        
        # 3. Get Gemini Response with Tiered Fallback
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                max_retries = 3
                retry_delay = 2 # initial delay
                success = False
                response = None
                
                # Tier 1: Primary Model with Retry
                retry_delay_val = 2.0
                for attempt in range(max_retries):
                    try:
                        # Refresh session with latest stats for proactive auditing
                        current_stats = {'cals': cals, 'protein': protein, 'density': density}
                        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days)
                        
                        existing_session = st.session_state.get("chat_session")
                        existing_history = getattr(existing_session, "history", getattr(existing_session, "_history", None)) if existing_session else None
                        
                        # Re-initialize session with fresh prompt and existing history
                        st.session_state.chat_session = get_chat_session(st.session_state.current_model, fresh_prompt, history=existing_history)

                        response = st.session_state.chat_session.send_message(message_content)
                        success = True
                        break
                    except Exception as e:
                        error_msg = str(e).upper()
                        if "503" in error_msg or "UNAVAILABLE" in error_msg:
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay_val)
                                retry_delay_val = retry_delay_val * 2
                                continue
                            else:
                                st.warning("Primary model unavailable. Trying fallback...")
                                break
                        elif "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                            # 429 handled by fallback
                            break
                        else:
                            st.error(f"Primary error: {e}")
                            break
                
                # Tier 2: Secondary Model Fallback
                if not success:
                    try:
                        existing_session = st.session_state.get("chat_session")
                        existing_history = getattr(existing_session, "history", getattr(existing_session, "_history", None)) if existing_session else None
                        st.session_state.current_model = SECONDARY_MODEL
                        
                        current_stats = {'cals': cals, 'protein': protein, 'density': density}
                        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days)
                        
                        st.session_state.chat_session = get_chat_session(SECONDARY_MODEL, fresh_prompt, history=existing_history)
                        response = st.session_state.chat_session.send_message(message_content)
                        success = True
                        st.info("Using secondary model (Primary rate limit).")
                    except Exception as e:
                        # Continue to final fallback
                        pass

                # Tier 3: Stable Model Fallback
                if not success:
                    try:
                        existing_session = st.session_state.get("chat_session")
                        existing_history = getattr(existing_session, "history", getattr(existing_session, "_history", None)) if existing_session else None
                        st.session_state.current_model = STABLE_MODEL
                        
                        current_stats = {'cals': cals, 'protein': protein, 'density': density}
                        fresh_prompt = get_system_prompt(fasting_schedule, custom_instructions, today_stats=current_stats, weekly_summary=df_7days)
                        
                        st.session_state.chat_session = get_chat_session(STABLE_MODEL, fresh_prompt, history=existing_history)
                        response = st.session_state.chat_session.send_message(message_content)
                        success = True
                        st.warning("High-performance models unavailable. Using stable fallback.")
                    except Exception as e:
                        st.error(f"Critical Error: {e}")
                
                # 4. Handle Response & Logging
                if success and response:
                    # Clean the displayed text (remove JSON)
                    display_text = re.sub(r'```json\n.*?\n```', '', response.text, flags=re.DOTALL).strip()
                    
                    # Thoughts/Chain-of-thought support
                    thought = getattr(response.candidates[0], "thought", None) if response.candidates else None
                    if thought:
                        with st.expander("💭 Thinking Process", expanded=False):
                            st.markdown(thought)
                    
                    st.markdown(display_text)
                    
                    # Parse and Log Food to Sheet
                    match = re.search(r'```json\n(.*?)\n```', response.text, re.DOTALL)
                    if match:
                        try:
                            items_to_log = json.loads(match.group(1))
                            for data in items_to_log:
                                if log_to_sheet(data.get("item", "Unknown"), 
                                               data.get("calories", 0), 
                                               data.get("protein", 0), 
                                               data.get("density", "0%"),
                                               data.get("emoji", "🍽️")):
                                    st.toast(f"Logged: {data.get('item')} {data.get('emoji', '🍽️')}")
                            get_trailing_7_days_data.clear()
                        except Exception as e:
                            st.error(f"Logging error: {e}")
                    
                    # Persistence & Cleanup
                    st.session_state.messages.append({"role": "assistant", "content": display_text})
                    log_chat_to_sheet("assistant", display_text)
                    st.session_state.pending_image = None
                    st.session_state.show_camera = False
                    st.rerun()

elif st.session_state.view_selection == "📊 Analyze":
    # --- Analytics View ---
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-bar-chart-2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', "Performance Analytics")
    df_7days = get_trailing_7_days_data()

    # Weekly History Table
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-activity"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>', "Trailing 7 Days")
    if not df_7days.empty:
        st.dataframe(df_7days, width="stretch", hide_index=True)
    else:
        st.info("No logs found for the trailing 7 days.")

    # Data Visualization Tool
    if not df_7days.empty:
        render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-trending-up"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>', "Performance Trends")
        
        # Prepare data for plotting
        plot_df = df_7days.copy()
        plot_df['Date'] = pd.to_datetime(plot_df['Date'])
        plot_df = plot_df.sort_values('Date')
        
        # Exclude today's data as it's partial and skews scaling
        today_date = datetime.now(EASTERN).date()
        plot_df = plot_df[plot_df['Date'].dt.date < today_date]
        
        # Force day-level granularity by converting back to string for the index
        plot_df['Date_Label'] = plot_df['Date'].dt.strftime('%b %d')
        
        # Clean Density for plotting (convert "11.5%" to 11.5)
        if 'Density' in plot_df.columns:
            plot_df['Density_Val'] = plot_df['Density'].str.replace('%', '').astype(float)
        
        # Metric Selector
        metrics = ["Calories", "Protein", "Density_Val"]
        cols = st.columns([2, 1])
        with cols[0]:
            selected_metrics = st.multiselect(
                "Select Metrics to Visualize",
                options=metrics,
                default=["Calories"],
                format_func=lambda x: x.replace("_Val", " (%)")
            )
        
        if selected_metrics:
            # Create a display-friendly dataframe for the chart
            chart_data = plot_df.set_index('Date_Label')[selected_metrics]
            chart_data.columns = [c.replace("_Val", " Density (%)") for c in chart_data.columns]
            
            st.line_chart(chart_data, use_container_width=True)
            st.caption("Note: Metric scales differ (Calories: ~1500, Protein: ~150, Density: ~10%). Multi-metric view may compress smaller values.")
        else:
            st.info("Select at least one metric to visualize the trend.")
    
    # --- Week-over-Week Analytics ---
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-calendar"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>', "Week-over-Week Performance")
    
    # Fetch WoW Data (handle demo mode)
    enable_demo = st.session_state.get("enable_demo", False)
    df_wow = get_wow_data(enable_demo=enable_demo)
    
    if not df_wow.empty:
        # WoW History Table
        render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-list"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>', "Weekly Averages")
        st.dataframe(df_wow[["Week", "Avg Calories", "Avg Protein", "Density"]], width="stretch", hide_index=True)
        
        render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-line-chart"><path d="M3 3v18h18"/><path d="m19 9-5 5-4-4-3 3"/></svg>', "Weekly Trends")
        # Reuse same metric selection logic for WoW
        metrics_wow = ["Avg Calories", "Avg Protein", "Density_Val"]
        selected_metrics_wow = st.multiselect(
            "Select Metrics for WoW Trends",
            options=metrics_wow,
            default=["Avg Calories"],
            key="wow_metrics",
            format_func=lambda x: x.replace("Avg ", "").replace("_Val", " (%)")
        )
        
        if selected_metrics_wow:
            wow_plot_df = df_wow.set_index('Week')[selected_metrics_wow]
            # Rename for display
            display_cols = []
            for c in wow_plot_df.columns:
                name = c.replace("Avg ", "")
                if "_Val" in name:
                    name = name.replace("_Val", " Density (%)")
                display_cols.append(name)
            wow_plot_df.columns = display_cols
            
            st.line_chart(wow_plot_df, use_container_width=True)
    else:
        st.info("Insufficient data for Week-over-Week trends. Start logging to build your history!")
    
    # --- 8. Timeline History Section (New) ---
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-history"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/></svg>', "Previous 10 Days")
    history_logs = get_logs_for_history(days=10)
    
    # Sort dates descending (exclude today if active)
    today_key = datetime.now(EASTERN).strftime("%Y-%m-%d")
    sorted_dates = sorted([d for d in history_logs.keys() if d != today_key], reverse=True)
    
    if not sorted_dates:
        st.info("No historical log data available yet.")
    else:
        for date_str in sorted_dates:
            # Get schedule for that day
            dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = dt_obj.strftime("%A")
            sched = fasting_schedule.get(day_name, {"start": "12:00", "end": "18:00"})
            
            day_logs = history_logs[date_str]
            if sched["start"] and sched["end"]:
                st.markdown(f"**{date_str} ({day_name})**")
                html = render_timeline_html(sched["start"], sched["end"], day_logs, title=None)
                st.markdown(html, unsafe_allow_html=True)

elif st.session_state.view_selection == "⚙️ Plan":
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-settings"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>', "Protocol Plan")
    
    # 1. Effectiveness Score
    score, msg = calculate_plan_effectiveness()
    if score is not None:
        # Render a custom Speedometer gauge
        color = "#00A6FF" # Cyan by default
        if score < 5: color = "#dc3545"
        elif score < 8: color = "#ffc107"
        
        rotation = (score / 10.0) * 180 - 90 # -90 to 90 degrees
        
        gauge_html = f"""
        <div style="text-align: center; margin: 30px 0;">
            <div style="font-size: 0.9rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px;">Plan Effectiveness Score</div>
            <div style="position: relative; width: 250px; height: 125px; margin: 0 auto; overflow: hidden;">
                <div style="position: absolute; top: 0; left: 0; width: 250px; height: 250px; border-radius: 50%; border: 15px solid #333; border-bottom-color: transparent; border-left-color: transparent; transform: rotate(135deg);"></div>
                <div style="position: absolute; top: 0; left: 0; width: 250px; height: 250px; border-radius: 50%; border: 15px solid {color}; border-bottom-color: transparent; border-left-color: transparent; transform: rotate({rotation+45}deg); transition: transform 1s ease-out; box-shadow: 0 0 15px {color} inset;"></div>
                <div style="position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); font-size: 3rem; font-weight: 800; color: white;">{score:.1f}</div>
            </div>
            <div style="font-size: 0.8rem; color: #aaa; margin-top: 5px;">Based on 14-day adherence & record low weight</div>
        </div>
        """
        st.markdown(gauge_html, unsafe_allow_html=True)
    else:
        st.info(f"📊 Effectiveness Calibrating: {msg}")

    # 2. Goal Editor
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-target"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>', "Daily Targets")
    with st.form("goals_form"):
        col_c, col_p = st.columns(2)
        with col_c:
            new_cals = st.number_input("Calorie Lid", min_value=1000, max_value=4000, value=user_goals.get("calories", 1500), step=50)
        with col_p:
            new_prot = st.number_input("Protein Floor (g)", min_value=50, max_value=300, value=user_goals.get("protein", 150), step=5)
            
        calculated_density = (new_prot / new_cals) * 100 if new_cals > 0 else 0
        st.caption(f"Calculated Target Density: **{calculated_density:.1f}%**")
        
        if st.form_submit_button("Save Goals", type="primary"):
            if save_user_goals(new_cals, new_prot):
                st.success("Goals updated successfully!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Failed to save goals.")

    # 3. Schedule Editor
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-clock"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>', "Fasting Schedule")
    with st.form("schedule_form"):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        new_schedule = {}
        for day in days:
            current = fasting_schedule.get(day, {"start": None, "end": None})
            col1, col2, col3 = st.columns([2, 2, 2])
            with col1:
                st.markdown(f"**{day}**")
                skip_day = st.checkbox("Fast / Skip", value=(current["start"] is None), key=f"skip_{day}")
            with col2:
                s_val = current["start"] if current["start"] else "12:00"
                s_time = st.time_input("Start", value=datetime.strptime(s_val, "%H:%M").time(), key=f"start_{day}", disabled=skip_day)
            with col3:
                e_val = current["end"] if current["end"] else "18:00"
                e_time = st.time_input("End", value=datetime.strptime(e_val, "%H:%M").time(), key=f"end_{day}", disabled=skip_day)
                
            if skip_day:
                new_schedule[day] = {"start": None, "end": None}
            else:
                new_schedule[day] = {"start": s_time.strftime("%H:%M"), "end": e_time.strftime("%H:%M")}
                
        if st.form_submit_button("Save Schedule", type="primary"):
            if save_fasting_schedule(new_schedule):
                st.success("Schedule updated successfully!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Failed to save schedule.")

    st.divider()
    
    # 4. Advanced Settings
    with st.expander("🛠️ Advanced", expanded=False):
        st.markdown("#### 🛠️ Developer Tools")
        show_always = st.checkbox(
            "Always Show Food Timeline (Home Page) [NEW]",
            value=st.session_state.get("show_timeline_always", False),
            help="Force the food timeline to be visible on the home page even during fasting windows."
        )
        if show_always != st.session_state.get("show_timeline_always", False):
            st.session_state.show_timeline_always = show_always
            st.rerun()

        st.divider()

        render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-database"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/></svg>', "Data Management")
        st.info("If the AI's tone feels off or you want to start a fresh interaction, you can clear the conversation history here.")
        if st.button("Clear Chat History", type="secondary", use_container_width=True):
            if clear_persistent_chat():
                st.session_state.messages = [{"role": "assistant", "content": "Ready to log. What are we eating?"}]
                st.success("Chat history cleared!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Failed to clear chat history.")
        
        st.markdown("#### Demo Mode")
        current_demo_state = st.session_state.get("enable_demo", False)
        new_demo_state = st.checkbox(
            "Enable Demo Data (for testing/showcasing)",
            value=current_demo_state,
            help="Toggle this to use pre-populated demo data instead of your own logs. Requires a rerun to take full effect."
        )
        if new_demo_state != current_demo_state:
            st.session_state.enable_demo = new_demo_state
            st.success(f"Demo mode {'enabled' if new_demo_state else 'disabled'}. Rerunning...")
            time.sleep(1)
            st.rerun()

    # End of view-specific content
