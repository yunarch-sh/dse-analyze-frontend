import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, time
import pytz
from streamlit_autorefresh import st_autorefresh

# --- 0. GLOBAL SETTINGS ---
dhaka_tz = pytz.timezone('Asia/Dhaka')

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
except Exception as e:
    st.error(f"DB Connection Failed: {e}")
    st.stop()

st.set_page_config(page_title="DSE Alpha Tracker", layout="wide")
st_autorefresh(interval=60000, key="refresh")

# --- 3. SIDEBAR: FILTERS ---
st.sidebar.header("⏳ Filter Data")
sel_date = st.sidebar.date_input("Select Date", datetime.now(dhaka_tz))

t_start, t_end = st.sidebar.slider(
    "Time Range", 
    value=(time(10, 0), time(14, 30)),
    format="HH:mm"
)

dt_start = dhaka_tz.localize(datetime.combine(sel_date, t_start)).astimezone(pytz.UTC)
dt_end = dhaka_tz.localize(datetime.combine(sel_date, t_end)).astimezone(pytz.UTC)

display_start = dt_start.astimezone(dhaka_tz).strftime('%H:%M')
display_end = dt_end.astimezone(dhaka_tz).strftime('%H:%M')

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# --- 4. DATA ENGINE ---
@st.cache_data(ttl=60)
def get_filtered_data(start, end):
    query = {"captured_at": {"$gte": start, "$lte": end}}
    cursor = collection.find(query).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))
    
    if not df.empty:
        df['captured_at'] = pd.to_datetime(df['captured_at'])
        if df['captured_at'].dt.tz is None:
            df['captured_at'] = df['captured_at'].dt.tz_localize('UTC')
        df['captured_at'] = df['captured_at'].dt.tz_convert(dhaka_tz)
    return df

raw_df = get_filtered_data(dt_start, dt_end)

# --- 5. MAIN LOGIC ---
st.title("⏳ DSE Alpha Tracker")

if not raw_df.empty:
    summary = []
    
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2: continue
        group = group.copy()
        group['price_changed'] = group['LTP*'] != group['LTP*'].shift()
        group['stay_id'] = group['price_changed'].cumsum()
        
        for stay_id, stay_group in group.groupby('stay_id'):
            if len(stay_group) < 2: continue
            price = float(stay_group['LTP*'].iloc[0])
            s_time = stay_group['captured_at'].iloc[0]
            e_time = stay_group['captured_at'].iloc[-1]
            duration = float((e_time - s_time).total_seconds() / 60)
            vol_diff = int(stay_group['VOLUME'].iloc[-1] - stay_group['VOLUME'].iloc[0])
            
            if vol_diff > 0:
                summary.append({
                    "Stock": stock, "Price": price, "Stay (Mins)": round(duration, 1),
                    "Vol Traded": vol_diff, "Start": s_time.strftime('%H:%M'), "End": e_time.strftime('%H:%M')
                })

    if summary:
        analysis_df = pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False)
        
        st.success(f"🏆 **Top Consolidation:** **{analysis_df.iloc[0]['Stock']}**")
        st.subheader("📋 Ranked Price Stays")
        st.dataframe(analysis_df, use_container_width=True, hide_index=True)
        
        # --- 6. INTENSITY CHART (Redesigned for Stability) ---
        st.subheader("🎯 Market Intensity: Price vs Volume & Stay")
        
        fig_rel = go.Figure()

        # Volume Points
        fig_rel.add_trace(go.Scatter(
            x=analysis_df["Vol Traded"], 
            y=analysis_df["Price"],
            mode='markers+text', 
            name='Volume Traded',
            text=analysis_df["Stock"], 
            textposition="top center",
            marker=dict(color='#636EFA', size=12),
            xaxis='x'
        ))

        # Stay Duration Points
        fig_rel.add_trace(go.Scatter(
            x=analysis_df["Stay (Mins)"], 
            y=analysis_df["Price"],
            mode='markers', 
            name='Minutes Stayed',
            marker=dict(color='#EF553B', size=10, symbol='diamond'),
            xaxis='x2'
        ))

        # Explicit layout configuration to avoid Python 3.14 dict issues
        fig_rel.update_layout(
            template="plotly_dark",
            height=500,
            margin=dict(t=80, b=50, l=50, r=50),
            legend=dict(orientation="h", y=1.15),
            yaxis=dict(title="Price (BDT)"),
            xaxis=dict(title="Volume Traded (Blue)", color="#636EFA"),
            xaxis2=dict(
                title="Minutes Stayed (Red)", 
                color="#EF553B",
                overlaying='x', 
                side='top'
            )
        )
        
        st.plotly_chart(fig_rel, use_container_width=True)

        # --- 7. INDIVIDUAL DETAIL ---
        st.divider()
        selected_stock = st.selectbox("Select stock for detail:", analysis_df['Stock'].unique())
        if selected_stock:
            df_sub = raw_df[raw_df['TRADING CODE'] == selected_stock]
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df_sub['captured_at'], y=df_sub['LTP*'], name="Price", line=dict(color='#00CC96')))
            fig.add_trace(go.Bar(x=df_sub['captured_at'], y=df_sub['VOLUME'], name="Volume", yaxis="y2", opacity=0.3))
            fig.update_layout(template="plotly_dark", yaxis2=dict(overlaying="y", side="right"))
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No stays detected.")
else:
    st.warning("Waiting for data...")

st.divider()
st.caption(f"Dhaka Time: {display_start} to {display_end}")
