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
from PIL import Image
import os

favicon_img = "🔟"
if os.path.exists("favicon.png"):
    try:
        favicon_img = Image.open("favicon.png")
    except:
        pass
elif os.path.exists("modern_ratioten_logo.png"):
    try:
        favicon_img = Image.open("modern_ratioten_logo.png")
    except:
        pass

st.set_page_config(page_title="RatioTen", page_icon=favicon_img, layout="centered")

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

@st.cache_resource
def get_google_sheet():
    credentials_dict = dict(st.secrets["gcp_service_account"])
    gc = gspread.service_account_from_dict(credentials_dict)
    return gc.open("Nutrition_Logs")

def render_page_header(icon_svg, title):
    """Renders a prominent page-level title, visually distinct from section headers."""
    # Replace the section icon class with a larger page-icon class
    icon = icon_svg.replace('class="lucide', 'class="page-icon lucide')
    st.markdown(f"""
    <style>
    .page-icon {{
        width: 28px; height: 28px;
        stroke: #fca311;
        vertical-align: middle;
        margin-right: 10px;
        flex-shrink: 0;
    }}
    .page-header-bar {{
        display: flex;
        align-items: center;
        margin: 28px 0 6px;
        padding-bottom: 10px;
        border-bottom: 2px solid rgba(252, 163, 17, 0.25);
    }}
    .page-header-text {{
        font-size: 1.45rem;
        font-weight: 800;
        letter-spacing: 0.5px;
        color: #ffffff;
        margin: 0;
    }}
    </style>
    <div class="page-header-bar">{icon}<span class="page-header-text">{title}</span></div>
    """, unsafe_allow_html=True)

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
                transform = "translate(-50%, 0)"
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
                    calories = int(float(row[2])) if len(row) > 2 and str(row[2]).strip() else 0
                    protein = int(float(row[3])) if len(row) > 3 and str(row[3]).strip() else 0
                    density = str(row[4]).strip() if len(row) > 4 and str(row[4]).strip() else "0.0%"
                    emoji = str(row[6]).strip() if len(row) > 6 and str(row[6]).strip() else "🍽️"
                    logs_by_date[date_key].append({"timestamp": ts, "item": item, "calories": calories, "protein": protein, "density": density, "emoji": emoji})
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

