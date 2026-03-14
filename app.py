import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.express as px
from datetime import datetime
import pytz # Added for timezone conversion
from streamlit_autorefresh import st_autorefresh

# --- 1. SECURE DATABASE CONNECTION & AUTHENTICATION ---
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        if st.session_state["username"] == st.secrets["LOGIN_USER"] and \
           st.session_state["password"] == st.secrets["LOGIN_PASS"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.text_input("Username", on_change=password_entered, key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.text_input("Username", on_change=password_entered, key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("😕 User not known or password incorrect")
        return False
    else:
        return True

if not check_password():
    st.stop()  
    
try:
    MONGO_URI = st.secrets["MONGO_URI"]
    client = MongoClient(MONGO_URI)
    db = client['DSE_Market_Data']
    collection = db['price_logs']
except Exception as e:
    st.error("Database Connection Failed. Check your Streamlit Secrets.")
    st.stop()

# --- 2. APP CONFIGURATION ---
st.set_page_config(page_title="DSE Stay Analytics", layout="wide")
st_autorefresh(interval=60000, key="datarefresh")

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.header("🕹️ Dashboard Controls")
view_mode = st.sidebar.radio("Select View", ["Market Grid", "Stock Deep Dive"])

# Get all unique trading codes
all_stocks = sorted(collection.distinct("TRADING CODE"))

if view_mode == "Market Grid":
    num_stocks = st.sidebar.slider("Number of stocks to show", 4, len(all_stocks) if all_stocks else 12, 12)
    search_query = st.sidebar.text_input("🔍 Search Stock Code").upper()
else:
    target_stock = st.sidebar.selectbox("🎯 Select Stock to Analyze", all_stocks)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# --- 4. HELPER FUNCTION (With Timezone Fix) ---
def get_stock_data(symbol):
    cursor = collection.find({"TRADING CODE": symbol}).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))
    
    if not df.empty:
        # 1. Convert to datetime objects
        df['captured_at'] = pd.to_datetime(df['captured_at'])
        
        # 2. Localize to UTC if not already, then convert to Dhaka
        if df['captured_at'].dt.tz is None:
            df['captured_at'] = df['captured_at'].dt.tz_localize('UTC')
        
        df['captured_at'] = df['captured_at'].dt.tz_convert('Asia/Dhaka')
    
    return df

# --- 5. MODE: MARKET GRID ---
if view_mode == "Market Grid":
    st.title("🏙️ DSE Market Pulse Grid")
    
    display_list = [s for s in all_stocks if search_query in s] if search_query else all_stocks[:num_stocks]
    
    cols_count = 3
    rows = [display_list[i:i + cols_count] for i in range(0, len(display_list), cols_count)]

    for row_idx, row in enumerate(rows):
        cols = st.columns(cols_count)
        for i, stock_code in enumerate(row):
            with cols[i]:
                df = get_stock_data(stock_code)
                if not df.empty:
                    current = df['LTP*'].iloc[-1]
                    prev = df['LTP*'].iloc[0]
                    delta = current - prev
                    
                    with st.container(border=True):
                        st.metric(label=stock_code, value=f"{current} BDT", delta=f"{delta:.2f}")
                        fig = px.line(df, x='captured_at', y='LTP*')
                        fig.update_layout(height=100, margin=dict(l=0,r=0,t=0,b=0),
                                          xaxis_visible=False, yaxis_visible=False,
                                          showlegend=False, plot_bgcolor='rgba(0,0,0,0)')
                        # Added unique key to prevent DuplicateID error
                        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False}, key=f"grid_{stock_code}")
                else:
                    st.info(f"No data for {stock_code}")

# --- 6. MODE: STOCK DEEP DIVE ---
else:
    st.title(f"🔎 Analysis: {target_stock}")
    df = get_stock_data(target_stock)
    
    if not df.empty:
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("Intraday Price Movement")
            fig_line = px.line(df, x='captured_at', y='LTP*', markers=True, template="plotly_dark")
            # Added unique key for stability
            st.plotly_chart(fig_line, use_container_width=True, key="dive_line")
        
        with col2:
            st.subheader("Price 'Stay' Duration")
            stay_df = df['LTP*'].value_counts().reset_index()
            stay_df.columns = ['Price', 'Minutes']
            stay_df = stay_df.sort_values(by='Price', ascending=False)
            
            fig_stay = px.bar(stay_df, x='Minutes', y='Price', orientation='h', 
                              color='Minutes', color_continuous_scale='Bluered_r')
            fig_stay.update_layout(yaxis={'type': 'category'})
            st.plotly_chart(fig_stay, use_container_width=True, key="dive_bar")

        max_stay = stay_df.sort_values(by='Minutes', ascending=False).iloc[0]
        st.success(f"**Longest Stay:** Price **{max_stay['Price']}** held for **{max_stay['Minutes']} minutes** today.")
    else:
        st.warning("No data found for this symbol.")

# --- 7. FOOTER ---
st.divider()
# Use Dhaka time for the footer display
now_bd = datetime.now(pytz.timezone('Asia/Dhaka')).strftime('%H:%M:%S')
st.caption(f"Last updated: {now_bd} (Dhaka Time) | Data from MongoDB Atlas")
