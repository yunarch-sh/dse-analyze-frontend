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
        "PDB ALL Price",
        "Excel Approach Profile",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ],
    default=[
        "PDB ALL Price",
        "Excel Approach Profile",
        "Price / Volume History",
        "Price Volume Reconciliation"
    ]
)

dt_start = datetime.combine(sel_date, t_start, tzinfo=dhaka_tz).astimezone(timezone.utc)
dt_end   = datetime.combine(sel_date, t_end,   tzinfo=dhaka_tz).astimezone(timezone.utc)

if st.sidebar.button("Log Out"):
    st.session_state["password_correct"] = False
    st.rerun()

# ---------------- VOL DIFF HELPERS ----------------
# RULE: both functions must be called AFTER time-filtering.
# If called on full_day_df, the first row of the filtered window
# will carry a pre-window cumulative as its diff — phantom volume.

def pdb_vol_diff(grp):
    """
    Backward diff within the filtered window.
      Row N delta = cum[N] - cum[N-1]  →  attributed to price of row N  ✓
      First row   = 0  (pre-window cumulative is out of scope)
      Negatives   = 0  (data-feed resets)
    """
    diff = grp.diff()
    diff.iloc[0] = 0
    return diff.clip(lower=0)


def excel_vol_diff(grp):
    """
    Forward diff attributed to the NEXT row's price.
    Mathematically identical to pdb_vol_diff for all interior rows.
    Kept as a separate column so both charts can be displayed and confirmed to match.

      forward[i]          = cum[i+1] - cum[i]   (naive: attributed to price of row i   ✗)
      forward_shifted[i]  = cum[i]   - cum[i-1]  (fixed:  attributed to price of row i  ✓)
                          == backward diff

      First row = 0  (no prior interval)
      Last  row = 0  (no next tick — do NOT backward-fill, would double-count)
      Negatives = 0
    """
    diff = grp.diff()
    diff.iloc[0]  = 0
    diff.iloc[-1] = 0
    return diff.clip(lower=0)


# ---------------- DATA FETCH ----------------
@st.cache_data(ttl=60)
def get_daily_data(selected_date):
    """
    Fetch raw full-day data. No diffs here — diffs computed post-filter only.
    """
    try:
        start_of_day = datetime.combine(selected_date, time(0, 0),      tzinfo=dhaka_tz).astimezone(timezone.utc)
        end_of_day   = datetime.combine(selected_date, time(23, 59, 59), tzinfo=dhaka_tz).astimezone(timezone.utc)

        query  = {"captured_at": {"$gte": start_of_day, "$lte": end_of_day}}
        cursor = collection.find(query).sort("captured_at", 1)
        df     = pd.DataFrame(list(cursor))

        if df.empty:
            return df

        df["captured_at"] = (
            pd.to_datetime(df["captured_at"], errors="coerce", utc=True)
              .dt.tz_convert(dhaka_tz)
        )
        df = df.sort_values(["TRADING CODE", "captured_at"]).reset_index(drop=True)
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

# ---------------- APPLY TIME FILTER THEN COMPUTE DIFFS ----------------
#
# Invariant after this block (per stock, within the window):
#   sum(VOL_DIFF_PDB)   == VOLUME.iloc[-1] - VOLUME.iloc[0]
#   sum(VOL_DIFF_EXCEL) == VOLUME.iloc[-1] - VOLUME.iloc[0]  (last row zeroed)
#
# The .reset_index(drop=True) is essential: it makes iloc[0] and iloc[-1]
# inside the transform functions always refer to the window boundaries,
# not the original full-day positions.

full_day_df = get_daily_data(sel_date)

if not full_day_df.empty:
    mask = (
        (full_day_df["captured_at"] >= dt_start.astimezone(dhaka_tz)) &
        (full_day_df["captured_at"] <= dt_end.astimezone(dhaka_tz))
    )
    raw_df = full_day_df.loc[mask].copy().reset_index(drop=True)  # ← critical

    if not raw_df.empty:
        raw_df["VOL_DIFF_PDB"] = (
            raw_df.groupby("TRADING CODE")["VOLUME"]
            .transform(pdb_vol_diff)
        )
        raw_df["VOL_DIFF_EXCEL"] = (
            raw_df.groupby("TRADING CODE")["VOLUME"]
            .transform(excel_vol_diff)
        )