def calculate_plan_effectiveness(calc_date=None, pre_sh=None, pre_goals=None, pre_fasting=None):
    """Calculates a score (1-10) based on 14-day adherence and weight shift."""
    try:
        if calc_date is None:
            calc_date = datetime.now(EASTERN).date()
            
        # --- Demo Mode Shortcut ---
        if st.session_state.get("enable_demo", False):
            drivers = {
                "adherent_days": 11,
                "total_days": 13,
                "avg_density": 10.8,
                "weight_shift": 1.4
            }
            return 8.7, None, drivers

        if pre_sh:
            sh = pre_sh
        else:
            credentials_dict = dict(st.secrets["gcp_service_account"])
            gc = gspread.service_account_from_dict(credentials_dict)
            sh = gc.open("Nutrition_Logs")

        goals = pre_goals if pre_goals else get_user_goals()
        target_density = 10.0
        target_cal_limit = int(goals.get('calories', 1500)) + 100

        # A 14-day inclusive window means calc_date to 13 days prior
        thirteen_days_ago = calc_date - timedelta(days=13)
        seven_days_ago = calc_date - timedelta(days=6) # 7 day window

        # --- 1. Food Logs (sheet1, same pattern as get_trailing_7_days_data) ---
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

            daily_data = {}
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
                except:
                    continue

            # Always evaluate exactly 14 days ending at calc_date
            for i in range(14):
                eval_date = calc_date - timedelta(days=i)
                if eval_date not in daily_data:
                    day_name = eval_date.strftime("%A")
                    sched = fasting_schedule.get(day_name, {"start": None, "end": None})
                    # Add as empty entry (0 cals/prot)
                    daily_data[eval_date] = {"cals": 0.0, "prot": 0.0, "logs": [], "is_missing": True}
            
            total_days_eval = 14
            
            # --- Build the drivers early to allow partial logging ---
            adherence_score_total = 0.0
            sum_cals = 0.0
            sum_prot = 0.0
            daily_breakdown = {}
            
            # Always iterate 14 days
            for eval_date, nums in daily_data.items():
                day_name = eval_date.strftime("%A")
                sched = fasting_schedule.get(day_name, {"start": None, "end": None})
                
                # Determine scheduled eating hours
                if sched["start"] and sched["end"]:
                    try:
                        start_t = datetime.strptime(sched["start"], "%H:%M")
                        end_t = datetime.strptime(sched["end"], "%H:%M")
                        eating_hours = (end_t - start_t).total_seconds() / 3600.0
                        if eating_hours < 0: eating_hours += 24.0 # Cross midnight
                    except:
                        eating_hours = 8.0
                else:
                    eating_hours = 0.0 # Skip day
                
                target_protein = float(goals.get('protein', 150))
                if eating_hours >= 6.0:
                    dynamic_floor = target_protein
                elif eating_hours <= 1.0:
                    if eating_hours == 0.0:
                        dynamic_floor = 0.0
                    else:
                        dynamic_floor = target_protein * 0.30
                else:
                    fraction = 0.30 + ((eating_hours - 1.0) / 5.0) * 0.70
                    dynamic_floor = target_protein * fraction
                
                sum_cals += nums["cals"]
                sum_prot += nums["prot"]

                cal_pts_awarded = 0.0
                prot_pts_awarded = 0.0

                # 1. Calories (4 pts)
                if nums["cals"] <= target_cal_limit:
                    cal_pts_awarded = 4.0
                elif nums["cals"] <= target_cal_limit + 200:
                    cal_pts_awarded = 2.0
                
                # 2. Protein (4 pts)
                if nums["prot"] >= dynamic_floor:
                    prot_pts_awarded = 4.0
                elif nums["prot"] >= (dynamic_floor * 0.8):
                    prot_pts_awarded = 2.0

                # 3. Fasting Timing (2 pts)
                timing_pts = 0.0
                if eating_hours == 0.0:
                    if nums["cals"] == 0:
                        timing_pts = 2.0
                else:
                    if len(nums["logs"]) > 0:
                        try:
                            s_time = datetime.strptime(sched["start"], "%H:%M").time()
                            e_time = datetime.strptime(sched["end"], "%H:%M").time()
                            buf_start = datetime.combine(eval_date, s_time) - timedelta(hours=1)
                            buf_end = datetime.combine(eval_date, e_time) + timedelta(hours=1)
                            if buf_end < buf_start: buf_end += timedelta(days=1)
                            
                            all_in_window = True
                            for log_dt in nums["logs"]:
                                log_dt_naive = log_dt.replace(tzinfo=None)
                                if not (buf_start <= log_dt_naive <= buf_end):
                                    all_in_window = False
                                    break
                            
                            if all_in_window:
                                timing_pts = 2.0
                        except:
                            timing_pts = 2.0 # Benefit of doubt for parsing errors
                
                day_score = cal_pts_awarded + prot_pts_awarded + timing_pts
                adherence_score_total += (day_score / 10.0)
                daily_breakdown[eval_date] = {
                    "cal_pts": cal_pts_awarded,
                    "prot_pts": prot_pts_awarded,
                    "time_pts": timing_pts,
                    "day_score": day_score
                }

            avg_density = (sum_prot / sum_cals * 100) if sum_cals > 0 else 0.0
            adherence_rate = adherence_score_total / total_days_eval
            
            drivers = {
                "adherent_days": round(adherence_score_total, 1),
                "total_days": total_days_eval,
                "avg_density": avg_density,
                "adherence_rate": adherence_rate,
                "daily_breakdown": daily_breakdown
            }

            # Now enforce the "minimum data for aggregate score" rule
            days_with_data = sum(1 for d in daily_data.values() if not d.get("is_missing", False))
            if days_with_data < 7:
                return None, f"Need 7+ days of data. Have {days_with_data}.", drivers

        except Exception as e:
            return None, f"Error parsing food logs: {str(e)}", None

        # --- 2. Weight Logs (Weight_Logs tab) ---
        try:
            try:
                weight_ws = sh.worksheet("Weight_Logs")
            except gspread.WorksheetNotFound:
                return None, "Weight_Logs sheet not found.", None

            weight_records = weight_ws.get_all_records()
            if not weight_records:
                return None, "No weight data found.", None

            df_weight = pd.DataFrame(weight_records)
            col_map_w = {col.lower().strip(): col for col in df_weight.columns}
            date_col = next((col_map_w[c] for c in ['date', 'timestamp', 'time'] if c in col_map_w), None)
            weight_col = next((col_map_w[c] for c in ['weight (lbs)', 'weight', 'lbs'] if c in col_map_w), None)

            if not date_col or not weight_col:
                return None, f"Weight_Logs columns not found. Got: {list(df_weight.columns)}", None

            df_weight['Date'] = pd.to_datetime(df_weight[date_col], errors='coerce').dt.date
            df_weight['Weight'] = pd.to_numeric(df_weight[weight_col], errors='coerce')
            df_recent = df_weight[(df_weight['Date'] >= thirteen_days_ago) & (df_weight['Date'] <= calc_date)].dropna(subset=['Weight'])

            if len(df_recent) < 4:
                return None, f"Need 4+ weigh-ins in 14 days. Have {len(df_recent)}.", None

            first_half = df_recent[df_recent['Date'] < seven_days_ago]
            second_half = df_recent[df_recent['Date'] >= seven_days_ago]

            if first_half.empty or second_half.empty:
                return None, "Need weigh-ins in both weeks of the 14-day window.", None

            weight_delta = float(first_half['Weight'].min()) - float(second_half['Weight'].min())

        except Exception as e:
            return None, f"Error parsing weight logs: {str(e)}", None

        # --- 3. Score Calculation ---
        score = adherence_rate * 5.0
        if weight_delta >= 1.0:
            score += 5.0
        elif weight_delta >= 0:
            score += 2.0 + (weight_delta * 2.9)
        elif weight_delta < -0.5:
            score -= 2.0

        score = max(1.0, min(10.0, score))

        return score, None, drivers

    except Exception as e:
        return None, f"System Error: {str(e)}", None

