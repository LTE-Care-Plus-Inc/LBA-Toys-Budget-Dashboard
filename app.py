import json
import base64
import numpy as np
import pandas as pd
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
    st.error("Secrets configuration is missing or malformed.")
    st.stop()

# =====================================================
# LOAD GOOGLE SHEET
# =====================================================
@st.cache_data(ttl=CACHE_TTL)
def load_data():

    decoded_bytes = base64.b64decode(SERVICE_ACCOUNT_B64)
    decoded_str = decoded_bytes.decode("utf-8")
    service_account_info = json.loads(decoded_str)

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    creds = Credentials.from_service_account_info(
        service_account_info,
        scopes=scopes,
    )

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    ws = sh.worksheet(RAW_SHEET_NAME)

    records = ws.get_all_records()
    df = pd.DataFrame(records)
    df.columns = [c.strip() for c in df.columns]

    return df

# =====================================================
# BUSINESS LOGIC (RESET MODEL)
# =====================================================
def build_summary(df):

    df["Client"] = df["Clients"].astype(str).str.strip()
    df["Client_key"] = df["Client"].str.lower()

    df["Purchased_bool"] = df["Purchased"].astype(str).str.strip().str.lower().isin(["true", "yes", "1"])
    df["Inactive_bool"] = df["Inactive"].astype(str).str.strip().str.lower().isin(["true", "yes", "1"])

    df["Timestamp_dt"] = pd.to_datetime(df["Timestamp"], errors="coerce")

    # Clean cost safely
    df["Amount"] = pd.to_numeric(
        df["Clean Cost"]
        .astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0.0)

    # Remove inactive clients
    df = df[df["Inactive_bool"] == False]

    summary_rows = []

    today = pd.Timestamp.today().normalize()

    for client_key, group in df.groupby("Client_key"):

        client_name = group["Client"].iloc[0]

        purchases = group[group["Purchased_bool"] == True]
        pending = group[group["Purchased_bool"] == False]

        if purchases.empty:
            # Never purchased
            purchased_total = 0.0
            balance = BUDGET
            last_purchase_date = None
        else:
            last_purchase_date = purchases["Timestamp_dt"].max()

            reset_date = last_purchase_date + relativedelta(months=6)

            if today >= reset_date:
                # Budget resets
                purchased_total = 0.0
                balance = BUDGET
            else:
                # Still within active 6-month window
                cycle_start = last_purchase_date
                active_purchases = purchases[purchases["Timestamp_dt"] >= cycle_start]
                purchased_total = active_purchases["Amount"].sum()
                balance = max(BUDGET - purchased_total, 0.0)

        pending_total = pending["Amount"].sum()

        # =============================
        # ACTION STATUS LOGIC
        # =============================
        if pending_total > 0:

            if pending_total > balance:
                action_status = "Over Budget — Pending"
            else:
                action_status = "Place Order"

        else:
            if balance == BUDGET:
                action_status = "Eligible"
            elif balance == 0:
                action_status = "Not Eligible — Wait 6 Months"
            else:
                action_status = "Purchased"

        summary_rows.append({
            "Client": client_name,
            "Purchased Total": purchased_total,
            "Pending Total": pending_total,
            "Remaining Balance": balance,
            "Action Status": action_status,
            "Last Purchase Date": last_purchase_date
        })

    summary = pd.DataFrame(summary_rows)
    summary = summary.sort_values("Client")

    return summary

# =====================================================
# RUN APP
# =====================================================
try:
    df_raw = load_data()
except Exception as e:
    st.error(f"Failed to load Google Sheet: {e}")
    st.stop()

summary = build_summary(df_raw)

# =====================================================
# SIDEBAR FILTER
# =====================================================
st.sidebar.header("Filters")

statuses = [
    "Eligible",
    "Purchased",
    "Place Order",
    "Over Budget — Pending",
    "Not Eligible — Wait 6 Months"
]

selected_status = st.sidebar.multiselect(
    "Action Status",
    statuses,
    default=statuses
)

filtered = summary[summary["Action Status"].isin(selected_status)]

selected_client = st.sidebar.selectbox(
    "Client",
    ["(All)"] + sorted(filtered["Client"].unique().tolist())
)

if selected_client != "(All)":
    filtered = filtered[filtered["Client"] == selected_client]

if st.sidebar.button("Refresh"):
    st.cache_data.clear()
    st.rerun()

# =====================================================
# KPI CARDS
# =====================================================
c1, c2, c3 = st.columns(3)

c1.metric("Total Purchased", f"${filtered['Purchased Total'].sum():,.2f}")
c2.metric("Total Pending", f"${filtered['Pending Total'].sum():,.2f}")
c3.metric("Clients Not Eligible", int((filtered["Action Status"] == "Not Eligible — Wait 6 Months").sum()))

# =====================================================
# FORMAT MONEY
# =====================================================
display = filtered.copy()
display["Purchased Total"] = display["Purchased Total"].map("${:,.2f}".format)
display["Pending Total"] = display["Pending Total"].map("${:,.2f}".format)
display["Remaining Balance"] = display["Remaining Balance"].map("${:,.2f}".format)

# =====================================================
# DISPLAY TABLE
# =====================================================
st.subheader("Client Overview")

st.dataframe(
    display[
        [
            "Client",
            "Purchased Total",
            "Pending Total",
            "Remaining Balance",
            "Action Status",
            "Last Purchase Date"
        ]
    ],
    use_container_width=True,
    hide_index=True
)
