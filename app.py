import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, time
import pytz
from streamlit_autorefresh import st_autorefresh

# --- 1. CONFIG & AUTH ---
def check_password():
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False
    if st.session_state["password_correct"]: return True

    with st.form("login"):
        st.subheader("🔐 Access Control")
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            if u == st.secrets["LOGIN_USER"] and p == st.secrets["LOGIN_PASS"]:
                st.session_state["password_correct"] = True
                st.rerun()
            else: st.error("Invalid credentials")
    return False

if not check_password(): st.stop()

# --- 2. DB CONNECTION ---
try:
    client = MongoClient(st.secrets["MONGO_URI"])
    db = client['DSE_Market_Data']
    collection = db['price_logs']
except:
    st.error("DB Connection Failed")
    st.stop()

st.set_page_config(page_title="DSE Alpha Tracker", layout="wide")
st_autorefresh(interval=60000, key="refresh")

# --- 3. SIDEBAR: FILTERS ---
st.sidebar.header("⏳ Filter Data")
sel_date = st.sidebar.date_input("Select Date", datetime.now())

# Time Range Slider
t_start, t_end = st.sidebar.slider(
    "Time Range", 
    value=(time(10, 0), time(14, 30)),
    format="HH:mm"
)

# Combine Date and Time into UTC for MongoDB query
dhaka_tz = pytz.timezone('Asia/Dhaka')
dt_start = dhaka_tz.localize(datetime.combine(sel_date, t_start)).astimezone(pytz.UTC)
dt_end = dhaka_tz.localize(datetime.combine(sel_date, t_end)).astimezone(pytz.UTC)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# --- 4. DATA ENGINE ---
@st.cache_data(ttl=60)
def get_filtered_data(start, end):
    # Query Mongo between timestamps
    query = {"captured_at": {"$gte": start, "$lte": end}}
    cursor = collection.find(query).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))
    
    if not df.empty:
        # 1. Ensure it is datetime
        df['captured_at'] = pd.to_datetime(df['captured_at'])
        
        # 2. FIX: Localize to UTC first, then convert to Dhaka
        # This handles both naive and aware timestamps safely
        if df['captured_at'].dt.tz is None:
            df['captured_at'] = df['captured_at'].dt.tz_localize('UTC')
            
        df['captured_at'] = df['captured_at'].dt.tz_convert('Asia/Dhaka')
        
    return df

raw_df = get_filtered_data(dt_start, dt_end)

# --- 5. MAIN LOGIC: VOLUME CHANGES WITH CONSTANT LTP ---
st.title("🚀 DSE volume/Price Divergence")

if not raw_df.empty:
    # Group by stock to check for changes
    summary = []
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2: continue
        
        # Check if LTP is constant but Volume has changed
        ltp_constant = group['LTP*'].nunique() == 1
        vol_changed = group['VOLUME'].iloc[-1] != group['VOLUME'].iloc[0]
        
        if ltp_constant and vol_changed:
            summary.append({
                "Stock": stock,
                "Price": group['LTP*'].iloc[0],
                "Starting Vol": int(group['VOLUME'].iloc[0]),
                "Current Vol": int(group['VOLUME'].iloc[-1]),
                "Vol Shift": int(group['VOLUME'].iloc[-1] - group['VOLUME'].iloc[0])
            })
    
    if summary:
        st.subheader("⚠️ Stocks with Volume Shift & Constant LTP")
        sum_df = pd.DataFrame(summary).sort_values("Vol Shift", ascending=False)
        
        # Selection
        selected_stock = st.selectbox("Select a stock to see the Detail Graph", sum_df['Stock'])
    else:
        st.info("No stocks found where volume changed with constant price in this range.")
        selected_stock = st.selectbox("Or Search any Stock", raw_df['TRADING CODE'].unique())
        
    # --- 6. DETAIL GRAPH (DUAL AXIS) ---
    if selected_stock:
        df_sub = raw_df[raw_df['TRADING CODE'] == selected_stock]
        
        fig = go.Figure()
        # Add LTP Line
        fig.add_trace(go.Scatter(
            x=df_sub['captured_at'], y=df_sub['LTP*'],
            name="LTP (Price)", mode='lines+markers',
            line=dict(color='royalblue', width=3)
        ))
        
        # Add Volume Bar (Secondary Axis)
        fig.add_trace(go.Bar(
            x=df_sub['captured_at'], y=df_sub['VOLUME'],
            name="Volume", opacity=0.3,
            yaxis="y2", marker_color='gray'
        ))

        fig.update_layout(
            title=f"Detailed Movement for {selected_stock}",
            xaxis_title="Time (Dhaka)",
            yaxis_title="Price (BDT)",
            yaxis2=dict(title="Volume", overlaying="y", side="right"),
            template="plotly_dark",
            hovermode="x unified"
        )
        st.plotly_chart(fig, use_container_width=True)
        
        # Stat cards
        c1, c2, c3 = st.columns(3)
        c1.metric("Current Price", f"{df_sub['LTP*'].iloc[-1]} BDT")
        c2.metric("Total Volume", f"{int(df_sub['VOLUME'].iloc[-1]):,}")
        c3.metric("Records Found", len(df_sub))

else:
    st.warning("No data found for the selected range. Ensure your collector is running.")

st.divider()
st.caption(f"Range: {dt_start.strftime('%H:%M')} to {dt_end.strftime('%H:%M')} UTC")
