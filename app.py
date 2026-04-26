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

# ---------------- AUTH ----------------
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

# ---------------- DB ----------------
@st.cache_resource
def init_connection():
    client = MongoClient(st.secrets["MONGO"]["MONGO_URI"])
    db = client["DSE_Market_Data"]
    return db["price_logs"]

collection = init_connection()

# ---------------- FILTER ----------------
st.sidebar.header("Filters")

sel_date = st.sidebar.date_input("Select Date", datetime.now(dhaka_tz))

t_start, t_end = st.sidebar.slider(
    "Time Range",
    value=(time(10, 0), time(14, 30)),
    format="HH:mm"
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end = datetime.combine(sel_date, t_end, tzinfo=dhaka_tz).astimezone(timezone.utc)

# ---------------- DATA ----------------
@st.cache_data(ttl=60)
def get_data(selected_date):
    start = datetime.combine(selected_date, time(0, 0), tzinfo=dhaka_tz).astimezone(timezone.utc)
    end = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)

    cursor = collection.find({
        "captured_at": {"$gte": start, "$lte": end}
    }).sort("captured_at", 1)

    df = pd.DataFrame(list(cursor))

    if df.empty:
        return df

    df["captured_at"] = pd.to_datetime(df["captured_at"], utc=True).dt.tz_convert(dhaka_tz)
    return df.sort_values(["TRADING CODE", "captured_at"]).reset_index(drop=True)


full_df = get_data(sel_date)

if full_df.empty:
    st.stop()

mask = (full_df["captured_at"] >= dt_start.astimezone(dhaka_tz)) & \
       (full_df["captured_at"] <= dt_end.astimezone(dhaka_tz))

df = full_df.loc[mask].copy().reset_index(drop=True)

# ---------------- TRUE PDB BACKWARD DIFF ----------------
def pdb_diff(x):
    d = x.diff()
    d.iloc[0] = 0
    return d.clip(lower=0)

df["VOL_PDB"] = df.groupby("TRADING CODE")["VOLUME"].transform(pdb_diff)

# ---------------- TRUE EXCEL FORWARD DIFF (FIXED) ----------------
# forward attribution: volume belongs to NEXT row price

df["VOL_EXCEL"] = (
    df.groupby("TRADING CODE")["VOLUME"].shift(-1)
    - df["VOLUME"]
).clip(lower=0)

df["EXCEL_PRICE"] = df.groupby("TRADING CODE")["LTP*"].shift(-1)

# ---------------- STOCK ----------------
stocks = sorted(df["TRADING CODE"].unique())

selected_stock = st.selectbox("Select Stock", stocks)

df_sub = df[df["TRADING CODE"] == selected_stock].copy()

# ---------------- PRICE GROUP (PDB VIEW) ----------------
pdb_profile = (
    df_sub.groupby("LTP*")
    .agg(Vol=("VOL_PDB", "sum"))
    .reset_index()
    .sort_values("LTP*")
)

# ---------------- PRICE GROUP (EXCEL VIEW FIXED) ----------------
excel_profile = (
    df_sub.dropna(subset=["EXCEL_PRICE"])
    .groupby("EXCEL_PRICE")
    .agg(Vol=("VOL_EXCEL", "sum"))
    .reset_index()
    .rename(columns={"EXCEL_PRICE": "LTP*"})
    .sort_values("LTP*")
)

# ---------------- RECONCILIATION (TRUTH MODEL) ----------------
recon_profile = (
    df_sub.groupby("LTP*")
    .agg(Vol=("VOL_PDB", "sum"))
    .reset_index()
)

cumulative_total = int(df_sub["VOLUME"].iloc[-1] - df_sub["VOLUME"].iloc[0])
price_sum = int(recon_profile["Vol"].sum())

# ---------------- UI ----------------
st.subheader(f"Stock: {selected_stock}")

# ---------------- EXCEL PROFILE ----------------
st.subheader("📊 Excel Forward Model (Corrected)")

fig1 = go.Figure()
fig1.add_trace(go.Bar(
    y=excel_profile["LTP*"],
    x=excel_profile["Vol"],
    orientation="h",
    name="Excel Forward Volume"
))
fig1.update_layout(template="plotly_dark")
st.plotly_chart(fig1, use_container_width=True)

# ---------------- RECONCILIATION (ONLY TRUTH GRAPH) ----------------
st.subheader("✅ Reconciliation (Truth Model)")

st.metric("Cumulative Volume", cumulative_total)
st.metric("Price Sum Volume", price_sum)
st.metric("Difference", cumulative_total - price_sum)

fig2 = go.Figure()
fig2.add_trace(go.Bar(
    y=recon_profile["LTP*"],
    x=recon_profile["Vol"],
    orientation="h",
    name="PDB Volume by Price"
))

fig2.add_vline(
    x=cumulative_total,
    line_dash="dash",
    line_color="red"
)

fig2.update_layout(template="plotly_dark")
st.plotly_chart(fig2, use_container_width=True)
