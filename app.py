import streamlit as st
from google import genai
import gspread
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
import re
import time

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

# --- Function Definitions ---
@st.cache_resource
def get_chat_session(model_id, history=None):
    # Determine the best thinking configuration for the model
    config_params = {"system_instruction": SYSTEM_PROMPT}

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
    .main {
        background-color: #f8f9fa;
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
    header[data-testid="stHeader"] {
        background: #1E1E1E !important;
        color: #00A6FF !important;
        border-bottom: 1px solid #00A6FF;
    }
    header[data-testid="stHeader"] * {
        color: #00A6FF !important;
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
    .stButton button {
        border-radius: 20px;
        font-weight: 600;
    }
    .block-container {
        padding-top: 5rem !important;
        padding-bottom: 0rem !important;
    }
    .stChatFloatingInputContainer {
        bottom: 20px !important;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 2. API Setup ---
@st.cache_resource
def get_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

client = get_client()

# --- Database Setup ---
@st.cache_data(ttl=60)
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

def log_to_sheet(item, calories, protein, density):
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
        
        worksheet.append_row([today, item, calories, protein, density, week_num])
        return True
    except Exception as e:
        st.error(f"Failed to log to database: {e}")
        return False

# --- Chat Persistence Helpers ---
CHAT_HISTORY_WS = "Chat_History"

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
            if len(row) < 3: continue
            ts, role, parts_json = row
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
        return True
    except:
        return False

@st.cache_data(ttl=300)
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
            if len(row) >= 2:
                label, content = row
                if content.strip():
                    instructions.append(f"### {label}\n{content}")
        return "\n\n".join(instructions)
    except:
        return ""

# --- 3. System Prompt (The Rules Engine) ---
def get_system_prompt(schedule, custom_instructions=""):
    formatted_schedule = "\n".join([f"- {day}: {times['start']} to {times['end']}" if times['start'] else f"- {day}: Fasting / Skip" for day, times in schedule.items()])
    return f"""
You are the RatioTen Assistant, a precise, analytical, and highly supportive nutrition tracker.
Primary Quality Metric: Protein Density (Goal: 10.0%).
- Calculated explicitly as: (Protein in grams / Total Calories).
- For example: an item with 150 calories and 30g protein has a density of 30 / 150 = 0.20 = 20.0%.

Remote Custom Instructions (Source of Truth):
{custom_instructions}

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
- When logging food, display ONLY ONE table with the items, but you MUST also include conversational banter as described below.
  1. Current Day's Items: (Item Name, Cals, Protein, Density)

Daily Targets & Banter (REQUIRED):
- Goal: <= 1500 Calories, >= 150g Protein, Density Target: >= 10.0%.
- You are a conversational, supportive, and analytical AI.
- Below the data table, you MUST evaluate each logged item and the overall progress for the day against these targets.
- Include encouraging banter about how the day is shaping up, if the item is a good choice for aligning with the goals, and suggest adjustments if needed.
- On Sundays (weekly wrap-up), provide a brief encouraging summary.

Daily 6:00 PM Wrap-Up (Creatine Check):
- Check logs for "protein shake" or "ultra-filtered shake".
- If present: Assume creatine was taken. No reminder.
- If missing: Remind me to take creatine. Suggest mixing it with a Prime hydration drink.

JSON Output for Database Logging:
- When the user logs food items, you MUST append a JSON block at the very end of your response containing a list of objects for EACH item logged.
- Format exactly like this:
```json
[
  {{"item": "Food Name", "calories": 150, "protein": 30, "density": "20.0%"}}
]
```
- Only include the JSON block if new food is being logged.
"""

fasting_schedule = get_fasting_schedule()
custom_instructions = get_custom_instructions()
SYSTEM_PROMPT = get_system_prompt(fasting_schedule, custom_instructions)

# --- 4. Sidebar & Profile ---
with st.sidebar:
    try:
        st.image("logo.png", width="stretch")
    except:
        pass
    st.header("⚙️ RatioTen Protocol")
    st.info("""
    **Standard Protocol:**
    18:6 Fast (12PM - 6PM)
    
    **Special Days:**
    - Mon: 42hr "Skip Day"
    - Fri: OMAD @ 6PM
    
    **Daily Targets:**
    - Cals: <= 1500
    - Protein: >= 150g
    - Density: >= 10.0%
    """)
    st.divider()
    st.subheader("🧭 Navigation")
    st.session_state.view_selection = st.radio(
        "Select View",
        ["🍽️ Log", "📊 Analytics"],
        label_visibility="collapsed"
    )
    st.divider()
    st.subheader("📷 Meal Capture")
    if st.button("📷 Open Camera", use_container_width=True):
        st.session_state.show_camera = not st.session_state.show_camera
    
    if st.session_state.show_camera:
        captured_file = st.camera_input("Capture your meal")
        if captured_file:
            st.session_state.pending_image = captured_file.getvalue()
            st.success("Photo attached!")
    
    if st.session_state.pending_image:
        st.info("✅ Image ready to send")
        if st.button("🗑️ Discard Photo", use_container_width=True):
            st.session_state.pending_image = None
            st.rerun()

    st.divider()
    if st.button("🗑️ Clear Chat History", help="Permanently clear the persistent thread from Google Sheets"):
        if clear_persistent_chat():
            st.session_state.messages = [{"role": "assistant", "content": "Thread cleared. Ready to start fresh!"}]
            st.session_state.chat_session = get_chat_session(st.session_state.current_model)
            st.success("History wiped!")
            st.rerun()

# --- 4. Main View Logic ---
if st.session_state.view_selection == "🍽️ Log":
    # --- 4. Modernized Dashboard (Log View) ---
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
            <div class="metric-delta {'delta-green' if cals <= 1500 else 'delta-red'}">
                {f'↑ {1500 - cals} left' if cals <= 1500 else f'↓ {cals - 1500} over'}
            </div>
        </div>
        <div class="metric-card">
            <div class="metric-label">Protein</div>
            <div class="metric-value">{protein}g</div>
            <div class="metric-delta {'delta-green' if protein >= 150 else 'delta-red'}">
                {f'↑ {protein - 150}g' if protein >= 150 else f'↓ {150 - protein}g left'}
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
    st.divider()

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
        st.session_state.chat_session = get_chat_session(st.session_state.current_model)

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
    # Status indicator for pending image
    if st.session_state.pending_image:
        st.markdown("""
        <div style="background-color: #1E3A5F; padding: 10px; border-radius: 5px; margin-bottom: 10px; border-left: 5px solid #00A6FF;">
            📷 <b>Photo Attached:</b> Describing your meal below will submit both the text and the photo.
        </div>
        """, unsafe_allow_html=True)

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
                for attempt in range(max_retries):
                    try:
                        if st.session_state.current_model != PRIMARY_MODEL and not success:
                            existing_session = st.session_state.get("chat_session")
                            existing_history = getattr(existing_session, "history", getattr(existing_session, "_history", None)) if existing_session else None
                            st.session_state.current_model = PRIMARY_MODEL
                            st.session_state.chat_session = get_chat_session(PRIMARY_MODEL, history=existing_history)

                        response = st.session_state.chat_session.send_message(message_content)
                        success = True
                        break
                    except Exception as e:
                        error_msg = str(e).upper()
                        if "503" in error_msg or "UNAVAILABLE" in error_msg:
                            if attempt < max_retries - 1:
                                time.sleep(retry_delay)
                                retry_delay *= 2
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
                        st.session_state.chat_session = get_chat_session(SECONDARY_MODEL, history=existing_history)
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
                        st.session_state.chat_session = get_chat_session(STABLE_MODEL, history=existing_history)
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
                    if response.candidates and response.candidates[0].thought:
                        with st.expander("💭 Thinking Process", expanded=False):
                            st.markdown(response.candidates[0].thought)
                    
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
                                               data.get("density", "0%")):
                                    st.toast(f"Logged: {data.get('item')}")
                            get_trailing_7_days_data.clear()
                        except Exception as e:
                            st.error(f"Logging error: {e}")
                    
                    # Persistence & Cleanup
                    st.session_state.messages.append({"role": "assistant", "content": display_text})
                    log_chat_to_sheet("assistant", display_text)
                    st.session_state.pending_image = None
                    st.session_state.show_camera = False
                    st.rerun()

else:
    # --- Analytics View ---
    st.subheader("📊 Performance Analytics")
    df_7days = get_trailing_7_days_data()
    
    # Weekly History Table
    st.markdown("#### Trailing 7 Days")
    if not df_7days.empty:
        st.dataframe(df_7days, width="stretch", hide_index=True)
    else:
        st.info("No logs found for the trailing 7 days.")
    
    st.info("💡 More graphs and insights coming soon!")
    st.divider()

    # End of view-specific content
