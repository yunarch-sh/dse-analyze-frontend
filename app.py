import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.graph_objects as go
from datetime import datetime, time
import pytz
from streamlit_autorefresh import st_autorefresh

# ---------------- GLOBAL SETTINGS ----------------
dhaka_tz = pytz.timezone("Asia/Dhaka")
now_dhaka = datetime.now(dhaka_tz)

st.set_page_config(
    page_title="DSE Alpha Tracker",
    layout="wide",
)

st.markdown("""
<style>
.main-header {
    padding: 20px 30px;
    margin-bottom: 25px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    border-bottom: 1px solid #444;
}
.header-left {
    display: flex;
    flex-direction: column;
}
.project-title {
    font-size: 32px;
    font-weight: 700;
    color: #4A90E2 !important;
    margin: 0;
}
.project-subtitle {
    font-size: 16px;
    font-weight: 500;
    color: #E74C3C;
    margin: 4px 0 0 0;
}
.header-right {
    font-size: 12px;
    color: #27AE60;
    text-align: right;
    border-left: 1px solid #444;
    padding-left: 15px;
}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="main-header">
    <div class="header-left">
        <h1 class="project-title">DSE ALPHA TRACKER</h1>
        <p class="project-subtitle">POC • PDB</p>
    </div>
    <div class="header-right">
        {now_dhaka.strftime('%d %b %Y | %H:%M:%S')}
    </div>
</div>
""", unsafe_allow_html=True)

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
            if (username == st.secrets["LOGIN"]["LOGIN_USER"] and password == st.secrets["LOGIN"]["LOGIN_PASS"]):
                st.session_state["password_correct"] = True
                st.rerun()
            else:
                st.error("Invalid credentials")
    return False

if not check_password():
    st.stop()

# ---------------- DATABASE ----------------
@st.cache_resource
def init_connection():
    client = MongoClient(st.secrets["MONGO"]["MONGO_URI"])
    db = client["DSE_Market_Data"]
    return db["price_logs"]

collection = init_connection()

# ---------------- SIDEBAR ----------------
st.sidebar.header("⏳ Filter Data")

# 🔄 Manual refresh
if st.sidebar.button("🔄 Refresh Now"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.divider()

# ⚡ Auto refresh toggle
auto_refresh = st.sidebar.toggle("⚡ Auto Refresh", value=False)

refresh_interval = 60
if auto_refresh:
    refresh_interval = st.sidebar.number_input(
        "Refresh Interval (seconds)",
        min_value=10,
        max_value=3600,
        value=60,
        step=10
    )
    st_autorefresh(interval=refresh_interval * 1000, key="auto_refresh")
    st.sidebar.success(f"Auto refresh every {refresh_interval}s")
else:
    st.sidebar.info("Manual refresh mode")

st.sidebar.divider()

# Filters
sel_date = st.sidebar.date_input("Select Date", now_dhaka)

t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10, 0), time(14, 30)),
    format="HH:mm",
)

dt_start = dhaka_tz.localize(datetime.combine(sel_date, t_start)).astimezone(pytz.UTC)
dt_end = dhaka_tz.localize(datetime.combine(sel_date, t_end)).astimezone(pytz.UTC)

display_start = dt_start.astimezone(dhaka_tz).strftime("%H:%M")
display_end = dt_end.astimezone(dhaka_tz).strftime("%H:%M")

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_filtered_data(start, end):
    query = {"captured_at": {"$gte": start, "$lte": end}}
    cursor = collection.find(query).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    df["captured_at"] = pd.to_datetime(df["captured_at"], errors='coerce')
    df["captured_at"] = df["captured_at"].apply(
        lambda x: x.tz_convert("UTC") if pd.notnull(x) and x.tzinfo else (x.tz_localize("UTC") if pd.notnull(x) else x)
    )
    df["captured_at"] = df["captured_at"].dt.tz_convert(dhaka_tz)
    return df

raw_df = get_filtered_data(dt_start, dt_end)

# ---------------- ANALYSIS ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2:
            continue

        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"] = group["price_changed"].cumsum()

        for _, stay_group in group.groupby("stay_id"):
            if len(stay_group) < 2:
                continue

            duration = (stay_group["captured_at"].iloc[-1] - stay_group["captured_at"].iloc[0]).total_seconds() / 60
            vol_diff = int(stay_group["VOLUME"].iloc[-1] - stay_group["VOLUME"].iloc[0])

            if vol_diff > 0:
                summary.append({
                    "Stock": stock,
                    "Price": float(stay_group["LTP*"].iloc[0]),
                    "Stay (Mins)": round(duration, 1),
                    "Vol Traded": vol_diff,
                    "Start": stay_group["captured_at"].iloc[0].strftime("%H:%M"),
                    "End": stay_group["captured_at"].iloc[-1].strftime("%H:%M"),
                })

analysis_df = pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False) if summary else pd.DataFrame()

# ---------------- TABLE ----------------
st.subheader("📋 Ranked Price Stays")
st.dataframe(analysis_df, use_container_width=True)

# ---------------- STOCK VIEW ----------------
stock_list = (
    sorted(analysis_df["Stock"].unique())
    if not analysis_df.empty else ["No Data"]
)

selected_stock = st.selectbox("Select Stock", stock_list)

if selected_stock != "No Data":
    df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock]

    st.subheader(f"📊 {selected_stock}")

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_sub["captured_at"], y=df_sub["LTP*"], name="Price"))
    fig.add_trace(go.Bar(x=df_sub["captured_at"], y=df_sub["VOLUME"], name="Volume", yaxis="y2"))

    fig.update_layout(
        template="plotly_dark",
        yaxis2=dict(overlaying="y", side="right")
    )

    st.plotly_chart(fig, use_container_width=True)

st.caption(f"Range: {display_start} to {display_end} | Dhaka Time")
