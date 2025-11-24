import streamlit as st
from kiteconnect import KiteConnect
from supabase import create_client
import logging
import json
import os
import time
import pandas as pd
from datetime import datetime, date

# --- Configuration ---
st.set_page_config(page_title="15 Minutes Scanner Approval", page_icon="✅")
logging.basicConfig(level=logging.INFO)
CACHE_FILE = "kite_session.json"

# --- Session Initialization ---
if "user_api_key" not in st.session_state:
    st.session_state.user_api_key = ""
if "is_logged_in" not in st.session_state:
    st.session_state.is_logged_in = False
    
# Scanner state
if "scanner_data" not in st.session_state:
    st.session_state.scanner_data = []  # Raw data from DB
if "selected_scanner_data" not in st.session_state:
    st.session_state.selected_scanner_data = [] # Data filtered by user
if "selection_done" not in st.session_state:
    st.session_state.selection_done = False # UX Toggle
if "current_row_index" not in st.session_state:
    st.session_state.current_row_index = 0

# --- Supabase Initialization ---
@st.cache_resource
def init_supabase():
    try:
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as e:
        st.error("Supabase secrets missing. Check .streamlit/secrets.toml")
        return None

supabase = init_supabase()

# --- Persistence Functions ---
def save_session_to_disk(api_key, access_token, user_data):
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "api_key": api_key,
                "access_token": access_token,
                "user_data": user_data,
                "timestamp": str(datetime.now())
            }, f)
    except Exception as e:
        st.warning(f"Could not save session cache: {e}")

def load_session_from_disk():
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        if not data.get("access_token") or not data.get("api_key"):
            return False
        
        # Validate token
        kite = KiteConnect(api_key=data["api_key"])
        kite.set_access_token(data["access_token"])
        kite.profile() # Will raise exception if invalid
        
        st.session_state.user_api_key = data["api_key"]
        st.session_state.access_token = data["access_token"]
        st.session_state.user_data = data.get("user_data", {})
        st.session_state.is_logged_in = True
        return True
    except Exception:
        clear_local_cache()
        return False

def clear_local_cache():
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

def finalize_login(request_token, api_key, api_secret):
    try:
        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        st.session_state.access_token = data["access_token"]
        st.session_state.user_data = data
        st.session_state.user_api_key = api_key
        st.session_state.is_logged_in = True
        save_session_to_disk(api_key, data["access_token"], data)
        return True
    except Exception as e:
        st.error(f"Connection failed: {e}")
        return False

def logout():
    st.session_state.clear()
    clear_local_cache()
    st.rerun()

# --- Scanner Logic ---

def fetch_scanner_results(selected_date):
    """Fetches data from Supabase based on date."""
    if not supabase:
        return []
    
    try:
        # Convert date to string format YYYY-MM-DD
        date_str = selected_date.strftime("%Y-%m-%d")
        
        response = (
            supabase.table("scanner_results")
            .select("rationale, symbol, atr_14, true_range")
            .eq("date", date_str)
            .execute()
        )
        return response.data
    except Exception as e:
        st.error(f"Supabase Query Error: {e}")
        return []

def get_ohlc_data(kite_client, symbol):
    """Fetches LTP and Open price for a given symbol from NSE."""
    try:
        # Assuming NSE exchange. Adjust if your DB stores "NSE:INFY" directly.
        instrument = f"NSE:{symbol}"
        # Fetch full quote to get OHLC data
        quote = kite_client.quote([instrument])
        data = quote.get(instrument, {})
        
        ltp = data.get("last_price", 0.0)
        open_price = data.get("ohlc", {}).get("open", 0.0)
        
        return ltp, open_price
    except Exception as e:
        st.error(f"Could not fetch data for {symbol}: {e}")
        return 0.0, 0.0
    
def place_buy_and_sell(kite: KiteConnect, trade: dict):
    symbol = trade["symbol"]
    qty    = trade["qty"]
    
    try:
        # 1. BUY order (MIS + Limit)
        buy_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=trade["buy_price"],
            validity=kite.VALIDITY_DAY
        )
        print(f"{symbol} | BUY placed  @ {trade['buy_price']} | ID: {buy_id}")

        # 2. SELL order (MIS + Limit) – placed immediately
        sell_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=trade["sell_price"],
            validity=kite.VALIDITY_DAY
        )
        print(f"{symbol} | SELL placed @ {trade['sell_price']} | ID: {sell_id}")

    except Exception as e:
        print(f"Failed for {symbol}: {e}")
        raise e

