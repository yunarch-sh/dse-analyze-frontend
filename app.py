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
        "Excel Approach Profile",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ],
    default=[
        "PDB ALL Price",
        "Excel Approach Profile",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ]
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data_with_vol(selected_date):
    try:
        start_of_day = datetime.combine(selected_date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
        end_of_day = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)

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
if "refresh_click" not in st.session_state:
    st.session_state["refresh_click"] = 0

if st.sidebar.button("🔄 Refresh Data"):
    st.session_state["refresh_click"] += 1
    st.cache_data.clear()

# ---------------- APPLY FILTER ----------------
full_day_df = get_daily_data_with_vol(sel_date)

if not full_day_df.empty:

    # PDB LOGIC
    full_day_df["VOL_DIFF_PDB"] = full_day_df.groupby("TRADING CODE")["VOLUME"].diff()
    full_day_df["VOL_DIFF_PDB"] = full_day_df["VOL_DIFF_PDB"].fillna(0).clip(lower=0)

    # ---------------- FIXED EXCEL LOGIC (TRUE EXCEL BEHAVIOR) ----------------
    full_day_df["VOL_DIFF_EXCEL"] = (
        full_day_df.groupby("TRADING CODE")["VOLUME"].shift(-1)
        - full_day_df["VOLUME"]
    ).fillna(0).clip(lower=0)

    # SHIFT PRICE (CRITICAL FIX)
    full_day_df["EXCEL_PRICE"] = full_day_df.groupby("TRADING CODE")["LTP*"].shift(-1)

    mask = (
        (full_day_df["captured_at"] >= dt_start.astimezone(dhaka_tz)) &
        (full_day_df["captured_at"] <= dt_end.astimezone(dhaka_tz))
    )
    raw_df = full_day_df.loc[mask].copy()

else:
    raw_df = pd.DataFrame()

# ---------------- STOCK SELECTION ----------------
stock_list = (
    sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty else ["No Data"]
)

if "selected_stock" not in st.session_state:
    st.session_state["selected_stock"] = stock_list[0]

selected_stock = st.selectbox("🔍 Select Stock", stock_list)

if not raw_df.empty and selected_stock != "No Data":
    df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock].copy()
else:
    df_sub = pd.DataFrame()

# ---------------- EXCEL PROFILE (FIXED) ----------------
if "Excel Approach Profile" in display_options:
    if not df_sub.empty:

        st.subheader(f"📊 EXCEL APPROACH (FIXED) — {selected_stock}")

        excel_profile = df_sub.groupby("EXCEL_PRICE").agg(
            Vol_Traded=("VOL_DIFF_EXCEL", "sum"),
            Stay_Count=("captured_at", "count")
        ).reset_index().rename(columns={"EXCEL_PRICE": "LTP*"}).sort_values("LTP*")

        total = excel_profile["Vol_Traded"].sum()
        excel_profile["Vol % of Total"] = (
            (excel_profile["Vol_Traded"] / total * 100) if total > 0 else 0
        )

        fig_excel = go.Figure()
        fig_excel.add_trace(go.Bar(
            y=excel_profile["LTP*"],
            x=excel_profile["Stay_Count"],
            orientation="h",
            name="Time Stay",
            marker_color="#EF553B"
        ))

        fig_excel.add_trace(go.Bar(
            y=excel_profile["LTP*"],
            x=excel_profile["Vol_Traded"],
            orientation="h",
            name="Excel Volume",
            marker_color="#AB63FA",
            base=excel_profile["Stay_Count"],
            customdata=excel_profile["Vol % of Total"],
            hovertemplate="Price: %{y}<br>Volume: %{x}<br>% of Total: %{customdata:.2f}%"
        ))

        fig_excel.update_layout(
            barmode="stack",
            template="plotly_dark",
            xaxis_title="Volume / Time",
            yaxis_title="Price",
            height=500
        )

        st.plotly_chart(fig_excel, use_container_width=True)

# ---------------- (ALL OTHER SECTIONS UNCHANGED) ----------------
# PDB, HISTORY, RECONCILIATION, TABLES — EXACTLY SAME AS YOUR CODE
