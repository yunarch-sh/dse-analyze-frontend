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

# ---------------- SIDEBAR FILTERS & DISPLAY OPTIONS ----------------
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
        "Exact Excel Workflow Profile",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ],
    default=[
        "Exact Excel Workflow Profile",
        "Price Volume Reconciliation"
    ]
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)
display_start = dt_start.astimezone(dhaka_tz).strftime("%H:%M")
display_end = dt_end.astimezone(dhaka_tz).strftime("%H:%M")

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

        # Ensure correct chronological order
        df = df.sort_values(["TRADING CODE", "captured_at"])

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

# ---------------- APPLY TIME FILTERS ----------------
full_day_df = get_daily_data_with_vol(sel_date)

if not full_day_df.empty:
    mask = (
        (full_day_df["captured_at"] >= dt_start.astimezone(dhaka_tz)) &
        (full_day_df["captured_at"] <= dt_end.astimezone(dhaka_tz))
    )
    raw_df = full_day_df.loc[mask].copy()
else:
    raw_df = pd.DataFrame()

# ---------------- DETAILED VIEW SELECTION ----------------
stock_list = sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty else ["No Data"]

if "selected_stock" not in st.session_state:
    st.session_state["selected_stock"] = stock_list[0]

selected_stock = st.selectbox(
    "🔍 Select Stock for Detailed View",
    stock_list,
    index=stock_list.index(st.session_state["selected_stock"]) if st.session_state["selected_stock"] in stock_list else 0
)
st.session_state["selected_stock"] = selected_stock

# ---------------- EXACT EXCEL LOGIC PROCESSING ----------------
if not raw_df.empty and selected_stock != "No Data":
    # Step 1: Copy all the data of a certain share
    df_stock = raw_df[raw_df["TRADING CODE"] == selected_stock].copy()
    
    # Step 2: Add a new column DV. = (Current - Previous). First row becomes 0.
    df_stock["DV"] = df_stock["VOLUME"].diff().fillna(0)
    
    # Step 3: Filter out all the zeros
    df_filtered = df_stock[df_stock["DV"] != 0].copy()
else:
    df_filtered = pd.DataFrame()

# ---------------- EXACT EXCEL WORKFLOW PROFILE ----------------
if "Exact Excel Workflow Profile" in display_options:
    if selected_stock != "No Data" and not df_filtered.empty:
        st.subheader(f"📊 Exact Excel Workflow Profile — {selected_stock}")
        
        st.info("This graph perfectly mirrors your 5 steps: Create DV column via subtraction -> Filter out 0s -> Sort by Price -> Sum DV.")

        # Step 4 & 5: Sort by price and Sum all the DVs for the price
        excel_profile = df_filtered.groupby("LTP*").agg(
            Vol_Traded=("DV", "sum"),
            Tick_Count=("captured_at", "count") # Number of rows left after filtering 0s
        ).reset_index().sort_values("LTP*")

        total_volume = excel_profile["Vol_Traded"].sum()
        excel_profile["Vol % of Total"] = (
            (excel_profile["Vol_Traded"] / total_volume * 100) if total_volume > 0 else 0
        )

        fig_combined = go.Figure()
        fig_combined.add_trace(go.Bar(
            y=excel_profile["LTP*"], x=excel_profile["Tick_Count"], orientation="h",
            name="Trade Ticks (Rows)", marker_color="#EF553B"
        ))
        fig_combined.add_trace(go.Bar(
            y=excel_profile["LTP*"], x=excel_profile["Vol_Traded"], orientation="h",
            name="Sum of DV", marker_color="#00CC96", base=excel_profile["Tick_Count"],
            hovertemplate="Price: %{y}<br>DV Sum: %{x:,}<br>Percent of total: %{customdata:.2f}%",
            customdata=excel_profile["Vol % of Total"]
        ))
        fig_combined.update_layout(
            barmode="stack", template="plotly_dark",
            xaxis_title="Rows / Sum of DV", yaxis_title="Price (BDT)",
            height=400 + len(excel_profile) * 15,
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=80, b=20)
        )
        st.plotly_chart(fig_combined, use_container_width=True)

# ---------------- PRICE VOLUME RECONCILIATION ----------------
if "Price Volume Reconciliation" in display_options:
    if selected_stock != "No Data" and not df_stock.empty:
        st.subheader(f"✅ Price Volume Reconciliation — {selected_stock}")

        # Grouping by price using the exact Excel Logic
        recon_profile = df_filtered.groupby("LTP*").agg(
            Vol_By_Price=("DV", "sum")
        ).reset_index().sort_values("LTP*")
        recon_profile.rename(columns={"LTP*": "Price"}, inplace=True)

        # The math check
        final_cumulative_val = int(df_stock["VOLUME"].iloc[-1])
        first_cumulative_val = int(df_stock["VOLUME"].iloc[0])
        total_accumulated = final_cumulative_val - first_cumulative_val
        
        price_vol_sum = int(recon_profile["Vol_By_Price"].sum())
        
        # The difference is exactly the missing initial volume + any negatives filtered out
        diff = final_cumulative_val - price_vol_sum

        col1, col2, col3 = st.columns(3)
        col1.metric("Final Cumulative Vol (from DB)", f"{final_cumulative_val:,}")
        col2.metric("Your Excel Sum (Sum of DV)", f"{price_vol_sum:,}")
        col3.metric("Missing Volume", f"{diff:,}")

        st.warning(
            f"⚠️ **Why is there a gap of {diff:,}?** \n\n"
            f"Because you started subtracting at a later row and set the top to `0`, any volume recorded "
            f"in the very first row(s) was skipped by your formula. If you update your Excel sheet's first DV cell to `=C2` (your starting volume) "
            f"instead of `0`, the numbers will match perfectly."
        )

        st.markdown("**📄 Price-Level Volume Breakdown**")
        recon_profile["Vol % of Sum"] = (
            (recon_profile["Vol_By_Price"] / price_vol_sum * 100).round(2)
            if price_vol_sum > 0 else 0
        )

        recon_display = recon_profile[["Price", "Vol_By_Price", "Vol % of Sum"]].copy()
        recon_display.columns = ["Price (BDT)", "Sum of DV", "% of Total Sum"]

        totals_row = pd.DataFrame([{
            "Price (BDT)": "TOTAL",
            "Sum of DV": price_vol_sum,
            "% of Total Sum": 100.00
        }])
        recon_display = pd.concat([recon_display, totals_row], ignore_index=True)

        st.dataframe(recon_display, use_container_width=True, hide_index=True)