else:
    raw_df = pd.DataFrame()

# ---------------- PRICE STAY ANALYSIS (PDB Logic) ----------------
summary = []
if not raw_df.empty:
    for stock, group in raw_df.groupby("TRADING CODE"):
        if len(group) < 2:
            continue
        group = group.copy()
        group["price_changed"] = group["LTP*"] != group["LTP*"].shift()
        group["stay_id"]       = group["price_changed"].cumsum()

        for stay_id, stay_group in group.groupby("stay_id"):
            if len(stay_group) < 2:
                continue
            price    = float(stay_group["LTP*"].iloc[0])
            start_t  = stay_group["captured_at"].iloc[0]
            end_t    = stay_group["captured_at"].iloc[-1]
            duration = (end_t - start_t).total_seconds() / 60
            vol_diff = int(stay_group["VOL_DIFF_PDB"].sum())
            if vol_diff > 0:
                summary.append({
                    "Stock":       stock,
                    "Price":       price,
                    "Stay (Mins)": round(duration, 1),
                    "Vol Traded":  vol_diff,
                    "Start":       start_t.strftime("%H:%M"),
                    "End":         end_t.strftime("%H:%M"),
                })

analysis_df = (
    pd.DataFrame(summary).sort_values("Stay (Mins)", ascending=False)
    if summary
    else pd.DataFrame(columns=["Stock", "Price", "Stay (Mins)", "Vol Traded", "Start", "End"])
)

# ---------------- RANKED TABLE ----------------
if "Ranked Price Stays Table" in display_options:
    st.subheader("📋 Ranked Price Stays")
    st.dataframe(analysis_df, use_container_width=True, hide_index=True)
    st.divider()

# ---------------- STOCK SELECTOR ----------------
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
    index=stock_list.index(st.session_state["selected_stock"])
    if st.session_state["selected_stock"] in stock_list else 0,
)
st.session_state["selected_stock"] = selected_stock

if not raw_df.empty and selected_stock != "No Data":
    df_sub = raw_df[
        (raw_df["TRADING CODE"] == selected_stock) &
        (raw_df["LTP*"] > 0)
    ].copy()
else:
    df_sub = pd.DataFrame()

# ---------------- PDB STAY PRICE PROFILE ----------------
if "PDB STAY PRICE Profile" in display_options:
    if selected_stock != "No Data" and not df_sub.empty:
        stock_summary = analysis_df[analysis_df["Stock"] == selected_stock]
        profile_data  = (
            stock_summary.groupby("Price").agg(
                Vol_Traded=("Vol Traded", "sum"),
                Stay_Mins=("Stay (Mins)", "sum"),
            ).reset_index().sort_values("Price")
            if not stock_summary.empty
            else pd.DataFrame(columns=["Price", "Vol_Traded", "Stay_Mins"])
        )

        st.subheader(f"📊 PDB STAY PRICE Profile — {selected_stock}")
        pdb_total = profile_data["Vol_Traded"].sum()
        profile_data["Vol % of Total"] = (
            (profile_data["Vol_Traded"] / pdb_total * 100) if pdb_total > 0 else 0
        )

        fig_p = go.Figure()
        fig_p.add_trace(go.Bar(
            y=profile_data["Price"], x=profile_data["Stay_Mins"], orientation="h",
            name="Time Stay", marker_color="#EF553B",
        ))
        fig_p.add_trace(go.Bar(
            y=profile_data["Price"], x=profile_data["Vol_Traded"], orientation="h",
            name="Volume", marker_color="#636EFA", base=profile_data["Stay_Mins"],
            hovertemplate="Price: %{y}<br>Volume: %{x}<br>% of Total: %{customdata:.2f}%",
            customdata=profile_data["Vol % of Total"],
        ))
        fig_p.update_layout(
            barmode="stack", template="plotly_dark",
            xaxis_title="Minutes / Volume", yaxis_title="Price (BDT)",
            height=400 + len(profile_data) * 10,
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=80, b=20),
        )
        st.plotly_chart(fig_p, use_container_width=True)