def handle_approval(action, row_data, final_buy, final_sell, quantity):
    """Handles the approve/reject logic with the edited prices."""
    kite = KiteConnect(api_key=st.session_state.user_api_key)
    kite.set_access_token(st.session_state.access_token)
    
    if action == "APPROVED":
        try:
            place_buy_and_sell(kite, {
                "symbol": row_data["symbol"],
                "qty": quantity,
                "buy_price": final_buy,
                "sell_price": final_sell
            })
            st.toast(f"Approved {row_data['symbol']} @ Buy: {final_buy} / Sell: {final_sell}", duration='long')
            time.sleep(2.0)
            st.session_state.current_row_index += 1
        except Exception as e:
            st.toast(f"Failed to place order for {row_data['symbol']}: {e}", duration='long')
            time.sleep(2.0)
    else:
        st.toast(f"Rejected {row_data['symbol']}")
        st.session_state.current_row_index += 1

# --- Main App ---


def main():
    # Auto-login check
    if not st.session_state.is_logged_in:
        if not st.query_params.get("request_token"):# Scanner state
            if load_session_from_disk():
                st.rerun()

    # ---------------------------------------------------------
    # PART 1: LOGGED IN DASHBOARD
    # ---------------------------------------------------------
    if st.session_state.is_logged_in:
        user = st.session_state.user_data
        
        # Initialize Kite
        kite = KiteConnect(api_key=st.session_state.user_api_key)
        kite.set_access_token(st.session_state.access_token)
        
        with st.sidebar:
            st.success(f"User: {user.get('user_name')}")
            st.divider()
            
            # --- Configuration Sidebar ---
            st.header("1. Settings")
            scan_date = st.date_input("Select Date", value=date.today())
            metric_type = st.selectbox("Select Metric", ["ATR", "True Range"])
            multiplier = st.number_input("Multiplier", value=1.5, step=0.1)
            
            st.divider()
            
            # Button to fetch data (Restarts the flow)
            if st.button("Fetch & Select Stocks", type="primary"):
                results = fetch_scanner_results(scan_date)
                if results:
                    st.session_state.scanner_data = results
                    st.session_state.selection_done = False # Reset to allow selection
                    st.session_state.current_row_index = 0
                    st.success(f"Fetched {len(results)} records.")
                    st.rerun()
                else:
                    st.warning("No data found for this date.")

            if st.button("Logout"):
                logout()

        # --- Main Content Area ---
        st.title("📋 Scanner Dashboard")

        # VIEW 1: SELECTION TABLE (If selection is NOT done yet)
        if not st.session_state.selection_done:
            if st.session_state.scanner_data:
                st.markdown("### Step 2: Select Stocks to Review")
                st.info("Check the boxes for the stocks you want to process in the Approval Dashboard.")
                
                # Convert to DF for Data Editor
                df = pd.DataFrame(st.session_state.scanner_data)
                
                # Add a 'Select' column initialized to False
                if "Select" not in df.columns:
                    df.insert(0, "Select", False)

                # Show Data Editor
                edited_df = st.data_editor(
                    df,
                    column_config={
                        "Select": st.column_config.CheckboxColumn(
                            "Select",
                            help="Select to review this stock",
                            default=False,
                        ),
                        "symbol": "Symbol",
                        "rationale": "Rationale",
                        "atr_14": st.column_config.NumberColumn("ATR 14", format="%.2f"),
                        "true_range": st.column_config.NumberColumn("True Range", format="%.2f"),
                    },
                    hide_index=True,
                    use_container_width=True
                )

                # Proceed Button
                col1, col2 = st.columns([1, 4])
                with col1:
                    if st.button("Proceed ➡️", type="primary"):
                        # Filter rows where 'Select' is True
                        selected_rows = edited_df[edited_df["Select"] == True]
                        
                        if not selected_rows.empty:
                            # Convert back to list of dicts for the logic
                            # Drop the 'Select' column before saving state
                            clean_data = selected_rows.drop(columns=["Select"]).to_dict("records")
                            st.session_state.selected_scanner_data = clean_data
                            st.session_state.selection_done = True
                            st.rerun()
                        else:
                            st.error("Please select at least one stock to proceed.")
            else:
                st.info("👈 Use the sidebar to fetch scanner results.")

        # VIEW 2: APPROVAL DASHBOARD (If selection IS done)
        else:
            rows = st.session_state.selected_scanner_data
            idx = st.session_state.current_row_index
            
            # Back Button
            if st.button("⬅️ Back to Selection List"):
                reset_selection()
                st.rerun()

            if rows and idx < len(rows):
                current_row = rows[idx]
                symbol = current_row['symbol']
                
                # 1. Fetch Live Price (LTP) and Open Price
                ltp, open_price = get_ohlc_data(kite, symbol)
                
                # 2. Calculate Metric Targets
                metric_base_val = 0.0
                if metric_type == "ATR":
                    metric_base_val = float(current_row.get("atr_14", 0))
                else:
                    metric_base_val = float(current_row.get("true_range", 0))
                    
                delta = metric_base_val * multiplier
                calc_buy = open_price + delta
                calc_sell = open_price - delta

                # 3. Display Interface
                st.progress((idx) / len(rows), text=f"Reviewing {idx + 1} of {len(rows)}")

                with st.container(border=True):
                    # Header Row
                    col_head1, col_head2, col_head3 = st.columns([2, 1, 1])
                    with col_head1:
                        st.markdown(f"## {symbol}")
                        st.caption(f"Rationale: {current_row.get('rationale', 'N/A')}")
                    with col_head2:
                        st.metric("LTP", f"₹{ltp}")
                    with col_head3:
                        st.metric("Open Price", f"₹{open_price}")

                    st.divider()

                    # Metrics Display
                    c_m1, c_m2 = st.columns(2)
                    with c_m1:
                        st.info(f"**{metric_type}:** {metric_base_val:.2f}")
                    with c_m2:
                        st.info(f"**Multiplier:** x{multiplier}")

                    st.markdown("### 🎯 Order Details")
                    
                    # Editable Inputs: Quantity | BUY | SELL
                    c_input1, c_input2, c_input3 = st.columns(3)
                    
                    with c_input1:
                        quantity = st.number_input(
                            "Quantity",
                            min_value=1,
                            value=1,
                            step=1,
                            key=f"qty_{idx}"
                        )

                    with c_input2:
                        final_buy_price = st.number_input(
                            "BUY Price (Open + Delta)", 
                            value=float(f"{calc_buy:.2f}"),
                            step=0.05,
                            key=f"buy_{idx}"
                        )
                    
                    with c_input3:
                        final_sell_price = st.number_input(
                            "SELL Price (Open - Delta)", 
                            value=float(f"{calc_sell:.2f}"),
                            step=0.05,
                            key=f"sell_{idx}"
                        )

                # 4. Action Buttons
                st.write("") 
                col_reject, col_approve = st.columns(2)
                
                with col_reject:
                    if st.button("❌ Reject", use_container_width=True):
                        handle_approval("REJECTED", current_row, final_buy_price, final_sell_price, quantity)
                        st.rerun()
                
                with col_approve:
                    if st.button("✅ Approve", type="primary", use_container_width=True):
                        handle_approval("APPROVED", current_row, final_buy_price, final_sell_price, quantity)
                        st.rerun()

            elif rows and idx >= len(rows):
                st.success("🎉 Selected items reviewed!")
                if st.button("Start Over"):
                    reset_selection()
                    st.rerun()

    # ---------------------------------------------------------
    # PART 2: LOGIN FLOW
    # ---------------------------------------------------------
    elif st.query_params.get("request_token"):
        request_token = st.query_params.get("request_token")
        stored_key = st.session_state.get("user_api_key")
        stored_secret = st.session_state.get("user_api_secret")

        if stored_key and stored_secret:
            if finalize_login(request_token, stored_key, stored_secret):
                st.query_params.clear()
                st.rerun()
        else:
            st.warning("Session mismatch. Please confirm credentials.")
            with st.form("finalize_form"):
                re_api_key = st.text_input("Confirm API Key", value=stored_key if stored_key else "")
                re_api_secret = st.text_input("Confirm API Secret", type="password")
                if st.form_submit_button("Complete Login"):
                    if finalize_login(request_token, re_api_key, re_api_secret):
                        st.query_params.clear()
                        st.rerun()

    else:
        st.title("Scanner Login")
        with st.form("init_form"):
            api_key = st.text_input("API Key")
            api_secret = st.text_input("API Secret", type="password")
            if st.form_submit_button("Connect"):
                if api_key and api_secret:
                    st.session_state.user_api_key = api_key
                    st.session_state.user_api_secret = api_secret
                    kite = KiteConnect(api_key=api_key)
                    st.link_button("Login with Zerodha", kite.login_url(), type="primary")


if __name__ == "__main__":
    main()
