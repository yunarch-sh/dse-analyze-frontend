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
        
        # --- 🏆 TOP SUMMARY ---
        top_stay = analysis_df.iloc[0]
        st.success(f"🏆 **Top Consolidation:** **{top_stay['Stock']}** at **{top_stay['Price']} BDT**")

        # --- 📋 RANKED TABLE ---
        st.subheader("📋 Ranked Price Stays")
        st.dataframe(analysis_df, use_container_width=True, hide_index=True)
        
        st.divider()

        # --- 📈 STOCK SELECTION ---
        selected_stock = st.selectbox("🔍 Select Stock for Detailed Analysis:", analysis_df['Stock'].unique())

        if selected_stock:
            col1, col2 = st.columns([1, 1])

            with col1:
                # --- 6. HORIZONTAL VOLUME PROFILE (EXCEL STYLE) ---
                st.subheader(f"📊 Volume Profile: {selected_stock}")
                
                stock_summary = analysis_df[analysis_df['Stock'] == selected_stock].copy()
                # Aggregate Volume and Minutes by Price Node
                profile_data = stock_summary.groupby("Price").agg({
                    "Vol Traded": "sum",
                    "Stay (Mins)": "sum"
                }).reset_index().sort_values("Price", ascending=True)

                fig_profile = go.Figure()
                fig_profile.add_trace(go.Bar(
                    y=profile_data["Price"],
                    x=profile_data["Vol Traded"],
                    orientation='h',
                    name="Volume Activity",
                    marker=dict(color='#636EFA', line=dict(color='white', width=0.5)),
                    customdata=profile_data["Stay (Mins)"],
                    hovertemplate="<b>Price: %{y} BDT</b><br>Volume: %{x:,.0f}<br>Time: %{customdata}m<extra></extra>"
                ))

                fig_profile.update_layout(
                    template="plotly_dark",
                    height=500,
                    xaxis=dict(title="Total Volume at Price"),
                    yaxis=dict(title="Price Level (BDT)", type='category', tickmode='linear'),
                    margin=dict(l=10, r=10, t=20, b=20)
                )
                st.plotly_chart(fig_profile, use_container_width=True)

            with col2:
                # --- 7. TIME-SERIES CHART ---
                st.subheader(f"⏱️ Price/Volume History: {selected_stock}")
                df_sub = raw_df[raw_df['TRADING CODE'] == selected_stock]
                
                fig_ts = go.Figure()
                fig_ts.add_trace(go.Scatter(x=df_sub['captured_at'], y=df_sub['LTP*'], name="Price", line=dict(color='#00CC96', width=2)))
                fig_ts.add_trace(go.Bar(x=df_sub['captured_at'], y=df_sub['VOLUME'], name="Volume", yaxis="y2", opacity=0.3, marker_color='#636EFA'))
                
                fig_ts.update_layout(
                    template="plotly_dark",
                    height=500,
                    yaxis=dict(title="Price (BDT)"),
                    yaxis2=dict(title="Volume", overlaying="y", side="right"),
                    legend=dict(orientation="h", y=1.1),
                    margin=dict(l=10, r=10, t=20, b=20)
                )
                st.plotly_chart(fig_ts, use_container_width=True)
    else:
        st.info("No volume-increasing stays detected in this range.")
else:
    st.warning("Waiting for market data... Ensure your collector is running.")

# --- 8. FOOTER ---
st.divider()
st.caption(f"Showing data from {display_start} to {display_end} (Dhaka Time) | Database: UTC")
