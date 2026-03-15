import streamlit as st
import pandas as pd
from pymongo import MongoClient
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, time
import pytz
from streamlit_autorefresh import st_autorefresh

# ---------------- GLOBAL SETTINGS ----------------
dhaka_tz = pytz.timezone("Asia/Dhaka")

st.set_page_config(
    page_title="DSE Alpha Tracker",
    layout="wide",
)

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
            if (
                username == st.secrets["LOGIN_USER"]
                and password == st.secrets["LOGIN_PASS"]
            ):
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

sel_date = st.sidebar.date_input(
    "Select Date",
    datetime.now(dhaka_tz)
)

t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10, 0), time(14, 30)),
    format="HH:mm",
)

dt_start = dhaka_tz.localize(
    datetime.combine(sel_date, t_start)
).astimezone(pytz.UTC)

dt_end = dhaka_tz.localize(
    datetime.combine(sel_date, t_end)
).astimezone(pytz.UTC)

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

# ---------------- MAIN PAGE ----------------
st.title("📊 DSE Alpha Tracker")

if raw_df.empty:
    st.warning("Waiting for market data from MongoDB...")
    st.stop()


# ---------------- PRICE STAY ANALYSIS ----------------
summary = []

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

        start_time = stay_group["captured_at"].iloc[0]
        end_time = stay_group["captured_at"].iloc[-1]

        duration = (end_time - start_time).total_seconds() / 60

        vol_diff = int(
            stay_group["VOLUME"].iloc[-1] - stay_group["VOLUME"].iloc[0]
        )

        if vol_diff > 0:
            summary.append(
                {
                    "Stock": stock,
                    "Price": price,
                    "Stay (Mins)": round(duration, 1),
                    "Vol Traded": vol_diff,
                    "Start": start_time.strftime("%H:%M"),
                    "End": end_time.strftime("%H:%M"),
                }
            )

if not summary:
    st.info("No volume-increasing price stays detected.")
    st.stop()


analysis_df = pd.DataFrame(summary)

analysis_df = analysis_df.sort_values("Stay (Mins)", ascending=False)

# ---------------- RANKED TABLE ----------------
st.subheader("📋 Ranked Price Stays")

st.dataframe(
    analysis_df,
    use_container_width=True,
    hide_index=True
)

st.divider()

# ---------------- STOCK SELECT ----------------
selected_stock = st.selectbox(
    "🔍 Select Stock for Detailed View",
    sorted(analysis_df["Stock"].unique())
)

# ---------------- MARKET PROFILE ----------------
stock_summary = analysis_df[
    analysis_df["Stock"] == selected_stock
].copy()

profile_data = (
    stock_summary.groupby("Price")
    .agg(
        {
            "Vol Traded": "sum",
            "Stay (Mins)": "sum",
        }
    )
    .reset_index()
    .sort_values("Price")
)

st.subheader(f"📊 Market Profile — {selected_stock}")

fig = make_subplots(specs=[[{"secondary_y": False}]])

fig.add_trace(
    go.Bar(
        y=profile_data["Price"],
        x=profile_data["Vol Traded"],
        orientation="h",
        name="Volume",
        marker_color="#636EFA",
    )
)

fig.add_trace(
    go.Bar(
        y=profile_data["Price"],
        x=profile_data["Stay (Mins)"],
        orientation="h",
        name="Time (Minutes)",
        marker_color="#EF553B",
        xaxis="x2",
    )
)

fig.update_layout(
    template="plotly_dark",
    barmode="group",
    height=300 + len(profile_data) * 40,

    yaxis=dict(
        title=dict(text="Price Level (BDT)")
    ),

    xaxis=dict(
        title=dict(
            text="Total Volume",
            font=dict(color="#636EFA"),
        ),
        tickfont=dict(color="#636EFA"),
    ),

    xaxis2=dict(
        title=dict(
            text="Total Stay (Minutes)",
            font=dict(color="#EF553B"),
        ),
        tickfont=dict(color="#EF553B"),
        overlaying="x",
        side="top",
        showgrid=False,
    ),

    legend=dict(
        orientation="h",
        y=1.1,
        x=0.5,
        xanchor="center",
    ),

    margin=dict(l=10, r=10, t=80, b=20),
)

st.plotly_chart(fig, use_container_width=True)

st.divider()

# ---------------- PRICE HISTORY ----------------
st.subheader(f"⏱️ Price / Volume History — {selected_stock}")

df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock]

fig_hist = go.Figure()

fig_hist.add_trace(
    go.Scatter(
        x=df_sub["captured_at"],
        y=df_sub["LTP*"],
        name="Price",
        line=dict(width=2, color="#00CC96"),
    )
)

fig_hist.add_trace(
    go.Bar(
        x=df_sub["captured_at"],
        y=df_sub["VOLUME"],
        name="Volume",
        yaxis="y2",
        opacity=0.3,
    )
)

fig_hist.update_layout(
    template="plotly_dark",
    height=400,

    yaxis=dict(title="Price"),

    yaxis2=dict(
        title="Cumulative Volume",
        overlaying="y",
        side="right",
    ),

    legend=dict(
        orientation="h",
        y=1.1,
        x=0.5,
        xanchor="center",
    ),

    margin=dict(l=10, r=10, t=20, b=20),
)

st.plotly_chart(fig_hist, use_container_width=True)

st.divider()

st.caption(
    f"Showing data from {display_start} to {display_end} (Dhaka Time)"
)
