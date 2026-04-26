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
            <p style="margin:0; color:#E74C3C;">POC • PDB</p>
        </div>
        <div style="text-align:right; font-size:14px; color:#27AE60;">
            {now_dhaka.strftime('%d %b %Y | %H:%M:%S')}
        </div>
    </div>
    <hr>
    """, unsafe_allow_html=True)

render_header()

# ---------------- AUTH ----------------
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
            else:
                st.error("Invalid credentials")
    return False

if not check_password():
    st.stop()

# ---------------- DB ----------------
@st.cache_resource
def init_connection():
    client = MongoClient(st.secrets["MONGO"]["MONGO_URI"])
    return client["DSE_Market_Data"]["price_logs"]

collection = init_connection()

# ---------------- SIDEBAR ----------------
st.sidebar.header("⏳ Filter Data")
sel_date = st.sidebar.date_input("Select Date", datetime.now(dhaka_tz))
t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10,0), time(14,30)),
    format="HH:mm"
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)

display_start = dt_start.astimezone(dhaka_tz).strftime("%H:%M")
display_end = dt_end.astimezone(dhaka_tz).strftime("%H:%M")

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.experimental_rerun()

# ---------------- GRAPH SELECTOR ----------------
st.sidebar.header("📊 Display Options")

graph_choices = [
    "PDB STAY PRICE Profile",
    "PDB ALL Price",
    "Price / Volume History"
]

default_graphs = [
    "PDB ALL Price",
    "Price / Volume History"
]

if "selected_graphs" not in st.session_state:
    st.session_state["selected_graphs"] = default_graphs

selected_graphs = st.sidebar.multiselect(
    "Select Graphs",
    graph_choices,
    default=st.session_state["selected_graphs"]
)

st.session_state["selected_graphs"] = selected_graphs

# ---------------- DATA ----------------
@st.cache_data(ttl=60)
def get_filtered_data(start, end):
    query = {"captured_at": {"$gte": start, "$lte": end}}
    df = pd.DataFrame(list(collection.find(query).sort("captured_at", 1)))
    if df.empty:
        return df
    df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
    return df

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()

raw_df = get_filtered_data(dt_start, dt_end)

# ---------------- PREPROCESS ----------------
if not raw_df.empty:
    raw_df = raw_df.sort_values(["TRADING CODE", "captured_at"])
    raw_df["VOL_DIFF"] = raw_df.groupby("TRADING CODE")["VOLUME"].diff().fillna(0).clip(lower=0)

# ---------------- ANALYSIS ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        group = group.copy()
        group["stay_id"] = (group["LTP*"] != group["LTP*"].shift()).cumsum()
        for _, g in group.groupby("stay_id"):
            if len(g) < 2:
                continue
            vol = int(g["VOL_DIFF"].sum())
            if vol <= 0:
                continue
            summary.append({
                "Stock": stock,
                "Price": float(g["LTP*"].iloc[0]),
                "Stay (Mins)": round((g["captured_at"].iloc[-1] - g["captured_at"].iloc[0]).total_seconds()/60,1),
                "Vol Traded": vol,
                "Start": g["captured_at"].iloc[0].strftime("%H:%M"),
                "End": g["captured_at"].iloc[-1].strftime("%H:%M")
            })

analysis_df = pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False) if summary else pd.DataFrame()

# ---------------- TABLE ----------------
st.subheader("📋 Ranked Price Stays")
st.dataframe(analysis_df, width='stretch', hide_index=True)
st.divider()

# ---------------- STOCK SELECT ----------------
stock_list = (
    sorted(analysis_df["Stock"].unique()) if not analysis_df.empty
    else sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty
    else ["No Data"]
)

selected_stock = st.selectbox("Select Stock", stock_list)

df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock].copy() if not raw_df.empty else pd.DataFrame()
df_sub = df_sub[df_sub["LTP*"] > 0] if not df_sub.empty else df_sub
total_volume = int(df_sub["VOL_DIFF"].sum()) if not df_sub.empty else 0

# ---------------- GRAPH 1 ----------------
if "PDB STAY PRICE Profile" in selected_graphs and not df_sub.empty:
    st.subheader(f"PDB STAY PRICE Profile — {selected_stock}")
    profile = analysis_df[analysis_df["Stock"]==selected_stock].groupby("Price").agg({"Vol Traded":"sum","Stay (Mins)":"sum"}).reset_index()

    fig = go.Figure()
    fig.add_bar(y=profile["Price"], x=profile["Stay (Mins)"], orientation='h', name="Time")
    fig.add_bar(y=profile["Price"], x=profile["Vol Traded"], orientation='h', name="Volume", base=profile["Stay (Mins)"])
    st.plotly_chart(fig, width='stretch')

# ---------------- GRAPH 2 ----------------
if "PDB ALL Price" in selected_graphs and not df_sub.empty:
    st.subheader(f"PDB ALL Price — {selected_stock}")
    full = df_sub.groupby("LTP*").agg(Vol=("VOL_DIFF","sum"), Count=("captured_at","count")).reset_index()

    fig = go.Figure()
    fig.add_bar(y=full["LTP*"], x=full["Count"], orientation='h', name="Time")
    fig.add_bar(y=full["LTP*"], x=full["Vol"], orientation='h', name="Volume", base=full["Count"])
    st.plotly_chart(fig, width='stretch')

# ---------------- GRAPH 3 ----------------
if "Price / Volume History" in selected_graphs and not df_sub.empty:
    st.subheader(f"Price / Volume History — {selected_stock}")
    fig = go.Figure()
    fig.add_scatter(x=df_sub["captured_at"], y=df_sub["LTP*"], name="Price")
    fig.add_bar(x=df_sub["captured_at"], y=df_sub["VOL_DIFF"], name="Volume", yaxis="y2")

    fig.update_layout(
        yaxis2=dict(overlaying="y", side="right")
    )
    st.plotly_chart(fig, width='stretch')

st.caption(f"Range: {display_start} to {display_end}")
