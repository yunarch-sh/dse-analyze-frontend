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
            <p style="margin:0; color:#E74C3C;">POC • PDB • EXCEL SYNC</p>
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
    value=(time(10, 0), time(14, 30)),
    format="HH:mm"
)

st.sidebar.header("👁️ Display Options")
display_options = st.sidebar.multiselect(
    "Select Views to Display",
    options=[
        "Ranked Price Stays Table",
        "Excel Profile (Time + Vol)",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ],
    default=["Ranked Price Stays Table", "Excel Profile (Time + Vol)", "Price / Volume History", "Price Volume Reconciliation"]
)

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data(selected_date):
    start_of_day = datetime.combine(selected_date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end_of_day = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)
    cursor = collection.find({"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))
    if not df.empty:
        df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
    return df

raw_full_day = get_daily_data(sel_date)

if raw_full_day.empty:
    st.warning("No data found for this date.")
    st.stop()

# ---------------- STOCK SELECTION ----------------
stock_list = sorted(raw_full_day["TRADING CODE"].unique())
selected_stock = st.selectbox("🔍 Select Stock", stock_list)

# ---------------- EXCEL LOGIC (CORE STEP-BY-STEP) ----------------

# 1. Copy data of certain share (Excel Step 1)
df_sub = raw_full_day[raw_full_day["TRADING CODE"] == selected_stock].copy().sort_values("captured_at")

# Apply Time Range Filter
dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz)
df_sub = df_sub[(df_sub["captured_at"] >= dt_start) & (df_sub["captured_at"] <= dt_end)]

if not df_sub.empty:
    # 2. Add dv column: C3-C2 (Excel Step 2)
    # We calculate ONLY within the filtered stock to prevent cross-stock volume leakage
    df_sub["dv"] = df_sub["VOLUME"].diff().fillna(0).clip(lower=0)

    # ---------------- PRICE STAY CALCULATION ----------------
    # This identifies "Stays" for the Ranked Table
    df_sub["price_changed"] = df_sub["LTP*"] != df_sub["LTP*"].shift()
    df_sub["stay_id"] = df_sub["price_changed"].cumsum()

    summary_list = []
    for stay_id, stay_group in df_sub.groupby("stay_id"):
        if len(stay_group) >= 1:
            start_t = stay_group["captured_at"].iloc[0]
            end_t = stay_group["captured_at"].iloc[-1]
            summary_list.append({
                "Stock": selected_stock,
                "Price": stay_group["LTP*"].iloc[0],
                "Stay (Mins)": round((end_t - start_t).total_seconds() / 60, 1),
                "Vol Traded": int(stay_group["dv"].sum()),
                "Start": start_t.strftime("%H:%M"),
                "End": end_t.strftime("%H:%M")
            })
    analysis_df = pd.DataFrame(summary_list)

    # ---------------- DISPLAY MODULES ----------------

    # 1. Ranked Table
    if "Ranked Price Stays Table" in display_options:
        st.subheader("📋 Ranked Price Stays")
        st.dataframe(analysis_df.sort_values("Stay (Mins)", ascending=False), use_container_width=True, hide_index=True)

    # 2. Excel Profile (The Bar Chart)
    if "Excel Profile (Time + Vol)" in display_options:
        st.subheader(f"📊 Volume Profile — {selected_stock}")
        # Excel Step 4 & 5: Sort by Price & Sum dv
        profile_data = df_sub.groupby("LTP*").agg(
            Vol_Traded=("dv", "sum"),
            Time_Ticks=("captured_at", "count")
        ).reset_index().sort_values("LTP*")

        fig_p = go.Figure()
        fig_p.add_trace(go.Bar(y=profile_data["LTP*"], x=profile_data["Time_Ticks"], orientation="h", name="Time (Ticks)", marker_color="#EF553B"))
        fig_p.add_trace(go.Bar(y=profile_data["LTP*"], x=profile_data["Vol_Traded"], orientation="h", name="Volume (dv)", marker_color="#636EFA", base=profile_data["Time_Ticks"]))
        fig_p.update_layout(barmode="stack", template="plotly_dark", height=500, xaxis_title="Ticks / Volume", yaxis_title="Price")
        st.plotly_chart(fig_p, use_container_width=True)

    # 3. History
    if "Price / Volume History" in display_options:
        st.subheader(f"⏱️ Price / Volume History — {selected_stock}")
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(x=df_sub["captured_at"], y=df_sub["LTP*"], name="Price", line=dict(color="#00CC96")))
        fig_hist.add_trace(go.Bar(x=df_sub["captured_at"], y=df_sub["dv"], name="Volume Delta", yaxis="y2", opacity=0.6, marker_color="#636EFA"))
        fig_hist.update_layout(template="plotly_dark", height=400, yaxis2=dict(overlaying="y", side="right"), legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"))
        st.plotly_chart(fig_hist, use_container_width=True)

    # 4. Reconciliation (Verification)
    if "Price Volume Reconciliation" in display_options:
        st.subheader("✅ Excel Reconciliation")
        sum_dv = df_sub["dv"].sum()
        cum_vol = df_sub["VOLUME"].iloc[-1] - df_sub["VOLUME"].iloc[0]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Sum of all dv", f"{sum_dv:,.0f}")
        c2.metric("Cumulative (Last-First)", f"{cum_vol:,.0f}")
        c3.metric("Match Status", "✅ 1:1 SYNC" if abs(sum_dv - cum_vol) < 1 else "⚠️ MISMATCH")
        
        if abs(sum_dv - cum_vol) > 1:
            st.warning(f"Note: Mismatch of {sum_dv - cum_vol} units usually occurs if the first trade of the time window is larger than the previous day's close.")

else:
    st.error("No data available for the selected parameters.")
