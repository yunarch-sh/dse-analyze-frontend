import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, time
import pytz
from streamlit_autorefresh import st_autorefresh

# ---------------- GLOBAL SETTINGS ----------------
dhaka_tz = pytz.timezone("Asia/Dhaka")

st.set_page_config(
    page_title="DSE Alpha Tracker",
    layout="wide",
)

st_autorefresh(interval=60000, key="refresh")


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
            if (username == st.secrets["LOGIN_USER"] and password == st.secrets["LOGIN_PASS"]):
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
    return MongoClient(st.secrets["MONGO_URI"])

try:
    client = init_connection()
    db = client["DSE_Market_Data"]
    collection = db["price_logs"]
except Exception as e:
    st.error(f"MongoDB Connection Failed: {e}")
    st.stop()


# ---------------- SIDEBAR FILTERS ----------------
st.sidebar.header("⏳ Filter Data")
now_dhaka = datetime.now(dhaka_tz)
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
    df["captured_at"] = pd.to_datetime(df["captured_at"])
    if df["captured_at"].dt.tz is None:
        df["captured_at"] = df["captured_at"].dt.tz_localize("UTC")
    df["captured_at"] = df["captured_at"].dt.tz_convert(dhaka_tz)
    return df

raw_df = get_filtered_data(dt_start, dt_end)


# ---------------- PRICE STAY ANALYSIS ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2: continue
        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"] = group["price_changed"].cumsum()

        for stay_id, stay_group in group.groupby("stay_id"):
            if len(stay_group) < 2: continue
            price = float(stay_group["LTP*"].iloc[0])
            start_t = stay_group["captured_at"].iloc[0]
            end_t = stay_group["captured_at"].iloc[-1]
            duration = (end_t - start_t).total_seconds() / 60
            vol_diff = int(stay_group["VOLUME"].iloc[-1] - stay_group["VOLUME"].iloc[0])

            if vol_diff > 0:
                summary.append({
                    "Stock": stock, "Price": price, "Stay (Mins)": round(duration, 1),
                    "Vol Traded": vol_diff, "Start": start_t.strftime("%H:%M"), "End": end_t.strftime("%H:%M"),
                })

if summary:
    analysis_df = pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False)
else:
    analysis_df = pd.DataFrame(columns=["Stock", "Price", "Stay (Mins)", "Vol Traded", "Start", "End"])


# ---------------- MAIN VIEW
