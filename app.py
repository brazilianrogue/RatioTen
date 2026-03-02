import streamlit as st
from google import genai
import gspread
import pandas as pd
from datetime import datetime, timedelta
import json
import re

# --- 1. Page Configuration & Custom CSS ---
st.set_page_config(page_title="Nutrition Tracker", page_icon="🍏", layout="centered")

st.markdown("""
    <style>
    .main {
        background-color: #f8f9fa;
    }
    .stMetric {
        background-color: #31333F;
        color: white !important;
        padding: 8px !important;
        border-radius: 8px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        min-height: 100px;
        display: flex !important;
        flex-direction: column !important;
        justify-content: flex-start !important;
        align-items: flex-start !important;
    }
    .stMetric [data-testid="stMetricValue"] {
        color: white !important;
        font-size: 1.1rem !important;
        line-height: 1.2 !important;
    }
    .stMetric [data-testid="stMetricLabel"] {
        color: #e0e0e0 !important;
        font-size: 0.7rem !important;
        margin-bottom: 2px !important;
    }
    .stMetric [data-testid="stMetricDelta"] {
        font-size: 0.7rem !important;
    }
    div[data-testid="column"] {
        padding: 0 2px !important;
        flex: 1 1 32% !important;
        max-width: 33% !important;
        min-width: 0 !important;
    }
    [data-testid="stMetricValue"] {
        overflow-wrap: anywhere !important;
    }
    div[data-testid="stHorizontalBlock"] {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: flex-start !important;
        gap: 4px !important;
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
        background: rgba(255, 255, 255, 0.8);
        backdrop-filter: blur(10px);
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
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.sheet1
        
        values = worksheet.get_all_values()
        if not values:
            return pd.DataFrame()
            
        df = pd.DataFrame(values)
        if df.iloc[0, 0] in ["Date", "date", "Time", "timestamp", "today"]:
            df.columns = df.iloc[0]
            df = df[1:]
        else:
            df.columns = ["Date", "Item", "Calories", "Protein", "Density"]
            
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce').dt.date
        df['Calories'] = pd.to_numeric(df['Calories'], errors='coerce').fillna(0)
        df['Protein'] = pd.to_numeric(df['Protein'], errors='coerce').fillna(0)
        
        seven_days_ago = (datetime.now() - timedelta(days=7)).date()
        df = df[df['Date'] >= seven_days_ago]
        
        if df.empty:
            return pd.DataFrame()
            
        daily_summary = df.groupby('Date')[['Calories', 'Protein']].sum().reset_index()
        daily_summary['Density'] = (daily_summary['Protein'] / daily_summary['Calories']) * 100
        daily_summary['Density'] = daily_summary['Density'].fillna(0).apply(lambda x: f"{x:.1f}%")
        
        daily_summary = daily_summary.sort_values(by='Date', ascending=False)
        daily_summary['Date'] = daily_summary['Date'].astype(str)
        return daily_summary
        
    except Exception as e:
        return pd.DataFrame()

def log_to_sheet(item, calories, protein, density):
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(credentials_dict)
        sh = gc.open("Nutrition_Logs")
        worksheet = sh.sheet1
        today = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        worksheet.append_row([today, item, calories, protein, density])
        return True
    except Exception as e:
        st.error(f"Failed to log to database: {e}")
        return False

# --- 3. System Prompt (The Rules Engine) ---
SYSTEM_PROMPT = """
You are a highly capable, supportive nutrition tracker.
Primary Quality Metric: Protein Density.
- Calculated explicitly as: (Protein in grams / Total Calories).
- For example: an item with 150 calories and 30g protein has a density of 30 / 150 = 0.20 = 20.0%.

Fasting Protocol Strict Adherence:
- Standard: 18-6 Intermittent Fasting (Eating window 12:00 PM to 6:00 PM).
- Monday "Skip Day": 42-hour fast (Stop eating Sunday 6:00 PM, resume Tuesday 12:00 PM).
- Friday OMAD: One Meal a Day at exactly 6:00 PM.

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
  {"item": "Food Name", "calories": 150, "protein": 30, "density": "20.0%"}
]
```
- Only include the JSON block if new food is being logged.
"""

# --- 4. Sidebar & Profile ---
with st.sidebar:
    st.header("⚙️ Protocol & Goals")
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

# --- 4. Modernized Header & Dashboard ---
st.markdown("### 🍏 **Nutrition** & Fasting")

df_7days = get_trailing_7_days_data()

# Calculate today's metrics
today_str = datetime.now().strftime("%Y-%m-%d")
today_data = df_7days[df_7days['Date'] == today_str]

if not today_data.empty:
    cals = int(today_data.iloc[0]['Calories'])
    protein = int(today_data.iloc[0]['Protein'])
    density = today_data.iloc[0]['Density']
else:
    cals, protein, density = 0, 0, "0.0%"

# Metric Row
m1, m2, m3 = st.columns(3)
m1.metric("Calories", f"{cals}", f"{1500 - cals} left" if cals < 1500 else f"+{cals - 1500} over", delta_color="inverse")
m2.metric("Protein", f"{protein}g", f"{protein - 150}g" if protein >= 150 else f"{protein - 150}g", delta_color="normal")
m3.metric("Density", density, delta=None)

# Collapsible Weekly History
with st.expander("📊 Weekly History", expanded=False):
    if not df_7days.empty:
        st.dataframe(df_7days, use_container_width=True, hide_index=True)
    else:
        st.info("No logs found for the trailing 7 days.")

st.divider()

# --- 5. Initialize Chat History & Session State ---
if "messages" not in st.session_state:
    st.session_state.messages = [
        {"role": "assistant", "content": "Ready to log. What are we eating?"}
    ]

if "chat_session" not in st.session_state:
    st.session_state.chat_session = client.chats.create(
        model="gemini-3-flash-preview",
        config=genai.types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
        )
    )

if "show_camera" not in st.session_state:
    st.session_state.show_camera = False

if "pending_image" not in st.session_state:
    st.session_state.pending_image = None

# Display previous chat messages
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        content = message["content"]
        if isinstance(content, list):
            for item in content:
                st.write(item)
        else:
            st.markdown(content)

# --- 6. Chat Input & Camera Support ---
col1, col2 = st.columns([1, 4])
with col1:
    if st.button("📷 Photo"):
        st.session_state.show_camera = not st.session_state.show_camera

if st.session_state.show_camera:
    captured_file = st.camera_input("Capture your meal")
    if captured_file:
        st.session_state.pending_image = captured_file.getvalue()
        st.success("Photo captured! Now add a description below to submit.")

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
    
    # Show user message in the UI
    with st.chat_message("user"):
        for item in ui_content:
            st.write(item)
            if item == "📷 *Photo attached*" and image_to_send:
                st.image(image_to_send)
    
    st.session_state.messages.append({"role": "user", "content": ui_content})
    
    # 2. Send message (text + image) to Gemini and get response
    with st.chat_message("assistant"):
        with st.spinner("Analyzing meal..."):
            response = st.session_state.chat_session.send_message(message_content)
            
            # Remove JSON block from the displayed text so the user doesn't see it
            display_text = re.sub(r'```json\n.*?\n```', '', response.text, flags=re.DOTALL).strip()
            st.markdown(display_text)
            
            # Parse JSON block if present to log to sheet
            logged_something = False
            match = re.search(r'```json\n(.*?)\n```', response.text, re.DOTALL)
            if match:
                try:
                    items_to_log = json.loads(match.group(1))
                    for data in items_to_log:
                        success = log_to_sheet(data.get("item", "Unknown"), data.get("calories", 0), data.get("protein", 0), data.get("density", "0%"))
                        if success:
                            st.toast(f"Logged to sheet: {data.get('item')}")
                            logged_something = True
                except Exception as e:
                    st.error(f"Failed to parse or log items: {e}")
            
            # Save assistant response to UI history BEFORE rerunning
            st.session_state.messages.append({"role": "assistant", "content": display_text})
            
            if logged_something:
                get_trailing_7_days_data.clear()
            
            # Reset camera and pending image after successful submission
            st.session_state.pending_image = None
            st.session_state.show_camera = False
            st.rerun()