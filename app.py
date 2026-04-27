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

# ---------------- SIDEBAR ----------------
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
        "PDB ALL Price",
        "Price Stay Duration",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ],
    default=[
        "PDB ALL Price",
        "Price Stay Duration",
        "Price / Volume History",
    ]
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data_with_vol(selected_date):
    start_of_day = datetime.combine(selected_date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end_of_day = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)

    query = {"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}
    cursor = collection.find(query).sort("captured_at", 1)
    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    df["captured_at"] = pd.to_datetime(df["captured_at"], errors='coerce', utc=True)
    df["captured_at"] = df["captured_at"].dt.tz_convert(dhaka_tz)

    df = df.sort_values(["TRADING CODE", "captured_at"])

    df["DV"] = df.groupby("TRADING CODE")["VOLUME"].diff().fillna(0)
    df["DV"] = df["DV"].clip(lower=0)

    df["VOL_DIFF_PDB"] = df["DV"]

    return df

# ---------------- MANUAL REFRESH ----------------
if "refresh_click" not in st.session_state:
    st.session_state["refresh_click"] = 0

if st.sidebar.button("🔄 Refresh Data"):
    st.session_state["refresh_click"] += 1
    st.cache_data.clear()

# ---------------- APPLY FILTERS ----------------
full_day_df = get_daily_data_with_vol(sel_date)

if not full_day_df.empty:
    mask = (
        (full_day_df["captured_at"] >= dt_start.astimezone(dhaka_tz)) &
        (full_day_df["captured_at"] <= dt_end.astimezone(dhaka_tz))
    )
    raw_df = full_day_df.loc[mask].copy()
else:
    raw_df = pd.DataFrame()

# ---------------- PRICE STAY ANALYSIS ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"] = group["price_changed"].cumsum()

        for _, stay_group in group.groupby("stay_id"):
            if len(stay_group) < 2:
                continue

            summary.append({
                "Stock": stock,
                "Price": stay_group["LTP*"].iloc[0],
                "Stay (Mins)": (stay_group["captured_at"].iloc[-1] -
                                stay_group["captured_at"].iloc[0]).total_seconds() / 60,
                "Vol Traded": stay_group["DV"].sum(),
                "Start": stay_group["captured_at"].iloc[0],
                "End": stay_group["captured_at"].iloc[-1]
            })

analysis_df = pd.DataFrame(summary)

# ---------------- RANKED TABLE ----------------
if "Ranked Price Stays Table" in display_options:
    st.subheader("📋 Ranked Price Stays")
    st.dataframe(analysis_df, use_container_width=True, hide_index=True)
    st.divider()

# ---------------- STOCK SELECTION ----------------
stock_list = (
    sorted(analysis_df["Stock"].unique()) if not analysis_df.empty
    else sorted(raw_df["TRADING CODE"].unique()) if not raw_df.empty
    else ["No Data"]
)

selected_stock = st.selectbox("🔍 Select Stock", stock_list)

df_sub = raw_df[raw_df["TRADING CODE"] == selected_stock] if selected_stock != "No Data" else pd.DataFrame()

# ---------------- COMPUTE FULL PROFILE (shared) ----------------
full_profile = pd.DataFrame()
if not df_sub.empty:
    full_profile = df_sub.groupby("LTP*").agg(
        Vol_Traded=("DV", "sum"),
        Stay_Count=("captured_at", "count")
    ).reset_index().sort_values("LTP*")
    full_profile = full_profile[
        ~((full_profile["LTP*"] == 0) & (full_profile["Vol_Traded"] == 0))
    ]

# ---------------- PDB ALL PRICE ----------------
if "PDB ALL Price" in display_options and not full_profile.empty:
    st.subheader(f"📊 PDB ALL Price — {selected_stock}")

    total_volume = full_profile["Vol_Traded"].sum()
    full_profile["Vol % of Total"] = (
        (full_profile["Vol_Traded"] / total_volume * 100) if total_volume > 0 else 0
    )

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=full_profile["LTP*"], x=full_profile["Stay_Count"],
        orientation="h", name="Time Stay", marker_color="#EF553B"
    ))
    fig.add_trace(go.Bar(
        y=full_profile["LTP*"], x=full_profile["Vol_Traded"],
        orientation="h", name="Volume", marker_color="#00CC96",
        base=full_profile["Stay_Count"],
        customdata=full_profile["Vol % of Total"],
        hovertemplate="Price: %{y}<br>Volume: %{x}<br>% of total: %{customdata:.2f}%"
    ))

    fig.update_layout(
        barmode="stack",
        template="plotly_dark",
        xaxis_title="Snapshots / Volume",
        yaxis_title="Price (BDT)",
        height=400 + len(full_profile) * 10
    )
    st.plotly_chart(fig, use_container_width=True)