# ---------------- PDB ALL PRICE PROFILE ----------------
if "PDB ALL Price" in display_options:
    if selected_stock != "No Data" and not df_sub.empty:
        st.subheader(f"📊 PDB ALL Price — {selected_stock}")

        full_profile = (
            df_sub.groupby("LTP*").agg(
                Vol_Traded=("VOL_DIFF_PDB", "sum"),
                Stay_Count=("captured_at", "count"),
            ).reset_index().sort_values("LTP*")
        )
        total_vol_pdb = full_profile["Vol_Traded"].sum()
        full_profile["Vol % of Total"] = (
            (full_profile["Vol_Traded"] / total_vol_pdb * 100) if total_vol_pdb > 0 else 0
        )

        fig_full = go.Figure()
        fig_full.add_trace(go.Bar(
            y=full_profile["LTP*"], x=full_profile["Stay_Count"], orientation="h",
            name="Time Stay", marker_color="#EF553B",
        ))
        fig_full.add_trace(go.Bar(
            y=full_profile["LTP*"], x=full_profile["Vol_Traded"], orientation="h",
            name="Volume", marker_color="#00CC96", base=full_profile["Stay_Count"],
            hovertemplate="Price: %{y}<br>Volume: %{x}<br>% of Total: %{customdata:.2f}%",
            customdata=full_profile["Vol % of Total"],
        ))
        fig_full.update_layout(
            barmode="stack", template="plotly_dark",
            xaxis_title="Minutes / Volume", yaxis_title="Price (BDT)",
            height=400 + len(full_profile) * 10,
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=80, b=20),
        )
        st.plotly_chart(fig_full, use_container_width=True)

# ---------------- EXCEL APPROACH PROFILE ----------------
if "Excel Approach Profile" in display_options:
    if selected_stock != "No Data" and not df_sub.empty:
        st.subheader(f"📊 Excel Approach (Forward Diff — Price-Corrected) — {selected_stock}")

        st.info(
            "**Excel Logic (Fixed):** Forward diff shifted to next row's price equals backward diff. "
            "Volume is attributed to the price it actually traded at. "
            "PDB and Excel volumes are identical — both are correct."
        )

        excel_profile = (
            df_sub.groupby("LTP*").agg(
                Vol_Traded=("VOL_DIFF_EXCEL", "sum"),
                Stay_Count=("captured_at", "count"),
            ).reset_index().sort_values("LTP*")
        )
        total_vol_excel = excel_profile["Vol_Traded"].sum()
        excel_profile["Vol % of Total"] = (
            (excel_profile["Vol_Traded"] / total_vol_excel * 100) if total_vol_excel > 0 else 0
        )

        fig_excel = go.Figure()
        fig_excel.add_trace(go.Bar(
            y=excel_profile["LTP*"], x=excel_profile["Stay_Count"], orientation="h",
            name="Time Stay", marker_color="#EF553B",
        ))
        fig_excel.add_trace(go.Bar(
            y=excel_profile["LTP*"], x=excel_profile["Vol_Traded"], orientation="h",
            name="Excel Volume", marker_color="#AB63FA", base=excel_profile["Stay_Count"],
            hovertemplate="Price: %{y}<br>Excel Volume: %{x}<br>% of Total: %{customdata:.2f}%",
            customdata=excel_profile["Vol % of Total"],
        ))
        fig_excel.update_layout(
            barmode="stack", template="plotly_dark",
            xaxis_title="Minutes / Volume", yaxis_title="Price (BDT)",
            height=400 + len(excel_profile) * 10,
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=80, b=20),
        )
        st.plotly_chart(fig_excel, use_container_width=True)