def sync_plan_effectiveness_logs(force_resync=False):
    """Backfills and continuously logs the explicit daily plan effectiveness scores to the database."""
    if st.session_state.get("enable_demo", False): return
    
    # st.toast("DEBUG: Sync Engine Started!", icon="🔄") # Removed noise, but will show result if forced
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        
        # Pre-fetch all expensive data once for the batch
        all_goals = get_user_goals()
        all_fasting = get_fasting_schedule()
        
        try:
            log_ws = sh.worksheet("Plan_Effectiveness_Logs")
        except gspread.WorksheetNotFound:
            log_ws = sh.add_worksheet(title="Plan_Effectiveness_Logs", rows="100", cols="7")
            log_ws.append_row(["Date", "Calorie Pts", "Protein Pts", "Fast Timing Pts", "Ad Score", "Weight Shift", "Plan Score"])
            
        data = log_ws.get_all_values()
        # Create a mapping of Date -> Row Index (1-indexed)
        date_row_map = {row[0]: i + 1 for i, row in enumerate(data)}
        logged_dates = set(date_row_map.keys())
        
        now = datetime.now(EASTERN).date()
        backfill_range = 21 if force_resync else 14
        
        # Test the backfill days
        days_logged_this_run = 0
        for i in range(backfill_range, 0, -1):
            target_date = now - timedelta(days=i)
            date_str = target_date.strftime("%Y-%m-%d")
            
            if date_str not in logged_dates or force_resync:
                # Optimized call with pre-fetched sheet/goals/fasting
                score, msg, drivers = calculate_plan_effectiveness(
                    calc_date=target_date, 
                    pre_sh=sh, 
                    pre_goals=all_goals, 
                    pre_fasting=all_fasting
                )
                
                if drivers:
                    daily_breakdown = drivers.get("daily_breakdown", {})
                    day_data = daily_breakdown.get(target_date, {})
                    
                    cal_pts = day_data.get("cal_pts", 0.0)
                    prot_pts = day_data.get("prot_pts", 0.0)
                    time_pts = day_data.get("time_pts", 0.0)
                    day_score = day_data.get("day_score", 0.0)
                    
                    # Log DAILY perf (0-5) in Ad Score
                    day_ad_score = day_score / 2.0
                    weight_shift = drivers.get("weight_shift", 0.0)
                    final_score = score if score is not None else 0.0
                    
                    row_content = [
                        date_str, cal_pts, prot_pts, time_pts,
                        round(day_ad_score, 2), round(weight_shift, 2), round(final_score, 2)
                    ]
                    
                    if date_str in date_row_map:
                        if force_resync:
                            log_ws.update(f"A{date_row_map[date_str]}:G{date_row_map[date_str]}", [row_content])
                    else:
                        log_ws.append_row(row_content)
                        # Avoid rate limit on append
                        time.sleep(1.0)
                        
                    days_logged_this_run += 1
                    if days_logged_this_run >= 20: 
                        break
                else:
                    with open("sync_errors.txt", "a") as f:
                        f.write(f"[{datetime.now(EASTERN)}] Skipped {date_str}: {msg}\n")
    except Exception as e:
        import traceback
        with open("sync_errors.txt", "a") as f:
            f.write(f"[{datetime.now(EASTERN)}] Sync Error: {e}\n{traceback.format_exc()}\n")


