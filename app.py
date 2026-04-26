import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.graph_objects as go
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# ---------------- SETTINGS ----------------
dhaka_tz = ZoneInfo("Asia/Dhaka")
st.set_page_config(page_title="DSE Alpha Tracker", layout="wide")

# ---------------- HEADER ----------------
def render_header():
    now = datetime.now(dhaka_tz)
    st.markdown(f"""
    <div style="display:flex;justify-content:space-between;align-items:center;font-family:sans-serif;">
        <div>
            <h1 style="margin:0;color:#4A90E2;">DSE ALPHA TRACKER</h1>
            <p style="margin:0;color:#E74C3C;">Volume Profile System</p>
        </div>
        <div style="text-align:right;color:#27AE60;">
            {now.strftime('%d %b %Y | %H:%M:%S')}
        </div>
    </div>
    <hr>
    """, unsafe_allow_html=True)

render_header()

# ---------------- AUTH ----------------
def check_password():
    if "auth" not in st.session_state:
        st.session_state["auth"] = False

    if st.session_state["auth"]:
        return True

    with st.form("login"):
        st.subheader("🔐 Login")
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        ok = st.form_submit_button("Login")

        if ok:
            if u == st.secrets["LOGIN"]["LOGIN_USER"] and p == st.secrets["LOGIN"]["LOGIN_PASS"]:
                st.session_state["auth"] = True
                st.rerun()
            else:
                st.error("Invalid credentials")

    return False

if not check_password():
    st.stop()

# ---------------- DB ----------------
@st.cache_resource
def db_conn():
    client = MongoClient(st.secrets["MONGO"]["MONGO_URI"])
    return client["DSE_Market_Data"]["price_logs"]

collection = db_conn()

# ---------------- SIDEBAR ----------------
st.sidebar.header("Filters")

sel_date = st.sidebar.date_input("Date", datetime.now(dhaka_tz))
t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10, 0), time(14, 30)),
    format="HH:mm"
)

# ---------------- LOAD DATA ----------------
@st.cache_data(ttl=60)
def load_data(date):
    start = datetime.combine(date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end = datetime.combine(date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)

    df = pd.DataFrame(list(collection.find({
        "captured_at": {"$gte": start, "$lte": end}
    }).sort("captured_at", 1)))

    if df.empty:
        return df

    df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
    df = df.sort_values(["TRADING CODE", "captured_at"])
    return df

full_df = load_data(sel_date)

# ---------------- VOLUME FIX ----------------
if not full_df.empty:

    full_df["IND_VOL"] = (
        full_df.groupby("TRADING CODE")["VOLUME"].diff()
    )

    full_df["IND_VOL"] = full_df["IND_VOL"].fillna(full_df["VOLUME"])
    full_df["IND_VOL"] = full_df["IND_VOL"].clip(lower=0)

    start_t = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz)
    end_t = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz)

    df = full_df[
        (full_df["captured_at"] >= start_t) &
        (full_df["captured_at"] <= end_t)
    ].copy()

else:
    df = pd.DataFrame()

# ---------------- STOCK ----------------
stocks = sorted(df["TRADING CODE"].unique()) if not df.empty else ["No Data"]
stock = st.selectbox("Select Stock", stocks)

df_sub = df[df["TRADING CODE"] == stock].copy() if stock != "No Data" else pd.DataFrame()

# ---------------- VOLUME PROFILE ----------------
if not df_sub.empty:

    st.subheader(f"📊 Volume Profile — {stock}")

    vp = df_sub.groupby("LTP*").agg(
        Volume=("IND_VOL", "sum")
    ).reset_index().sort_values("LTP*")

    total_vol = vp["Volume"].sum()

    # POC
    poc = vp.loc[vp["Volume"].idxmax(), "LTP*"]

    # VWAP
    vwap = (df_sub["LTP*"] * df_sub["IND_VOL"]).sum() / df_sub["IND_VOL"].sum()

    fig = go.Figure()

    fig.add_trace(go.Bar(
        y=vp["LTP*"],
        x=vp["Volume"],
        orientation="h",
        name="Volume"
    ))

    # ✅ FIXED HERE (no add_vline)
    fig.add_hline(
        y=poc,
        line_dash="dash",
        line_color="red",
        annotation_text="POC",
        annotation_position="right"
    )

    fig.add_hline(
        y=vwap,
        line_dash="dot",
        line_color="yellow",
        annotation_text="VWAP",
        annotation_position="right"
    )

    fig.update_layout(
        template="plotly_dark",
        xaxis_title="Volume",
        yaxis_title="Price",
        height=600
    )

    st.plotly_chart(fig, use_container_width=True)

    st.success(f"""
    POC: {poc}  
    VWAP: {round(vwap, 4)}  
    Total Volume: {total_vol:,.0f}
    """)

# ---------------- PRICE HISTORY ----------------
if not df_sub.empty:

    st.subheader("⏱ Price & Volume Flow")

    fig2 = go.Figure()

    fig2.add_trace(go.Scatter(
        x=df_sub["captured_at"],
        y=df_sub["LTP*"],
        name="Price"
    ))

    fig2.add_trace(go.Bar(
        x=df_sub["captured_at"],
        y=df_sub["IND_VOL"],
        name="Volume"
    ))

    fig2.update_layout(template="plotly_dark", height=400)

    st.plotly_chart(fig2, use_container_width=True)

# ---------------- FINAL CHECK ----------------
if not df_sub.empty:
    st.info(f"""
    ✔ Volume Reconciliation Check:
    Sum IND_VOL = {df_sub['IND_VOL'].sum():,.0f}
    (Should match session cumulative delta)
    """)
