import json
import base64
import pandas as pd
import numpy as np
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
from dateutil.relativedelta import relativedelta

# =====================================================
# PAGE CONFIG
# =====================================================
st.set_page_config(page_title="Toys Budget Dashboard", layout="wide")
st.title("🎁 Toys Budget Dashboard")
st.caption("Each Client Receives $25 Every 6 Months (Reset Model)")

# =====================================================
# LOAD SETTINGS
# =====================================================
try:
    SHEET_ID = st.secrets["SHEET_ID"]
    RAW_SHEET_NAME = st.secrets.get("RAW_SHEET_NAME", "Toys")
    BUDGET = float(st.secrets.get("BUDGET", 25))
    CACHE_TTL = int(st.secrets.get("CACHE_TTL_SECONDS", 60))
    SERVICE_ACCOUNT_B64 = st.secrets["GOOGLE_SERVICE_ACCOUNT_B64"]
except Exception:
    st.error("Secrets configuration missing or malformed.")
    st.stop()

# =====================================================
# HELPER FUNCTIONS
# =====================================================

TRUE_VALUES = {"true", "yes", "1", "y", "checked", "x"}

def to_bool(series):
    return (
        series.fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(TRUE_VALUES)
    )

def to_money(series):
    return pd.to_numeric(
        series.fillna("")
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0.0)

