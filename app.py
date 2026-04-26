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

# ---------------- SIDEBAR FILTERS ----------------
st.sidebar.header("⏳ Filter Data")
sel_date = st.sidebar.date_input("Select Date", datetime.now(dhaka_tz))
t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10,0), time(14,30)),
    format="HH:mm"
)

st.sidebar.header("👁️ Display Options")
display_options = st.sidebar.multiselect(
    "Select Views to Display",
    options=[
        "Ranked Price Stays Table",
        "PDB STAY PRICE Profile",
        "PDB ALL Price",
        "Excel Approach Profile",
        "Price / Volume History"
    ],
    default=["PDB ALL Price", "Excel Approach Profile", "Price / Volume History"]
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)
display_start = dt_start.astimezone(dhaka_tz).strftime("%H:%M")
display_end = dt_end.astimezone(dhaka_tz).strftime("%H:%M")

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data(selected_date):
    try:
        start_of_day = datetime.combine(selected_date, time(0,0), tzinfo=dhaka_tz).astimezone(timezone.utc)
        end_of_day = datetime.combine(selected_date, time(23,59,59), tzinfo=dhaka_tz).astimezone(timezone.utc)
        
        query = {"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}
        cursor = collection.find(query).sort("captured_at", 1)
        df = pd.DataFrame(list(cursor))
        
        if df.empty: return df
            
        df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
        df = df.sort_values(["TRADING CODE", "captured_at"])
        return df
    except Exception as e:
        st.error(f"Data Fetch Error: {e}")
        return pd.DataFrame()

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# ---------------- CORE LOGIC: PREVENTING OVER-COUNTING ----------------
full_day_df = get_daily_data(sel_date)

if not full_day_df.empty:
    # 1. Calculate deltas for the whole day first
    full_day_df["VOL_DIFF_PDB"] = full_day_df.groupby("TRADING CODE")["VOLUME"].diff().fillna(0)
    full_day_df["VOL_DIFF_EXCEL"] = full_day_df.groupby("TRADING CODE")["VOLUME"].shift(-1) - full_day_df["VOLUME"]
    full_day_df["VOL_DIFF_EXCEL"] = full_day_df["VOL_DIFF_EXCEL"].fillna(0)

    # 2. Apply Time Filter
    mask = (full_day_df["captured_at"] >= dt_start.astimezone(dhaka_tz)) & \
           (full_day_df["captured_at"] <= dt_end.astimezone(dhaka_tz))
    raw_df = full_day_df.loc[mask].copy()

    # 3. THE FIX: Zero out the first delta of the slice for each stock
    # This ensures Sum(Deltas) + Starting_Volume = Actual_Final_Volume
    if not raw_df.empty:
        raw_df["VOL_DIFF_PDB"] = raw_df.groupby("TRADING CODE")["VOL_DIFF_PDB"].transform(
            lambda x: x.where(x.index != x.index[0], 0)
        )
else:
    raw_df = pd.DataFrame()

# ---------------- ANALYSIS & VISUALIZATION ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2: continue
        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"] = group["price_changed"].cumsum()
        
        for _, stay_group in group.groupby("stay_id"):
            if len(stay_group) < 1: continue
            vol_diff = int(stay_group["VOL_DIFF_PDB"].sum())
            if vol_diff >= 0: # Include 0 vol stays if they took time
                summary.append({
                    "Stock": stock,
                    "Price": float(stay_group["LTP*"].iloc[0]),
                    "Stay (Mins)": round((stay_group["captured_at"].iloc[-1] - stay_group["captured_at"].iloc[0]).total_seconds()/60, 1),
                    "Vol Traded": vol_diff,
                    "Start": stay_group["captured_at"].iloc[0].strftime("%H:%M"),
                    "End": stay_group["captured_at"].iloc[-1].strftime("%H:%M")
                })

analysis_df = pd.DataFrame(summary) if summary else pd.DataFrame(columns=["Stock","Price","Stay (Mins)","Vol Traded","Start","End"])

# ---------------- RANKED TABLE ----------------
if "Ranked Price Stays Table" in display_options:
    st.subheader("📋 Ranked Price Stays")
    st.dataframe(analysis_df.sort_values("Stay (Mins)", ascending=False), use_container_width=True, hide_index=True)

# ---------------- STOCK SELECTOR ----------------
stock_list = sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty else ["No Data"]
selected_stock = st.selectbox("🔍 Select Stock for Detailed View", stock_list)

if selected_stock != "No Data":
    df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock].copy()
    
    # Example Calculation Check in UI
    if not df_sub.empty:
        v_start = df_sub["VOLUME"].iloc[0]
        v_end = df_sub["VOLUME"].iloc[-1]
        sum_deltas = df_sub["VOL_DIFF_PDB"].sum()
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Start Volume", f"{v_start:,}")
        col2.metric("End Volume", f"{v_end:,}")
        col3.metric("Sum of Deltas (PDB)", f"{sum_deltas:,}", delta=int(sum_deltas - (v_end - v_start)))
        
        if int(sum_deltas) != int(v_end - v_start):
            st.warning(f"Note: Reconciliation discrepancy of {int(sum_deltas - (v_end - v_start))} units due to data gaps.")

    # ---------------- PDB ALL Price Profile ----------------
    if "PDB ALL Price" in display_options and not df_sub.empty:
        st.subheader(f"📊 PDB Profile — {selected_stock}")
        full_profile = df_sub.groupby("LTP*").agg(
            Vol_Traded=("VOL_DIFF_PDB", "sum"),
            Stay_Count=("captured_at", "count")
        ).reset_index().sort_values("LTP*")
        
        fig_full = go.Figure()
        fig_full.add_trace(go.Bar(y=full_profile["LTP*"], x=full_profile["Stay_Count"], orientation="h", name="Stay Count", marker_color="#EF553B"))
        fig_full.add_trace(go.Bar(y=full_profile["LTP*"], x=full_profile["Vol_Traded"], orientation="h", name="Volume", marker_color="#00CC96", base=full_profile["Stay_Count"]))
        fig_full.update_layout(template="plotly_dark", barmode="stack", height=500)
        st.plotly_chart(fig_full, use_container_width=True)

    # ---------------- PRICE / VOLUME HISTORY ----------------
    if "Price / Volume History" in display_options and not df_sub.empty:
        st.subheader(f"⏱️ Price / Volume History — {selected_stock}")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(x=df_sub["captured_at"], y=df_sub["LTP*"], name="Price", line=dict(color="#00CC96")))
        fig_hist.add_trace(go.Bar(x=df_sub["captured_at"], y=df_sub["VOL_DIFF_PDB"], name="Volume Delta", yaxis="y2", marker_color="#636EFA", opacity=0.6))
        fig_hist.update_layout(template="plotly_dark", yaxis2=dict(overlaying="y", side="right"), height=400)
        st.plotly_chart(fig_hist, use_container_width=True)

st.caption(f"Range: {display_start} to {display_end} | Dhaka Local Time")