# ---------------- PRICE STAY DURATION ----------------
if "Price Stay Duration" in display_options and not full_profile.empty:
    st.subheader(f"⏱️ Price Stay Duration — {selected_stock}")

    stay_profile = full_profile[full_profile["LTP*"] > 0].copy()
    total_stay = stay_profile["Stay_Count"].sum()
    stay_profile["Stay %"] = (stay_profile["Stay_Count"] / total_stay * 100).round(2)

    fig_stay = go.Figure()
    fig_stay.add_trace(go.Bar(
        y=stay_profile["LTP*"].astype(str),
        x=stay_profile["Stay_Count"],
        orientation="h",
        marker_color="#EF553B",
        customdata=stay_profile["Stay %"],
        hovertemplate="Price: %{y}<br>Snapshots: %{x}<br>% of total time: %{customdata:.2f}%"
    ))

    fig_stay.update_layout(
        template="plotly_dark",
        xaxis_title="Snapshots",
        yaxis_title="Price (BDT)",
        height=400 + len(stay_profile) * 10,
        yaxis=dict(
            type="category",
            categoryorder="array",
            categoryarray=stay_profile["LTP*"].astype(str).tolist()
        )
    )
    st.plotly_chart(fig_stay, use_container_width=True)

# ---------------- PRICE / VOLUME HISTORY ----------------
if "Price / Volume History" in display_options:
    if not df_sub.empty:
        st.subheader(f"⏱️ Price / Volume History — {selected_stock}")

        df_hist = df_sub[df_sub["LTP*"] > 0].sort_values("captured_at").copy()

        fig_hist = go.Figure()

        fig_hist.add_trace(go.Scatter(
            x=df_hist["captured_at"],
            y=df_hist["LTP*"],
            name="Price (LTP*)",
            line=dict(color="#00CC96", width=2),
            yaxis="y1"
        ))

        fig_hist.add_trace(go.Bar(
            x=df_hist["captured_at"],
            y=df_hist["VOL_DIFF_PDB"],
            name="Volume Delta (PDB)",
            marker_color="#636EFA",
            opacity=0.4,
            yaxis="y2"
        ))

        fig_hist.update_layout(
            template="plotly_dark",
            height=450,
            barmode="overlay",
            hovermode="x unified",
            xaxis=dict(title="Time"),
            yaxis=dict(title="Price", side="left"),
            yaxis2=dict(
                title="Volume (ΔPDB)",
                overlaying="y",
                side="right",
                showgrid=False
            ),
            legend=dict(orientation="h")
        )

        st.plotly_chart(fig_hist, use_container_width=True)

# ---------------- RECONCILIATION ----------------
if "Price Volume Reconciliation" in display_options and not df_sub.empty:
    st.subheader("✅ Reconciliation")

    recon_profile = df_sub.groupby("LTP*").agg(
        Vol_By_Price=("DV", "sum")
    ).reset_index()

    cumulative_total = int(df_sub["VOLUME"].iloc[-1] - df_sub["VOLUME"].iloc[0])
    price_vol_sum = int(recon_profile["Vol_By_Price"].sum())

    st.metric("Cumulative", cumulative_total)
    st.metric("Summed DV", price_vol_sum)
    st.metric("Difference", cumulative_total - price_vol_sum)
