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

st.sidebar.header("👁️ Display Options")
display_options = st.sidebar.multiselect(
    "Select Views to Display",
    options=[
        "Ranked Price Stays Table",
        "PDB STAY PRICE Profile",
        "PDB ALL Price",
        "Price / Volume History"
    ],
    default=[
        "Ranked Price Stays Table",
        "PDB ALL Price",
        "Price / Volume History"
    ]
)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data_with_vol(selected_date):
    start_of_day = datetime.combine(selected_date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end_of_day = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)
    query = {"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}
    cursor = collection.find(query).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))
    if df.empty:
        return df
    df["captured_at"] = pd.to_datetime(df["captured_at"], errors='coerce', utc=True).dt.tz_convert(dhaka_tz)
    df = df.sort_values(["TRADING CODE", "captured_at"])
    
    # EXCEL LOGIC: dv = (C3 - C2)
    df["DV"] = df.groupby("TRADING CODE")["VOLUME"].diff().fillna(0)
    df["DV"] = df["DV"].clip(lower=0)
    return df

full_day_df = get_daily_data_with_vol(sel_date)

if not full_day_df.empty:
    dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz)
    dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz)
    mask = (full_day_df["captured_at"] >= dt_start) & (full_day_df["captured_at"] <= dt_end)
    raw_df = full_day_df.loc[mask].copy()
else:
    raw_df = pd.DataFrame()

# ---------------- ANALYSIS ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"] = group["price_changed"].cumsum()
        for _, stay_group in group.groupby("stay_id"):
            if len(stay_group) >= 1:
                summary.append({
                    "Stock": stock,
                    "Price": stay_group["LTP*"].iloc[0],
                    "Stay (Mins)": round((stay_group["captured_at"].iloc[-1] - stay_group["captured_at"].iloc[0]).total_seconds() / 60, 1),
                    "Vol Traded": int(stay_group["DV"].sum()),
                    "Start": stay_group["captured_at"].iloc[0].strftime("%H:%M"),
                    "End": stay_group["captured_at"].iloc[-1].strftime("%H:%M")
                })
analysis_df = pd.DataFrame(summary) if summary else pd.DataFrame()

# ---------------- RANKED TABLE ----------------
if "Ranked Price Stays Table" in display_options and not analysis_df.empty:
    st.subheader("📋 Ranked Price Stays")
    st.dataframe(analysis_df.sort_values("Stay (Mins)", ascending=False), use_container_width=True, hide_index=True)

# ---------------- STOCK SELECTION ----------------
stock_list = sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty else ["No Data"]
selected_stock = st.selectbox("🔍 Select Stock", stock_list)
df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock] if selected_stock != "No Data" else pd.DataFrame()

# ---------------- GRAPHS ----------------
if "PDB STAY PRICE Profile" in display_options and not df_sub.empty:
    st.subheader(f"📊 PDB STAY PRICE Profile — {selected_stock}")
    prof = analysis_df[analysis_df["Stock"] == selected_stock].groupby("Price").agg({"Vol Traded": "sum", "Stay (Mins)": "sum"}).reset_index()
    fig = go.Figure()
    fig.add_trace(go.Bar(y=prof["Price"], x=prof["Stay (Mins)"], orientation="h", name="Stay (Mins)", marker_color="#EF553B"))
    fig.add_trace(go.Bar(y=prof["Price"], x=prof["Vol_Traded"], orientation="h", name="Volume", marker_color="#636EFA", base=prof["Stay (Mins)"]))
    fig.update_layout(barmode="stack", template="plotly_dark", height=500)
    st.plotly_chart(fig, use_container_width=True)

if "PDB ALL Price" in display_options and not df_sub.empty:
    st.subheader(f"📊 PDB ALL Price — {selected_stock}")
    full_profile = df_sub.groupby("LTP*").agg(Vol_Traded=("DV", "sum"), Stay_Count=("captured_at", "count")).reset_index()
    fig = go.Figure()
    fig.add_trace(go.Bar(y=full_profile["LTP*"], x=full_profile["Stay_Count"], orientation="h", name="Stay (Ticks)", marker_color="#EF553B"))
    fig.add_trace(go.Bar(y=full_profile["LTP*"], x=full_profile["Vol_Traded"], orientation="h", name="Volume", marker_color="#00CC96", base=full_profile["Stay_Count"]))
    fig.update_layout(barmode="stack", template="plotly_dark", height=500)
    st.plotly_chart(fig, use_container_width=True)

if "Price / Volume History" in display_options and not df_sub.empty:
    st.subheader(f"⏱️ Price / Volume History — {selected_stock}")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_sub["captured_at"], y=df_sub["LTP*"], name="Price", line=dict(color="#00CC96")))
    fig.add_trace(go.Bar(x=df_sub["captured_at"], y=df_sub["DV"], name="Volume Delta", yaxis="y2", opacity=0.6, marker_color="#636EFA"))
    fig.update_layout(template="plotly_dark", yaxis2=dict(overlaying="y", side="right"))
    st.plotly_chart(fig, use_container_width=True)