# Run the sync silently in the background
if "plan_effectiveness_synced" not in st.session_state:
    st.session_state.plan_effectiveness_synced = True
    sync_plan_effectiveness_logs()

# --- 3. System Prompt (The Rules Engine) ---
def get_system_prompt(schedule, goals, custom_instructions="", today_stats=None, weekly_summary=None, today_logs=None):
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

    logs_context = ""
    if today_logs:
        logs_rows = "\n".join([
            f"| {log['item']} ({log['emoji']}) | {log.get('calories', 0)} | {log.get('protein', 0)} | {log.get('density', '0.0%')} |"
            for log in today_logs
        ])
        logs_context = f"""
### TODAY'S EXPLICIT FOOD LOGS:
Use this as the authoritative source for all logged items and their macros today. Do NOT hallucinate or recall macros from earlier in the conversation — always use these values.

| Item | Calories | Protein (g) | Density |
|------|----------|-------------|---------|\n{logs_rows}
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
{logs_context}
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

nav_html = f"""
<!DOCTYPE html>
<html>
<head>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@500;800&display=swap');
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
    background: rgba(22, 24, 33, 0.85); /* Premium glassmorphism */
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.05); /* Subtle rim light */
    border-radius: 16px;
    padding: 0 20px;
    height: 70px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }}
  .nav-left {{
    display: flex;
    align-items: center;
  }}
  .nav-left svg {{
    width: 32px;
    height: 32px;
    margin-right: 12px;
    filter: drop-shadow(0 4px 6px rgba(252, 163, 17, 0.25)); /* Glow effect */
  }}
  .nav-left span.brand-text {{
    font-family: 'Outfit', sans-serif;
    color: white;
    font-size: 21px;
    letter-spacing: -0.2px;
    margin-top: 1px;
  }}
  .nav-left span .brand-accent {{
    color: #fca311;
    font-weight: 800;
  }}
  .nav-left span .brand-base {{
    font-weight: 500;
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
      <svg width="34" height="34" viewBox="0 0 34 34" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect width="34" height="34" rx="10" fill="url(#logo_grad_rt)" />
        <rect x="0.5" y="0.5" width="33" height="33" rx="9.5" stroke="rgba(255,255,255,0.2)" stroke-width="1"/>
        <path d="M11 10.5V23.5" stroke="#161821" stroke-width="2.8" stroke-linecap="round"/>
        <path d="M25 9.5L13 24.5" stroke="#161821" stroke-width="2.5" stroke-linecap="round"/>
        <circle cx="23" cy="17" r="4.5" stroke="#161821" stroke-width="2.5"/>
        <defs>
          <linearGradient id="logo_grad_rt" x1="0" y1="0" x2="34" y2="34" gradientUnits="userSpaceOnUse">
            <stop stop-color="#fca311"/>
            <stop offset="1" stop-color="#ff7e00"/>
          </linearGradient>
        </defs>
      </svg>
      <span class="brand-text"><span class="brand-base">Ratio</span><span class="brand-accent">Ten</span></span>
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

@st.dialog("Plan Effectiveness Score Breakdown", width="large")
def show_effectiveness_modal():
    st.markdown("""
    **How is the Plan Effectiveness Score Calculated?**

    The Plan Effectiveness Score is a powerful tool designed to measure how well you are adhering to your nutritional goals and how your body is responding over a 14-day period. Whether your goal is to lose body fat or build lean muscle, consistent feedback is the key to maximizing your progress. This score provides an objective, data-driven look at your consistency so you can calibrate your approach and avoid guessing.

    #### The Two Core Components

    Your score, which ranges from a minimum of 1.0 to a maximum of 10.0, is built on two primary factors: **Adherence** and **Weight Shift**.

    **1. Daily Adherence (Up to 5.0 Points)**
    The first half of your score measures your day-to-day consistency over the last 14 days. To hit your goals, you need to execute your plan effectively.

    We break each day down into a **10-Point System**, which is then averaged across the 14 days:
    *   **Calories (4 Points):** Full points for staying under your Calorie Lid (target + 100).
    *   **Protein (4 Points):** Full points for hitting your **Dynamic Protein Floor**. To protect lean mass without forcing impossible single-meal gorging, the required protein dynamically scales based on your scheduled eating window. A 6+ hour window asks for 100% of your goal. An OMAD (1 hour) window scales down to 30%.
    *   **Fasting Timing (2 Points):** Full points if all logged items fall within your scheduled eating window (with a generous 1-hour buffer). If you have a full Fasting/Skip day scheduled, staying at 0 calories yields full points.

    Your Adherence Score is then calculated based on your average daily performance:
    
    $$ \\text{Adherence Score} = \\left( \\frac{\\text{Average Daily Points}}{10} \\right) \\times 5.0 $$

    If you log 14 days and hit your targets and windows perfectly every single day, you earn a perfect 5.0 for this section. If you hit it half the time, you earn 2.5. This rewards consistency.

    **2. The Weight Shift Focus (Up to 5.0 Points)**
    Even with perfect adherence, your body's response is the ultimate truth. The second half of your score looks at actual weight movement over the 14-day window.

    To smooth out daily fluctuations, the system compares your lowest weight from the first 7 days to your lowest weight from the past 7 days:
    
    $$ \\text{Weight Shift} = \\text{Min. Weight (Week 1)} - \\text{Min. Weight (Week 2)} $$

    *   **Weight Loss (+5.0 Points):** If your minimum weight dropped by 1.0 lb or more, you earn the full 5 points. For weight loss journeys, this proves the calorie deficit is working. For lean bulking journeys, maintaining a very slight deficit or maintenance phase is often required before adjusting calories upward.
    *   **Maintenance (+2.0 to +4.9 Points):** If your weight stayed the same or dropped slightly (between 0 and 0.9 lbs), you earn a partial score between 2.0 and 4.9.
    *   **Weight Gain (-2.0 Points):** If your minimum weight increased by more than 0.5 lbs, the system subtracts 2.0 points. While this might seem counterintuitive for a bulking journey, rapid weight gain without precise tracking often includes unwanted fat. The system flags this so you can review if the surplus is appropriate.

    #### Note on Average Density (Score Driver)
    The "Avg Protein" metric shown on your dashboard uses a **Calorie-Weighted Aggregate** over the 14 days. This means it calculates `(Total Protein / Total Calories)`. By measuring total energy vs total protein over the entire block, we prevent short feeding days (OMAD) from disproportionately punishing your average metrics.

    #### Scenarios and Scores

    **Scenario A: The Perfect Run**
    You score 10/10 points 14 out of 14 days (5 points). Your lowest weight in the second week was 1.5 lbs lighter than the first week (5 points). **Total Score: 10.0.** Your plan is perfectly calibrated—keep going!

    **Scenario B: High Adherence, Slow Movement**
    You score 10/10 points every single day (5.0 points) but your weight only dropped by 0.5 lbs (+3.5 points). **Total Score: 8.5.** You are extremely consistent, but the scale isn't moving fast. This is a signal that you might need to slightly lower your "Calorie Lid" to accelerate fat loss.

    **Scenario C: Low Adherence, Weight Gain**
    You average 4/10 points per day (2.0 points) and your weight increased by 1 lb (-2.0 points). **Total Score: 1.0** (The minimum score). This means the system is detecting a drift from the protocol. This isn't a failure; it's vital feedback. It tells you exactly where to focus—getting back to hitting your daily targets.

    #### Why Feedback Matters
    We don't manage what we don't measure. By combining your daily input (Adherence) with your biological output (Weight Shift), the Plan Effectiveness Score acts as your compass. It removes the emotion from standing on the scale and replaces it with clear math, empowering you to adjust your approach and reach your goals.
    """)

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
        today_logs = get_today_log_for_timeline()
        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days, today_logs=today_logs)
        
        formatted_history = []
        for msg in st.session_state.messages:
            role = "user" if msg["role"] == "user" else "model"
            parts = []
            content = msg["content"]
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, str):
                        parts.append(genai.types.Part.from_text(text=item))
            elif isinstance(content, str):
                parts.append(genai.types.Part.from_text(text=content))
            if parts:
                formatted_history.append(genai.types.Content(role=role, parts=parts))
                
        st.session_state.chat_session = get_chat_session(st.session_state.current_model, fresh_prompt, history=formatted_history)

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
                        today_logs = get_today_log_for_timeline()
                        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days, today_logs=today_logs)
                        
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
                        today_logs = get_today_log_for_timeline()
                        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days, today_logs=today_logs)
                        
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
                        today_logs = get_today_log_for_timeline()
                        fresh_prompt = get_system_prompt(fasting_schedule, user_goals, custom_instructions, today_stats=current_stats, weekly_summary=df_7days, today_logs=today_logs)
                        
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
                            today_logs_data = get_today_log_for_timeline()
                            
                            valid_items_to_log = []
                            for data in items_to_log:
                                item_name = data.get("item", "Unknown").strip().lower()
                                
                                # Check for duplicates today
                                is_duplicate = False
                                for log in today_logs_data:
                                    if log.get("item", "").strip().lower() == item_name:
                                        is_duplicate = True
                                        break
                                
                                conf_key = f"confirm_{item_name}"
                                if is_duplicate and not st.session_state.get(conf_key, False):
                                    st.session_state[conf_key] = True
                                    display_text += f"\n\n**⚠️ System Notice:** You already logged '{data.get('item')}' today. Are you logging another one? *(Reply 'yes' to confirm)*"
                                else:
                                    valid_items_to_log.append(data)
                                    if st.session_state.get(conf_key, False):
                                        st.session_state[conf_key] = False

                            for data in valid_items_to_log:
                                if log_to_sheet(data.get("item", "Unknown"), 
                                               data.get("calories", 0), 
                                               data.get("protein", 0), 
                                               data.get("density", "0%"),
                                               data.get("emoji", "🍽️")):
                                    st.toast(f"Logged: {data.get('item')} {data.get('emoji', '🍽️')}")
                            
                            if valid_items_to_log:
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
    render_page_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-bar-chart-2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>', "Performance Analytics")
    df_7days = get_trailing_7_days_data()

    # --- Plan Effectiveness ---
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-gauge"><path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>', "Plan Effectiveness")
    score, msg, drivers = calculate_plan_effectiveness()
    if score is not None:
        color = "#00A6FF"
        if score < 5: color = "#dc3545"
        elif score < 8: color = "#ffc107"
        rotation = 135 + (score / 10.0) * 180
        gauge_html = f"""
        <div style="text-align: center; margin: 30px 0;">
            <div style="font-size: 0.9rem; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px;">Plan Effectiveness Score</div>
            <div style="position: relative; width: 250px; height: 125px; margin: 0 auto; overflow: hidden;">
                <div style="position: absolute; top: 0; left: 0; width: 250px; height: 250px; border-radius: 50%; border: 15px solid #333; border-bottom-color: transparent; border-left-color: transparent; transform: rotate(315deg);"></div>
                <div style="position: absolute; top: 0; left: 0; width: 250px; height: 250px; border-radius: 50%; border: 15px solid {color}; border-bottom-color: transparent; border-left-color: transparent; transform: rotate({rotation}deg); transition: transform 1s ease-out; box-shadow: 0 0 15px {color} inset;"></div>
                <div style="position: absolute; bottom: 0; left: 50%; transform: translateX(-50%); font-size: 3rem; font-weight: 800; color: white;">{score:.1f}</div>
            </div>
            <div style="font-size: 0.8rem; color: #aaa; margin-top: 5px;">Based on 14-day adherence &amp; record low weight</div>
        </div>
        """
        st.markdown(gauge_html, unsafe_allow_html=True)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            if st.button("How is this calculated?", use_container_width=True, type="tertiary", key="eff_modal_stats"):
                show_effectiveness_modal()

        st.markdown("""
        <style>
        .driver-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 20px 0; }
        .driver-card { background: rgba(255,255,255,0.05); border-radius: 12px; padding: 15px; text-align: center; border: 1px solid rgba(255,255,255,0.1); }
        .driver-icon { width: 24px; height: 24px; margin: 0 auto 8px; display: block; color: #fca311; stroke: #fca311; }
        .driver-label { font-size: 0.75rem; color: #aaa; text-transform: uppercase; letter-spacing: 0.5px; }
        .driver-value { font-size: 1.1rem; font-weight: 700; color: white; margin-top: 2px; }
        .driver-status { font-size: 0.7rem; margin-top: 5px; font-weight: 600; }
        .status-ok { color: #00FFC2; } .status-warn { color: #FFC107; } .status-crit { color: #FF4B4B; }
        .coach-tip { background: linear-gradient(90deg, rgba(0,166,255,0.1), rgba(0,255,194,0.1)); border-left: 4px solid #00A6FF; padding: 15px; border-radius: 8px; margin-top: 10px; }
        </style>
        """, unsafe_allow_html=True)

        adherence_pct = (drivers['adherent_days'] / drivers['total_days']) * 100
        adherence_status = "status-ok" if adherence_pct >= 85 else ("status-warn" if adherence_pct >= 70 else "status-crit")
        adherence_msg = "Optimal" if adherence_pct >= 85 else ("Inconsistent" if adherence_pct >= 70 else "Needs Focus")
        density_status = "status-ok" if drivers['avg_density'] >= 10.5 else ("status-warn" if drivers['avg_density'] >= 9.5 else "status-crit")
        density_msg = "Lean" if drivers['avg_density'] >= 10.5 else ("Moderate" if drivers['avg_density'] >= 9.5 else "Improve")
        weight_status = "status-ok" if drivers['weight_shift'] >= 0.8 else ("status-warn" if drivers['weight_shift'] >= 0 else "status-crit")
        weight_msg = "Losing" if drivers['weight_shift'] >= 0.8 else ("Steady" if drivers['weight_shift'] >= 0 else "Gaining")
        logging_pct = (drivers['total_days'] / 14) * 100
        logging_status = "status-ok" if logging_pct >= 80 else ("status-warn" if logging_pct >= 60 else "status-crit")
        logging_msg = "Consistent" if logging_pct >= 80 else ("Partial" if logging_pct >= 60 else "Sparse")

        icon_check = '<svg class="driver-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>'
        icon_zap   = '<svg class="driver-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
        icon_trend = '<svg class="driver-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 18 13.5 8.5 8.5 13.5 1 6"/><polyline points="17 18 23 18 23 12"/></svg>'
        icon_clip  = '<svg class="driver-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>'

        tip_protocol = "Average daily adherence score (Calories, Dynamic Protein, Fasting Window).&#10;Statuses:&#10;• Optimal (≥85%)&#10;• Inconsistent (70-84%)&#10;• Needs Focus (<70%)"
        tip_protein = "Overall Calorie-Weighted Protein Density (Total Protein/Total Calories).&#10;Statuses:&#10;• Lean (≥10.5%)&#10;• Moderate (9.5-10.4%)&#10;• Improve (<9.5%)"
        tip_weight = "14-day weight shift (Week 1 vs Week 2).&#10;Statuses:&#10;• Losing (≥0.8 lbs drop)&#10;• Steady (0-0.7 lbs drop)&#10;• Gaining (Weight increased)"
        tip_logging = "Percentage of days logged in the past 14 days.&#10;Statuses:&#10;• Consistent (≥80%)&#10;• Partial (60-79%)&#10;• Sparse (<60%)"

        display_weight = -drivers['weight_shift'] if drivers['weight_shift'] != 0 else 0.0

        drivers_html = f"""
        <div class="driver-grid">
            <div class="driver-card" title="{tip_protocol}">{icon_check}<div class="driver-label">Protocol Success</div><div class="driver-value">{drivers['adherent_days']:g}/{drivers['total_days']} Days</div><div class="driver-status {adherence_status}">{adherence_msg}</div></div>
            <div class="driver-card" title="{tip_protein}">{icon_zap}<div class="driver-label">Avg Protein</div><div class="driver-value">{drivers['avg_density']:.1f}%</div><div class="driver-status {density_status}">{density_msg}</div></div>
            <div class="driver-card" title="{tip_weight}">{icon_trend}<div class="driver-label">Weight Trend</div><div class="driver-value">{display_weight:+.1f} lbs</div><div class="driver-status {weight_status}">{weight_msg}</div></div>
            <div class="driver-card" title="{tip_logging}">{icon_clip}<div class="driver-label">Logging Consistency</div><div class="driver-value">{int(logging_pct)}%</div><div class="driver-status {logging_status}">{logging_msg}</div></div>
        </div>
        """
        st.markdown(drivers_html, unsafe_allow_html=True)

        tip_text = "Your plan is perfectly calibrated! Maintain this consistency to reach your goal."
        if logging_pct < 70:
            tip_text = "💡 **Coach's Tip:** Data is thin. Log at least 5 more days to unlock higher scoring precision."
        elif adherence_pct < 75:
            tip_text = "💡 **Coach's Tip:** You're drifting from the calorie lid. Focus on pre-logging meals to stay under target."
        elif drivers['avg_density'] < 10.0:
            tip_text = "💡 **Coach's Tip:** Protein density is low. Swapping one carb heavy side for lean protein will jumpstart your score."
        elif drivers['weight_shift'] < 0.2:
            tip_text = "💡 **Coach's Tip:** The scale is steady. If you want faster fat loss, try lowering your Calorie Lid by 100."
        st.markdown(f'<div class="coach-tip">{tip_text}</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
    else:
        st.info(f"📊 Effectiveness Calibrating: {msg or 'Gathering more bio-data...'}")

    # Weekly History Table
    render_section_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-activity"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>', "Trailing 7 Days")
    if not df_7days.empty:
        st.dataframe(df_7days[["Date", "Calories", "Protein", "Density"]], width="stretch", hide_index=True)
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

    # --- Timeline History Section ---
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
    render_page_header('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="lucide lucide-settings"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>', "Configuration")

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

        st.divider()
        st.markdown("#### Sync Utility")
        st.info("Force a re-sync of the Plan Effectiveness logs to the Google Sheet. This will overwrite existing entries for the last 14 days with the current math.")
        if st.button("Manual Sync Logs", type="primary", use_container_width=True):
            with st.status("Syncing logs...", expanded=True) as status:
                sync_plan_effectiveness_logs(force_resync=True)
                status.update(label="Sync Complete!", state="complete", expanded=False)
            st.success("Log sync completed successfully!")


    # End of view-specific content