# ---------------- PRICE / VOLUME HISTORY ----------------
if "Price / Volume History" in display_options:
    if not df_sub.empty:
        st.subheader(f"⏱️ Price / Volume History — {selected_stock}")

        fig_hist = go.Figure()
        fig_hist.add_trace(go.Scatter(
            x=df_sub["captured_at"], y=df_sub["LTP*"],
            name="Price", line=dict(color="#00CC96"),
        ))
        fig_hist.add_trace(go.Bar(
            x=df_sub["captured_at"], y=df_sub["VOL_DIFF_PDB"],
            name="Volume Delta (PDB)", yaxis="y2",
            opacity=0.6, marker_color="#636EFA",
        ))
        fig_hist.update_layout(
            template="plotly_dark", height=400,
            yaxis=dict(title="Price"),
            yaxis2=dict(overlaying="y", side="right", title="Volume"),
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=20, b=20),
        )
        st.plotly_chart(fig_hist, use_container_width=True)

# ---------------- PRICE VOLUME RECONCILIATION ----------------
if "Price Volume Reconciliation" in display_options:
    if selected_stock != "No Data" and not df_sub.empty:
        st.subheader(f"✅ Price Volume Reconciliation — {selected_stock}")

        recon_profile = (
            df_sub.groupby("LTP*")
            .agg(Vol_By_Price=("VOL_DIFF_PDB", "sum"))
            .reset_index()
            .sort_values("LTP*")
            .rename(columns={"LTP*": "Price"})
        )

        # Ground truth = last cumulative − first cumulative inside the window.
        # This excludes all volume that traded before the window opened,
        # matching exactly what sum(VOL_DIFF_PDB) can produce.
        cumulative_total = int(df_sub["VOLUME"].iloc[-1] - df_sub["VOLUME"].iloc[0])
        price_vol_sum    = int(recon_profile["Vol_By_Price"].sum())
        diff             = cumulative_total - price_vol_sum
        match            = abs(diff) == 0

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Cumulative Vol (Last − First)", f"{cumulative_total:,}")
        col2.metric("Sum of Price-Level Vols (PDB)", f"{price_vol_sum:,}")
        col3.metric("Difference", f"{diff:,}")
        col4.metric("Reconciled?", "✅ YES" if match else "⚠️ NO")

        if not match:
            st.warning(
                f"⚠️ Gap of **{diff:,}** units. Likely cause: missing ticks in the data feed "
                "or negative volume corrections clipped to 0."
            )

        fig_recon = go.Figure()
        fig_recon.add_trace(go.Bar(
            y=recon_profile["Price"],
            x=recon_profile["Vol_By_Price"],
            orientation="h",
            name="Vol Traded at Price",
            marker_color="#00CC96",
            hovertemplate="Price: %{y}<br>Vol at Price: %{x:,}<extra></extra>",
        ))
        fig_recon.add_vline(
            x=cumulative_total,
            line_dash="dash",
            line_color="#EF553B",
            annotation_text=f"Cumulative Total: {cumulative_total:,}",
            annotation_position="top right",
            annotation_font_color="#EF553B",
        )
        fig_recon.update_layout(
            template="plotly_dark",
            xaxis_title="Volume Traded",
            yaxis_title="Price (BDT)",
            height=400 + len(recon_profile) * 10,
            legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center"),
            margin=dict(l=10, r=10, t=80, b=20),
        )
        st.plotly_chart(fig_recon, use_container_width=True)

        st.markdown("**📄 Price-Level Volume Breakdown**")

        recon_profile["Vol % of Cumulative"] = (
            (recon_profile["Vol_By_Price"] / cumulative_total * 100).round(2)
            if cumulative_total > 0 else 0
        )
        recon_display = recon_profile[["Price", "Vol_By_Price", "Vol % of Cumulative"]].copy()
        recon_display.columns = ["Price (BDT)", "Vol Traded", "% of Cumulative Total"]

        totals_row = pd.DataFrame([{
            "Price (BDT)":           "TOTAL",
            "Vol Traded":            price_vol_sum,
            "% of Cumulative Total": round(price_vol_sum / cumulative_total * 100, 2) if cumulative_total > 0 else 0,
        }])
        recon_display = pd.concat([recon_display, totals_row], ignore_index=True)
        st.dataframe(recon_display, use_container_width=True, hide_index=True)
