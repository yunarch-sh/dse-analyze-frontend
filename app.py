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
    default=[
        "PDB ALL Price",
        "Excel Approach Profile",
        "Price / Volume History"
    ]
)

# ✅ FIXED TIME FILTER (clean, no double conversion)
dt_start_local = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz)
dt_end_local   = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data_with_vol(selected_date):
    try:
        start_of_day = datetime.combine(selected_date, time(0,0), tzinfo=dhaka_tz).astimezone(timezone.utc)
        end_of_day = datetime.combine(selected_date, time(23,59,59), tzinfo=dhaka_tz).astimezone(timezone.utc)

        query = {"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}
        cursor = collection.find(query).sort("captured_at", 1)

        df = pd.DataFrame(list(cursor))

        if df.empty:
            return df

        df["captured_at"] = pd.to_datetime(df["captured_at"], errors='coerce', utc=True)
        df["captured_at"] = df["captured_at"].dt.tz_convert(dhaka_tz)

        df = df.sort_values(["TRADING CODE", "captured_at"])

        return df

    except Exception as e:
        st.error(f"Data Fetch Error: {e}")
        return pd.DataFrame()

# ---------------- REFRESH ----------------
if st.sidebar.button("🔄 Refresh Data"):
    get_daily_data_with_vol.clear()

# ---------------- LOAD DATA ----------------
full_day_df = get_daily_data_with_vol(sel_date)

if not full_day_df.empty:

    # --- PDB Logic ---
    full_day_df["VOL_DIFF_PDB"] = full_day_df.groupby("TRADING CODE")["VOLUME"].diff().fillna(0)

    # --- Excel Logic ---
    full_day_df["VOL_DIFF_EXCEL"] = (
        full_day_df.groupby("TRADING CODE")["VOLUME"].shift(-1) - full_day_df["VOLUME"]
    ).fillna(0)

    # ✅ FIX: remove negative volume
    full_day_df["VOL_DIFF_PDB"] = full_day_df["VOL_DIFF_PDB"].clip(lower=0)
    full_day_df["VOL_DIFF_EXCEL"] = full_day_df["VOL_DIFF_EXCEL"].clip(lower=0)

    # Apply Time Filter
    mask = (full_day_df["captured_at"] >= dt_start_local) & \
           (full_day_df["captured_at"] <= dt_end_local)

    raw_df = full_day_df.loc[mask].copy()

else:
    raw_df = pd.DataFrame()

# ---------------- PRICE STAY ANALYSIS ----------------
summary = []

if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):

        group = group.dropna(subset=["LTP*"])

        if len(group) < 2:
            continue

        group = group.copy()

        group["price_changed"] = group["LTP*"].ne(group["LTP*"].shift())
        group["stay_id"] = group["price_changed"].cumsum()

        for stay_id, stay_group in group.groupby("stay_id"):

            if len(stay_group) < 2:
                continue

            price = float(stay_group["LTP*"].iloc[0])
            start_t = stay_group["captured_at"].iloc[0]
            end_t = stay_group["captured_at"].iloc[-1]

            # ✅ FIX: better duration approximation
            duration = (end_t - start_t).total_seconds() / 60 + 1

            vol_diff = int(stay_group["VOL_DIFF_PDB"].sum())

            if vol_diff > 0:
                summary.append({
                    "Stock": stock,
                    "Price": price,
                    "Stay (Mins)": round(duration,1),
                    "Vol Traded": vol_diff,
                    "Start": start_t.strftime("%H:%M"),
                    "End": end_t.strftime("%H:%M")
                })

analysis_df = pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False) if summary else pd.DataFrame(
    columns=["Stock","Price","Stay (Mins)","Vol Traded","Start","End"]
)

# ---------------- RANKED TABLE ----------------
if "Ranked Price Stays Table" in display_options:
    st.subheader("📋 Ranked Price Stays")
    st.dataframe(analysis_df, use_container_width=True, hide_index=True)
    st.divider()

# ---------------- STOCK SELECTION ----------------
stock_list = (
    sorted(analysis_df["Stock"].unique()) if not analysis_df.empty
    else sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty
    else ["No Data"]
)

if "selected_stock" not in st.session_state:
    st.session_state["selected_stock"] = stock_list[0]

selected_stock = st.selectbox(
    "🔍 Select Stock for Detailed View",
    stock_list,
    index=stock_list.index(st.session_state["selected_stock"]) if st.session_state["selected_stock"] in stock_list else 0
)

st.session_state["selected_stock"] = selected_stock

if not raw_df.empty and selected_stock != "No Data":
    df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock].copy()
    df_sub = df_sub[df_sub["LTP*"] > 0].copy()
else:
    df_sub = pd.DataFrame()

# ---------------- REMAINING CHARTS ----------------
# (UNCHANGED — your logic is already correct)

# PDB STAY PRICE Profile
if "PDB STAY PRICE Profile" in display_options:
    if selected_stock != "No Data" and not df_sub.empty:

        stock_summary = analysis_df[analysis_df["Stock"]==selected_stock]

        profile_data = stock_summary.groupby("Price").agg({
            "Vol Traded":"sum",
            "Stay (Mins)":"sum"
        }).reset_index().sort_values("Price") if not stock_summary.empty else pd.DataFrame(columns=["Price","Vol Traded","Stay (Mins)"])

        st.subheader(f"📊 PDB STAY PRICE Profile — {selected_stock}")

        pdb_total = df_sub["VOL_DIFF_PDB"].sum()
        profile_data["Vol % of Total"] = (profile_data["Vol Traded"]/pdb_total*100) if pdb_total>0 else 0

        fig_p = go.Figure()

        fig_p.add_trace(go.Bar(
            y=profile_data["Price"], x=profile_data["Stay (Mins)"],
            orientation="h", name="Time Stay"
        ))

        fig_p.add_trace(go.Bar(
            y=profile_data["Price"], x=profile_data["Vol Traded"],
            orientation="h", name="Volume",
            base=profile_data["Stay (Mins)"],
            customdata=profile_data["Vol % of Total"]
        ))

        fig_p.update_layout(barmode="stack", template="plotly_dark")
        st.plotly_chart(fig_p, use_container_width=True)
