import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.express as px
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

# --- 1. SECURE DATABASE CONNECTION ---
# We use st.secrets so your password NEVER appears on your public GitHub.
# --- 0. BASIC AUTHENTICATION ---
def check_password():
    """Returns True if the user had the correct password."""
    def password_entered():
        if st.session_state["username"] == st.secrets["LOGIN_USER"] and \
           st.session_state["password"] == st.secrets["LOGIN_PASS"]:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # don't store password
            del st.session_state["username"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        # First run, show inputs for username and password
        st.text_input("Username", on_change=password_entered, key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        # Password not correct, show input + error
        st.text_input("Username", on_change=password_entered, key="username")
        st.text_input("Password", type="password", on_change=password_entered, key="password")
        st.error("😕 User not known or password incorrect")
        return False
    else:
        # Password correct.
        return True

if not check_password():
    st.stop()  # Do not run the rest of the app if not authenticated
    
try:
    # This looks for the "MONGO_URI" you will paste into Streamlit's Advanced Settings
    MONGO_URI = st.secrets["MONGO_URI"]
    client = MongoClient(MONGO_URI)
    db = client['DSE_Market_Data']
    collection = db['price_logs']
except Exception as e:
    st.error("Database Connection Failed. Did you set your MONGO_URI in Streamlit Secrets?")
    st.stop()

# --- 2. APP CONFIGURATION ---
st.set_page_config(page_title="DSE Stay Analytics", layout="wide")

# This automatically refreshes the page every 60 seconds to show new data
st_autorefresh(interval=60000, key="datarefresh")

# --- 3. SIDEBAR CONTROLS ---
st.sidebar.header("🕹️ Dashboard Controls")
view_mode = st.sidebar.radio("Select View", ["Market Grid", "Stock Deep Dive"])

# Get all unique trading codes currently in your database
all_stocks = sorted(collection.distinct("TRADING CODE"))

if view_mode == "Market Grid":
    num_stocks = st.sidebar.slider("Number of stocks to show", 4, len(all_stocks), 12)
    search_query = st.sidebar.text_input("🔍 Search Stock Code").upper()
else:
    target_stock = st.sidebar.selectbox("🎯 Select Stock to Analyze", all_stocks)

# --- 4. HELPER FUNCTION ---
def get_stock_data(symbol):
    cursor = collection.find({"TRADING CODE": symbol}).sort("captured_at", 1)
    return pd.DataFrame(list(cursor))

# --- 5. MODE: MARKET GRID ---
if view_mode == "Market Grid":
    st.title("🏙️ DSE Market Pulse Grid")
    
    # Filter stocks based on search or slider
    display_list = [s for s in all_stocks if search_query in s] if search_query else all_stocks[:num_stocks]
    
    cols_count = 3
    rows = [display_list[i:i + cols_count] for i in range(0, len(display_list), cols_count)]

    for row in rows:
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
                        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

# --- 6. MODE: STOCK DEEP DIVE ---
else:
    st.title(f"🔎 Analysis: {target_stock}")
    df = get_stock_data(target_stock)
    
    if not df.empty:
        col1, col2 = st.columns([2, 1])
        
        with col1:
            st.subheader("Intraday Price Movement")
            fig_line = px.line(df, x='captured_at', y='LTP*', markers=True, template="plotly_dark")
            st.plotly_chart(fig_line, use_container_width=True)
        
        with col2:
            st.subheader("Price 'Stay' Duration")
            stay_df = df['LTP*'].value_counts().reset_index()
            stay_df.columns = ['Price', 'Minutes']
            stay_df = stay_df.sort_values(by='Price', ascending=False)
            
            fig_stay = px.bar(stay_df, x='Minutes', y='Price', orientation='h', 
                              color='Minutes', color_continuous_scale='Bluered_r')
            fig_stay.update_layout(yaxis={'type': 'category'})
            st.plotly_chart(fig_stay, use_container_width=True)

        # Analysis Card
        max_stay = stay_df.sort_values(by='Minutes', ascending=False).iloc[0]
        st.success(f"**Longest Stay:** Price **{max_stay['Price']}** held for **{max_stay['Minutes']} minutes** today.")
    else:
        st.warning("No data found for this symbol.")

# --- 7. FOOTER ---
st.divider()
st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')} | Data from MongoDB Atlas")
