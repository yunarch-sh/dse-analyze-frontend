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
# ---------------- CUSTOM CSS FOR HEADER ----------------
st.markdown("""
<style>
.main-header {
    background: linear-gradient(135deg, #1e1e2f 0%, #3b3b58 100%);
    padding: 25px;
    border-radius: 12px;
    border-left: 6px solid #00CC96;
    margin-bottom: 30px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.35);
}
.title-text {
    font-family: 'Inter', sans-serif;
    font-weight: 900;
    font-size: 36px;
    color: white;
    margin: 0;
    letter-spacing: -1px;
}
.title-text span {
    color: #636EFA;
    background: linear-gradient(90deg, #636EFA 0%, #00CC96 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}
.subtitle-text {
    font-size: 14px;
    color: #00CC96;
    text-transform: uppercase;
    letter-spacing: 2px;
    font-weight: 500;
}
.badge {
    background-color: #3b3b58;
    padding: 5px 14px;
    border-radius: 20px;
    font-size: 13px;
    color: #00CC96;
    border: 1px solid #00CC96;
    font-weight: 600;
}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="main-header">
    <div style="display:flex; justify-content: space-between; align-items:center;">
        <div>
            <p class="subtitle-text">📈 Smart Market Intelligence</p>
            <h1 class="title-text">POC • PDB <span>ALPHA</span></h1>
        </div>
        <div style="text-align:right;">
            <span class="badge">LIVE TRACKER</span>
            <p style="color: #888; font-size: 12px; margin-top: 6px;">
                Session: {now_dhaka.strftime('%d %b %Y')}<br>
                Dhaka: {now_dhaka.strftime('%H:%M:%S')}
            </p>
        </div>
    </div>
</div>
""", unsafe_allow_html=True)

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
                    "Stock": stock,
                    "Price": price,
                    "Stay (Mins)": round(duration, 1),
                    "Vol Traded": vol_diff,
                    "Start": start_t.strftime("%H:%M"),
                    "End": end_t.strftime("%H:%M"),
                })

analysis_df = pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False) if summary else pd.DataFrame(
    columns=["Stock", "Price", "Stay (Mins)", "Vol Traded", "Start", "End"]
)

# ---------------- RANKED TABLE ----------------
st.subheader("📋 Ranked Price Stays")
st.dataframe(analysis_df, use_container_width=True, hide_index=True)
st.divider()

# ---------------- DETAILED VIEW ----------------
stock_list = (
    sorted(analysis_df["Stock"].unique())
    if not analysis_df.empty
    else sorted(raw_df["TRADING CODE"].unique())
    if not raw_df.empty
    else ["No Data"]
)

# ---------------- STOCK SELECTION (STATEFUL) ----------------
if "selected_stock" not in st.session_state:
    st.session_state["selected_stock"] = stock_list[0]

selected_stock = st.selectbox(
    "🔍 Select Stock for Detailed View",
    stock_list,
    index=stock_list.index(st.session_state["selected_stock"])
    if st.session_state["selected_stock"] in stock_list else 0
)

st.session_state["selected_stock"] = selected_stock

# ---------------- Calculate total volume for the selected stock ----------------
total_volume = 0
if not raw_df.empty:
    df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock]
    if not df_sub.empty:
        total_volume = int(df_sub["VOLUME"].max() - df_sub["VOLUME"].min())

# ---------------- PRICE / VOLUME PROFILE ----------------
if selected_stock != "No Data":
    stock_summary = analysis_df[analysis_df["Stock"] == selected_stock]
    if not stock_summary.empty:
        profile_data = (
            stock_summary.groupby("Price")
            .agg({"Vol Traded": "sum", "Stay (Mins)": "sum"})
            .reset_index()
            .sort_values("Price")
        )
    else:
        profile_data = pd.DataFrame(columns=["Price", "Vol Traded", "Stay (Mins)"])

    st.subheader(f"📊 Market Profile — {selected_stock}")

    if total_volume > 0:
        profile_data["Vol % of Total"] = (profile_data["Vol Traded"] / total_volume) * 100
    else:
        profile_data["Vol % of Total"] = 0


    # ---- SINGLE STACKED HORIZONTAL BARS ----
    fig_p = go.Figure()
    fig_p.add_trace(go.Bar(
        y=profile_data["Price"],
        x=profile_data["Stay (Mins)"],
        orientation="h",
        name="Time Stay",
        marker_color="#EF553B"
    ))
    fig_p.add_trace(go.Bar(
        y=profile_data["Price"],
        x=profile_data["Vol Traded"],
        orientation="h",
        name="Volume",
        marker_color="#636EFA",
        base=profile_data["Stay (Mins)"],  # stack on top of Time Stay
        hovertemplate=(
            "Price: %{y}<br>"
            "Volume: %{x}<br>"
            "Percent of total: %{customdata:.2f}%"
        ),
        customdata=profile_data["Vol % of Total"]
    ))

    fig_p.update_layout(
        barmode="stack",
        template="plotly_dark",
        xaxis_title="Minutes / Volume",
        yaxis_title="Price (BDT)",
        height=400 + len(profile_data) * 10,
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=10, r=10, t=80, b=20)
    )

    st.plotly_chart(fig_p, use_container_width=True)

    # ---------------- PRICE HISTORY ----------------
    df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock] if not raw_df.empty else pd.DataFrame()
    st.subheader(f"⏱️ Price / Volume History — {selected_stock}")
    fig_hist = go.Figure()
    if not df_sub.empty:
        fig_hist.add_trace(go.Scatter(
            x=df_sub["captured_at"],
            y=df_sub["LTP*"],
            name="Price",
            line=dict(color="#00CC96")
        ))
        fig_hist.add_trace(go.Bar(
            x=df_sub['captured_at'],
            y=df_sub['VOLUME'],
            name="Volume",
            yaxis="y2",
            opacity=0.3,
            marker_color="#636EFA"
        ))
    fig_hist.update_layout(
        template="plotly_dark",
        height=400,
        yaxis=dict(title="Price"),
        yaxis2=dict(overlaying="y", side="right", title="Volume"),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=10, r=10, t=20, b=20)
    )
    st.plotly_chart(fig_hist, use_container_width=True)

st.divider()
st.caption(f"Range: {display_start} to {display_end} | Dhaka Local Time")
