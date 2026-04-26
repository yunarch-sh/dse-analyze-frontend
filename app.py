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

display_options = st.sidebar.multiselect(
    "Select Views",
    [
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

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)

# ---------------- DATA ----------------
@st.cache_data(ttl=60)
def get_data(date):
    start = datetime.combine(date, time(0,0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end = datetime.combine(date, time(23,59,59), tzinfo=dhaka_tz).astimezone(timezone.utc)

    cursor = collection.find({
        "captured_at": {"$gte": start, "$lte": end}
    }).sort("captured_at", 1)

    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
    return df.sort_values(["TRADING CODE", "captured_at"])

df = get_data(sel_date)

if not df.empty:
    df["VOL_DIFF_PDB"] = df.groupby("TRADING CODE")["VOLUME"].diff().fillna(0)
    df["VOL_DIFF_EXCEL"] = df.groupby("TRADING CODE")["VOLUME"].shift(-1) - df["VOLUME"]
    df["VOL_DIFF_EXCEL"] = df["VOL_DIFF_EXCEL"].fillna(0)
else:
    df = pd.DataFrame()

mask = (df["captured_at"] >= dt_start.astimezone(dhaka_tz)) & \
       (df["captured_at"] <= dt_end.astimezone(dhaka_tz))

raw_df = df.loc[mask].copy()

# ---------------- STOCK SELECT ----------------
stocks = raw_df["TRADING CODE"].unique() if not raw_df.empty else ["No Data"]

selected_stock = st.selectbox("Stock", stocks)

df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock].copy() if selected_stock != "No Data" else pd.DataFrame()

# ---------------- PRICE / VOLUME HISTORY ----------------
if "Price / Volume History" in display_options:
    if not df_sub.empty:
        st.subheader("Price / Volume History")

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=df_sub["captured_at"],
            y=df_sub["LTP*"],
            name="Price"
        ))

        fig.add_trace(go.Bar(
            x=df_sub["captured_at"],
            y=df_sub["VOL_DIFF_PDB"],
            name="Volume"
        ))

        st.plotly_chart(fig, use_container_width=True)

# ---------------- 🔥 BONUS GRAPH (ADDED ONLY HERE) ----------------
if "Price / Volume History" in display_options:
    if not df_sub.empty:

        st.subheader(f"📊 Price vs Volume + % + Cumulative — {selected_stock}")

        temp = df_sub.sort_values("captured_at").copy()

        temp["VOL"] = temp["VOLUME"].diff().fillna(0)
        temp.loc[temp["VOL"] < 0, "VOL"] = 0

        price_vol = temp.groupby("LTP*").agg(
            Volume=("VOL", "sum")
        ).reset_index().sort_values("LTP*")

        total_vol = price_vol["Volume"].sum()

        price_vol["Percent"] = (price_vol["Volume"] / total_vol * 100) if total_vol > 0 else 0
        price_vol["Cumulative"] = price_vol["Volume"].cumsum()

        fig2 = go.Figure()

        fig2.add_trace(go.Bar(
            x=price_vol["LTP*"],
            y=price_vol["Volume"],
            name="Volume per Price",
            customdata=price_vol["Percent"],
            hovertemplate="Price:%{x}<br>Vol:%{y}<br>%:%{customdata:.2f}"
        ))

        fig2.add_trace(go.Scatter(
            x=price_vol["LTP*"],
            y=price_vol["Cumulative"],
            mode="lines+markers",
            name="Cumulative Volume"
        ))

        fig2.add_hline(
            y=total_vol,
            line_dash="dash",
            line_color="red"
        )

        fig2.update_layout(
            template="plotly_dark",
            xaxis_title="Price",
            yaxis_title="Volume"
        )

        st.plotly_chart(fig2, use_container_width=True)

        st.caption(f"Total Volume: {int(total_vol)} (must match cumulative end)")