def normalize_name(series):
    return (
        series.fillna("")
        .astype(str)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

# =====================================================
# LOAD GOOGLE SHEET (ROBUST VERSION)
# =====================================================

@st.cache_data(ttl=CACHE_TTL)
def load_data():
    decoded = base64.b64decode(SERVICE_ACCOUNT_B64).decode("utf-8")
    creds_info = json.loads(decoded)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    gc = gspread.authorize(creds)

    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(RAW_SHEET_NAME)

    values = ws.get_all_values()

    if not values or len(values) < 2:
        return pd.DataFrame()

    headers = [h.strip() for h in values[0]]
    rows = values[1:]

    df = pd.DataFrame(rows, columns=headers)
    df.columns = [c.strip() for c in df.columns]

    return df

# =====================================================
# PREP DATA
# =====================================================

def prepare_data(df):
    df = df.copy()

    required_cols = ["Timestamp", "Clients", "Purchased", "Inactive", "Clean Cost"]
    missing = [c for c in required_cols if c not in df.columns]

    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    df["Clients"] = normalize_name(df["Clients"])
    df["Client_key"] = df["Clients"].str.lower()

    df["Purchased_bool"] = to_bool(df["Purchased"])
    df["Inactive_bool"] = to_bool(df["Inactive"])

    df["Timestamp_dt"] = pd.to_datetime(df["Timestamp"], errors="coerce")
    df["Amount"] = to_money(df["Clean Cost"])

    # Only Active Clients
    df = df[df["Inactive_bool"] == False].copy()

    return df

# =====================================================
# RESET MODEL SUMMARY LOGIC
# =====================================================

def build_summary(df):

    today = pd.Timestamp.today().normalize()
    summary_rows = []

    for client_key, group in df.groupby("Client_key"):

        client_name = group["Clients"].iloc[0]
        purchases = group[group["Purchased_bool"] == True]
        pending = group[group["Purchased_bool"] == False]

        if purchases.empty:
            last_purchase = pd.NaT
            reset_date = pd.NaT
            purchased_cycle = 0.0
            remaining = BUDGET
        else:
            last_purchase = purchases["Timestamp_dt"].max()
            reset_date = last_purchase + relativedelta(months=6)

            if today >= reset_date:
                purchased_cycle = 0.0
                remaining = BUDGET
            else:
                cycle_start = last_purchase - relativedelta(months=6)
                purchases_in_cycle = purchases[purchases["Timestamp_dt"] >= cycle_start]
                purchased_cycle = purchases_in_cycle["Amount"].sum()
                remaining = max(BUDGET - purchased_cycle, 0.0)

        pending_total = pending["Amount"].sum()

        # ===========================
        # ACTION STATUS LOGIC
        # ===========================
        if pending_total > 0:
            if pending_total > remaining:
                action_status = "Over Budget — Pending"
            else:
                action_status = "Place Order"
        else:
            if remaining == BUDGET:
                action_status = "Eligible"
            elif remaining == 0:
                action_status = "Not Eligible — Wait 6 Months"
            else:
                action_status = "Purchased"

        summary_rows.append({
            "Client": client_name,
            "Purchased Total (Current Cycle)": purchased_cycle,
            "Pending Total": pending_total,
            "Remaining Balance": remaining,
            "Action Status": action_status,
            "Last Purchase Date": last_purchase,
            "Next Reset Date": reset_date
        })

    return pd.DataFrame(summary_rows).sort_values("Client")

# =====================================================
# RUN APP
# =====================================================

try:
    df_raw = load_data()

    if df_raw.empty:
        st.warning("No data found in sheet.")
        st.stop()

    df = prepare_data(df_raw)
    summary = build_summary(df)

except Exception as e:
    st.error("Failed to load data.")
    st.exception(e)
    st.stop()

# =====================================================
# KPI LOGIC (STRICT BUSINESS RULES)
# =====================================================

# Total Purchased = ALL Purchased TRUE rows (active only)
total_purchased = df[df["Purchased_bool"] == True]["Amount"].sum()

# Total Pending = ALL Purchased FALSE rows (active only)
total_pending = df[df["Purchased_bool"] == False]["Amount"].sum()

# Clients Not Eligible
clients_not_eligible = summary[summary["Action Status"] == "Not Eligible — Wait 6 Months"].shape[0]

# =====================================================
# SIDEBAR FILTER
# =====================================================

st.sidebar.header("Filters")

statuses = summary["Action Status"].unique().tolist()

selected_status = st.sidebar.multiselect(
    "Action Status",
    statuses,
    default=statuses
)

filtered = summary[summary["Action Status"].isin(selected_status)]

selected_client = st.sidebar.selectbox(
    "Client",
    ["(All)"] + sorted(filtered["Client"].unique())
)

if selected_client != "(All)":
    filtered = filtered[filtered["Client"] == selected_client]

if st.sidebar.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()

# =====================================================
# KPI DISPLAY
# =====================================================

col1, col2, col3, col4 = st.columns(4)

col1.metric("Total Purchased", f"${total_purchased:,.2f}")
col2.metric("Total Pending", f"${total_pending:,.2f}")
col3.metric("Clients Not Eligible", clients_not_eligible)
col4.metric("Active Rows Loaded", len(df))

# =====================================================
# DATA HEALTH CHECK
# =====================================================

with st.expander("🔎 Data Reconciliation"):
    st.write({
        "Total Raw Rows Pulled": len(df_raw),
        "Active Rows Used": len(df),
        "Purchased Rows": int(df["Purchased_bool"].sum()),
        "Pending Rows": int((~df["Purchased_bool"]).sum()),
        "Total Purchased (Raw)": f"${total_purchased:,.2f}",
        "Total Pending (Raw)": f"${total_pending:,.2f}",
    })

# =====================================================
# FORMAT TABLE
# =====================================================

display = filtered.copy()

display["Purchased Total (Current Cycle)"] = display["Purchased Total (Current Cycle)"].map("${:,.2f}".format)
display["Pending Total"] = display["Pending Total"].map("${:,.2f}".format)
display["Remaining Balance"] = display["Remaining Balance"].map("${:,.2f}".format)

display["Last Purchase Date"] = pd.to_datetime(display["Last Purchase Date"]).dt.strftime("%Y-%m-%d")
display["Next Reset Date"] = pd.to_datetime(display["Next Reset Date"]).dt.strftime("%Y-%m-%d")

# =====================================================
# DISPLAY TABLE
# =====================================================

st.subheader("Client Overview")

st.dataframe(
    display[
        [
            "Client",
            "Purchased Total (Current Cycle)",
            "Pending Total",
            "Remaining Balance",
            "Action Status",
            "Last Purchase Date",
            "Next Reset Date"
        ]
    ],
    use_container_width=True,
    hide_index=True
)
