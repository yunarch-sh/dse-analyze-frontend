import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.graph_objects as go
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# ---------------- GLOBAL SETTINGS ----------------
dhaka_tz = ZoneInfo("Asia/Dhaka")
st.set_page_config(page_title="DSE Alpha Tracker", layout="wide")

# ---------------- HEADER ----------------
def render_header():
    now_dhaka = datetime.now(dhaka_tz)
    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:center;font-family:sans-serif;">
        <div>
            <h1 style="margin:0; color:#4A90E2;">DSE ALPHA TRACKER</h1>
            <p style="margin:0; color:#E74C3C;">EXCEL SYNC MODE</p>
        </div>
        <div style="text-align:right; font-size:14px; color:#27AE60;">
            {now_dhaka.strftime('%d %b %Y | %H:%M:%S')}
        </div>
    </div>
    <hr>
    """, unsafe_allow_html=True)

render_header()

# ---------------- AUTH SYSTEM ----------------
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]:
        return True

    with st.form("login"):
        st.subheader("🔐 Access Control")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Login")
        if submitted:
            if (username == st.secrets["LOGIN"]["LOGIN_USER"]
                    and password == st.secrets["LOGIN"]["LOGIN_PASS"]):
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.error("Invalid credentials")
    return False

if not check_password():
    st.stop()

# ---------------- DATABASE CONNECTION ----------------
@st.cache_resource
def init_connection():
    try:
        client = MongoClient(st.secrets["MONGO"]["MONGO_URI"])
        db = client["DSE_Market_Data"]
        collection = db["price_logs"]
        return collection
    except Exception as e:
        st.error(f"MongoDB Connection Failed: {e}")
        st.stop()

collection = init_connection()

# ---------------- SIDEBAR ----------------
st.sidebar.header("⏳ Filter Data")
sel_date = st.sidebar.date_input("Select Date", datetime.now(dhaka_tz))
t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10, 0), time(14, 30)),
    format="HH:mm"
)

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_raw_daily_data(selected_date):
    start_of_day = datetime.combine(selected_date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end_of_day = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)
    
    query = {"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}
    cursor = collection.find(query).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))
    
    if not df.empty:
        df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
    return df

full_df = get_raw_daily_data(sel_date)

if full_df.empty:
    st.warning("No data found for this date.")
    st.stop()

# ---------------- STOCK SELECTION ----------------
all_stocks = sorted(full_df["TRADING CODE"].unique())
selected_stock = st.selectbox("🔍 Select Stock (Excel Step 1)", all_stocks)

# ---------------- EXCEL LOGIC PROCESSING ----------------

# Step 1: Filter for the certain share
df_sub = full_df[full_df["TRADING CODE"] == selected_stock].copy()
df_sub = df_sub.sort_values("captured_at")

# Apply Time Slider Filter
dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz)
df_sub = df_sub[(df_sub["captured_at"] >= dt_start) & (df_sub["captured_at"] <= dt_end)]

if df_sub.empty:
    st.error("No data for this stock in the selected time range.")
    st.stop()

# Step 2: Calculate dv = (C3 - C2). Fill first row with 0 to match Excel starting point.
df_sub["dv"] = df_sub["VOLUME"].diff().fillna(0)

# Step 3: Filter out zeros (Optional for math, but good for display)
# We keep zeros for the dataframe view but they won't affect the sum.

# Step 4 & 5: Sort by price and Sum all dvs for the price (Excel Step 4 & 5)
price_profile = df_sub.groupby("LTP*").agg(
    Total_DV=("dv", "sum"),
    Stay_Count=("captured_at", "count")
).reset_index().sort_values("LTP*")

# ---------------- VISUALIZATION ----------------

# Metrics for Reconciliation
total_sum_dv = price_profile["Total_DV"].sum()
actual_cum_vol = df_sub["VOLUME"].iloc[-1] - df_sub["VOLUME"].iloc[0]

col1, col2, col3 = st.columns(3)
col1.metric("Sum of DV (Excel Match)", f"{total_sum_dv:,.0f}")
col2.metric("Actual Vol (Last-First)", f"{actual_cum_vol:,.0f}")
col3.metric("Status", "✅ MATCHED" if abs(total_sum_dv - actual_cum_vol) < 1 else "⚠️ MISMATCH")

st.subheader(f"📊 Volume Profile: {selected_stock}")

fig = go.Figure()
# Time Stay Bar
fig.add_trace(go.Bar(
    y=price_profile["LTP*"], x=price_profile["Stay_Count"], 
    orientation="h", name="Time Stay (Ticks)", marker_color="#EF553B"
))
# Volume DV Bar
fig.add_trace(go.Bar(
    y=price_profile["LTP*"], x=price_profile["Total_DV"], 
    orientation="h", name="Volume (dv)", marker_color="#636EFA",
    base=price_profile["Stay_Count"]
))

fig.update_layout(
    barmode="stack", template="plotly_dark",
    xaxis_title="Count / Volume", yaxis_title="Price (BDT)",
    height=600, legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center")
)
st.plotly_chart(fig, use_container_width=True)

# Data Table
st.subheader("📋 Price Level Breakdown")
st.dataframe(price_profile.rename(columns={"LTP*": "Price"}), use_container_width=True, hide_index=True)
