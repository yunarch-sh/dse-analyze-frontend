import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.graph_objects as go
from datetime import datetime, time
import pytz

# ---------------- GLOBAL SETTINGS ----------------
dhaka_tz = pytz.timezone("Asia/Dhaka")
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
dt_start = dhaka_tz.localize(datetime.combine(sel_date, t_start)).astimezone(pytz.UTC)
dt_end = dhaka_tz.localize(datetime.combine(sel_date, t_end)).astimezone(pytz.UTC)
display_start = dt_start.astimezone(dhaka_tz).strftime("%H:%M")
display_end = dt_end.astimezone(dhaka_tz).strftime("%H:%M")

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.experimental_rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_filtered_data(start, end):
    try:
        query = {"captured_at": {"$gte": start, "$lte": end}}
        cursor = collection.find(query).sort("captured_at", 1)
        df = pd.DataFrame(list(cursor))
        if df.empty:
            return df
        df["captured_at"] = pd.to_datetime(df["captured_at"], errors='coerce')
        df["captured_at"] = df["captured_at"].apply(
            lambda x: x.tz_convert("UTC") if pd.notnull(x) and x.tzinfo 
            else (x.tz_localize("UTC") if pd.notnull(x) else x)
        )
        df["captured_at"] = df["captured_at"].dt.tz_convert(dhaka_tz)
        return df
    except Exception as e:
        st.error(f"Data Fetch Error: {e}")
        return pd.DataFrame()

# ---------------- MANUAL REFRESH ----------------
if "refresh_click" not in st.session_state:
    st.session_state["refresh_click"] = 0

if st.sidebar.button("🔄 Refresh Data"):
    st.session_state["refresh_click"] += 1
    st.cache_data.clear()

raw_df = get_filtered_data(dt_start, dt_end)

# ---------------- CALCULATE VOL_DIFF ONCE ----------------
if not raw_df.empty:
    raw_df = raw_df.sort_values(["TRADING CODE", "captured_at"])
    # Compute volume differences per stock
    raw_df["VOL_DIFF"] = raw_df.groupby("TRADING CODE")["VOLUME"].diff()
    # Fill the first value per group with the first VOLUME
    raw_df["VOL_DIFF"] = raw_df.groupby("TRADING CODE")["VOL_DIFF"].transform(lambda x: x.fillna(x.iloc[0]))
    # Ensure no negative volume
    raw_df["VOL_DIFF"] = raw_df["VOL_DIFF"].clip(lower=0)

# ---------------- PRICE STAY ANALYSIS ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2:
            continue
        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"] = group["price_changed"].cumsum()
        for stay_id, stay_group in group.groupby("stay_id"):
            if len(stay_group) < 2:
                continue
            price = float(stay_group["LTP*"].iloc[0])
            start_t = stay_group["captured_at"].iloc[0]
            end_t = stay_group["captured_at"].iloc[-1]
            duration = (end_t - start_t).total_seconds() / 60
            vol_diff = int(stay_group["VOL_DIFF"].sum())
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
st.subheader("📋 Ranked Price Stays")
st.dataframe(analysis_df, width='stretch', hide_index=True)
st.divider()

# ---------------- DETAILED VIEW ----------------
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
    total_volume = int(df_sub["VOL_DIFF"].sum()) if not df_sub.empty else 0
else:
    df_sub = pd.DataFrame()
    total_volume = 0

# ---------------- PRICE / VOLUME PROFILE ----------------
if selected_stock != "No Data" and not df_sub.empty:
    stock_summary = analysis_df[analysis_df["Stock"]==selected_stock]
    profile_data = stock_summary.groupby("Price").agg({
        "Vol Traded":"sum",
        "Stay (Mins)":"sum"
    }).reset_index().sort_values("Price") if not stock_summary.empty else pd.DataFrame(columns=["Price","Vol Traded","Stay (Mins)"])

    st.subheader(f"📊 PDB STAY PRICE Profile — {selected_stock}")
    profile_data["Vol % of Total"] = (profile_data["Vol Traded"]/total_volume*100) if total_volume>0 else 0

    fig_p = go.Figure()
    fig_p.add_trace(go.Bar(
        y=profile_data["Price"], x=profile_data["Stay (Mins)"], orientation="h",
        name="Time Stay", marker_color="#EF553B"
    ))
    fig_p.add_trace(go.Bar(
        y=profile_data["Price"], x=profile_data["Vol Traded"], orientation="h",
        name="Volume", marker_color="#636EFA", base=profile_data["Stay (Mins)"],
        hovertemplate="Price: %{y}<br>Volume: %{x}<br>Percent of total: %{customdata:.2f}%",
        customdata=profile_data["Vol % of Total"]
    ))
    fig_p.update_layout(
        barmode="stack", template="plotly_dark",
        xaxis_title="Minutes / Volume", yaxis_title="Price (BDT)",
        height=400 + len(profile_data)*10,
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=10, r=10, t=80, b=20)
    )
    st.plotly_chart(fig_p, width='stretch')

# ---------------- FULL MARKET PROFILE ----------------
if selected_stock != "No Data" and not df_sub.empty:
    st.subheader(f"📊 PDB ALL Price — {selected_stock}")
    full_profile = df_sub.groupby("LTP*").agg(
        Vol_Traded=("VOL_DIFF", "sum"),
        Stay_Count=("captured_at", "count")
    ).reset_index().sort_values("LTP*")
    total_volume_full = full_profile["Vol_Traded"].sum()
    full_profile["Vol % of Total"] = (full_profile["Vol_Traded"] / total_volume_full * 100) if total_volume_full>0 else 0

    fig_full = go.Figure()
    fig_full.add_trace(go.Bar(
        y=full_profile["LTP*"], x=full_profile["Stay_Count"], orientation="h",
        name="Time Stay", marker_color="#EF553B"
    ))
    fig_full.add_trace(go.Bar(
        y=full_profile["LTP*"], x=full_profile["Vol_Traded"], orientation="h",
        name="Volume", marker_color="#636EFA", base=full_profile["Stay_Count"],
        hovertemplate="Price: %{y}<br>Volume: %{x}<br>Percent of total: %{customdata:.2f}%",
        customdata=full_profile["Vol % of Total"]
    ))
    fig_full.update_layout(
        barmode="stack", template="plotly_dark",
        xaxis_title="Minutes / Volume", yaxis_title="Price (BDT)",
        height=400 + len(full_profile)*10,
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=10, r=10, t=80, b=20)
    )
    st.plotly_chart(fig_full, width='stretch')

# ---------------- PRICE / VOLUME HISTORY ----------------
if not df_sub.empty:
    st.subheader(f"⏱️ Price / Volume History — {selected_stock}")
    fig_hist = go.Figure()
    fig_hist.add_trace(go.Scatter(
        x=df_sub["captured_at"], y=df_sub["LTP*"], name="Price", line=dict(color="#00CC96")
    ))
    fig_hist.add_trace(go.Bar(
        x=df_sub["captured_at"], y=df_sub["VOL_DIFF"], name="Volume Delta", yaxis="y2",
        opacity=0.6, marker_color="#636EFA"
    ))
    fig_hist.update_layout(
        template="plotly_dark", height=400,
        yaxis=dict(title="Price"),
        yaxis2=dict(overlaying="y", side="right", title="Volume"),
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
        margin=dict(l=10, r=10, t=20, b=20)
    )
    st.plotly_chart(fig_hist, width='stretch')

st.caption(f"Range: {display_start} to {display_end} | Dhaka Local Time")
