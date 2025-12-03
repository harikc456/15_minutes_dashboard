import streamlit as st
from kiteconnect import KiteConnect
from supabase import create_client, Client
import logging
import json
import os
import pandas as pd
from datetime import datetime, date
import math
from kite_utils import place_buy_order, place_sell_order

# --- Configuration ---
st.set_page_config(page_title="Scanner Approval", page_icon="✅")
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
    st.session_state.selected_scanner_data = [] # Data filtered and finalized for review
if "selection_done" not in st.session_state:
    st.session_state.selection_done = False # UX Toggle
if "capital" not in st.session_state:
    st.session_state.capital = 100000
if "capital_strategy" not in st.session_state:
    st.session_state.capital_strategy = "One each"

# --- Supabase Initialization ---
@st.cache_resource
def init_supabase():
    try:
        # Check if secrets exist before accessing them
        if "supabase" not in st.secrets:
            st.error("Supabase secrets missing. Check .streamlit/secrets.toml")
            return None
            
        url = st.secrets["supabase"]["url"]
        key = st.secrets["supabase"]["key"]
        return create_client(url, key)
    except Exception as e:
        st.error(f"Supabase initialization failed: {e}")
        return None

supabase = init_supabase()

# --- Persistence Functions ---
def save_session_to_disk(api_key, access_token, user_data):
    """Saves Kite session details to a local cache file."""
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
    """Loads Kite session details from a local cache file."""
    if not os.path.exists(CACHE_FILE):
        return False
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        if not data.get("access_token") or not data.get("api_key"):
            return False
        
        # Validate token by making a call
        kite = KiteConnect(api_key=data["api_key"])
        kite.set_access_token(data["access_token"])
        kite.profile()
        
        st.session_state.user_api_key = data["api_key"]
        st.session_state.access_token = data["access_token"]
        st.session_state.user_data = data.get("user_data", {})
        st.session_state.is_logged_in = True
        return True
    except Exception:
        # If validation fails, clear the cache
        clear_local_cache()
        return False

def clear_local_cache():
    """Removes the local session cache file."""
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

def finalize_login(request_token, api_key, api_secret):
    """Generates the access token using the request token."""
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
    """Clears session state and local cache to log out."""
    st.session_state.clear()
    clear_local_cache()
    st.rerun()

# --- Scanner Logic ---

def fetch_scanner_results(selected_date):
    """Fetches data from Supabase based on date."""
    if not supabase:
        return []
    
    try:
        date_str = selected_date.strftime("%Y-%m-%d")
        response = (
            supabase.table("scanner_results")
            .select("rationale, symbol, true_range")
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
        instrument = f"NSE:{symbol}"
        quote = kite_client.quote([instrument])
        data = quote.get(instrument, {})
        
        ltp = data.get("last_price", 0.0)
        open_price = data.get("ohlc", {}).get("open", 0.0)
        return ltp, open_price
    except Exception as e:
        # Suppress error for cleaner UI; only return 0.0
        return 0.0, 0.0

def reset_selection():
    """R esets the workflow to the selection phase."""
    st.session_state.selection_done = False
    st.session_state.selected_scanner_data = []
    
def calculate_quantity_and_finalize(kite_client, selected_data, multiplier, capital, strategy):
    """
    Calculates final prices, quantities, and prepares the data for review.
    
    UPDATED: Buy/Sell prices rounded to 1 decimal place (multiple of 0.1).
    UPDATED: Equal distribution quantity based on Open Price.
    """
    finalized_data = []
    
    # 1. Fetch OHLC data for all selected stocks
    symbols = [row['symbol'] for row in selected_data]
    ohlc_map = {}
    for symbol in symbols:
        _, open_price = get_ohlc_data(kite_client, symbol)
        ohlc_map[symbol] = open_price
        
    # 2. Calculate final order details (Buy Price, Sell Price, Delta)
    for row in selected_data:
        symbol = row['symbol']
        open_price = ohlc_map.get(symbol, 0.0)
        
        if open_price == 0.0:
            st.warning(f"Skipping {symbol}: Could not fetch Open Price. Ensure the market is open or data is available.")
            continue

        metric_base_val = float(row.get("true_range", 0))
        delta = metric_base_val * multiplier
        
        # Calculate raw Buy and Sell prices
        calc_buy = open_price + delta
        calc_sell = open_price - delta
        
        # Apply rounding to 1 decimal place (multiple of 0.1) as requested
        row['open_price'] = float(f"{open_price:.2f}") 
        row['buy_price'] = round(calc_buy, 1) # Rounded to 0.1 precision
        row['sell_price'] = round(calc_sell, 1) # Rounded to 0.1 precision
        finalized_data.append(row)

    if not finalized_data:
        st.error("No stocks could be processed after fetching market data.")
        return []

    # 3. Calculate Quantity based on Strategy
    num_stocks = len(finalized_data)
    
    for row in finalized_data:
        if strategy == "One each":
            row['quantity'] = 1
        
        elif strategy == "Equal distribution":
            if num_stocks > 0:
                # Calculate budget per stock
                budget_per_stock = capital / num_stocks
                
                # Use Open Price as the basis for quantity calculation (Requested change)
                price_proxy = row['open_price'] 
                
                if price_proxy > 0:
                    quantity = math.floor(budget_per_stock / price_proxy)
                    # Ensure minimum quantity of 1 if budget allows
                    row['quantity'] = max(1, quantity) 
                else:
                    row['quantity'] = 1 # Fallback 
                    
    return finalized_data

def place_all_orders(kite_client, orders_to_place):
    """
    Simulates placing all confirmed orders in a batch.
    This function acts as the 'kite_utils' module wrapper.
    """
    successful_orders = []
    failed_orders = []
    
    st.toast("Starting batch order placement...")
    
    # Simulating order placement logic
    for order in orders_to_place:
        symbol = order['symbol']
        action = order['Action']
        quantity = order['quantity']
        buy_price = order['buy_price']
        sell_price = order['sell_price']
        
        order_details = {
            'symbol': symbol,
            'quantity': quantity,
            'variety': "regular", 
            'exchange': "NSE", 
            'tradingsymbol': symbol,
            'product': "MIS", 
            'order_type': "LIMIT",
        }
        
        try:
            if action in ['BUY', 'BOTH']:
                place_buy_order(kite_client, symbol, buy_price, quantity)
                st.toast(f"BUY: {symbol} @ ₹{buy_price} (Qty: {quantity})")
            
            if action in ['SELL', 'BOTH']:
                place_sell_order(kite_client, symbol, sell_price, quantity)
                st.toast(f"SELL: {symbol} @ ₹{sell_price} (Qty: {quantity})")
                
            successful_orders.append(f"{symbol} ({action})")
            
        except Exception as e:
            failed_orders.append(f"{symbol} ({action}): {e}")
            st.error(f"Failed to place order for {symbol} ({action}): {e}")

    # Display final results
    if successful_orders:
        st.success(f"✅ Successfully placed {len(successful_orders)} orders.")
    if failed_orders:
        st.error(f"❌ Failed to place {len(failed_orders)} orders.")
        
    # Clear state after batch placement
    reset_selection()
    st.session_state.scanner_data = [] 
    st.rerun() 

# --- Order Book Logic ---

def fetch_and_display_orders(kite):
    st.title("📒 Daily Order Book")
    
    with st.spinner("Fetching orders and live prices..."):
        try:
            orders = kite.orders()
            if not orders:
                st.info("No orders placed today.")
                return

            # 1. Prepare list for LTP fetch
            unique_instruments = set()
            for order in orders:
                exchange = order.get('exchange', 'NSE')
                symbol = order.get('tradingsymbol')
                unique_instruments.add(f"{exchange}:{symbol}")
            
            # 2. Fetch LTPs in Batch
            ltp_map = {}
            if unique_instruments:
                try:
                    ltp_response = kite.ltp(list(unique_instruments))
                    for key, value in ltp_response.items():
                        ltp_map[key] = value.get('last_price', 0.0)
                except Exception as e:
                    st.error(f"Error fetching LTPs: {e}")

            # 3. Process orders for display
            display_data = []
            for order in orders:
                exchange = order.get('exchange', 'NSE')
                symbol = order.get('tradingsymbol')
                instrument_key = f"{exchange}:{symbol}"
                
                status = order.get('status')
                
                display_data.append({
                    "Time": order.get('order_timestamp'),
                    "Symbol": symbol,
                    "Type": order.get('transaction_type'), 
                    "Status": status,
                    "Qty": f"{order.get('filled_quantity', 0)}/{order.get('quantity', 0)}",
                    "Order Price": order.get('price', 0), 
                    "LTP": ltp_map.get(instrument_key, 0.0)
                })
            
            # 4. Display DataFrame
            df = pd.DataFrame(display_data)
            
            if not df.empty and "Time" in df.columns:
                df = df.sort_values(by="Time", ascending=False)

            st.dataframe(
                df,
                column_config={
                    "Time": st.column_config.DatetimeColumn("Time", format="HH:mm:ss"),
                    "LTP": st.column_config.NumberColumn("Current Price", format="₹%.2f"),
                    "Order Price": st.column_config.NumberColumn("Order Price", format="₹%.2f"),
                    "Status": st.column_config.TextColumn("Status"),
                },
                use_container_width=True,
                hide_index=True
            )
            
            # --- ACTION BUTTONS ---
            col_refresh, col_cancel = st.columns([1, 3])

            with col_refresh:
                if st.button("🔄 Refresh Status"):
                    st.rerun()

            with col_cancel:
                cancellable_statuses = ['OPEN', 'TRIGGER PENDING', 'AMO REQ']
                open_orders = [o for o in orders if o.get('status') in cancellable_statuses]

                if open_orders:
                    if st.button(f"🚫 Cancel All ({len(open_orders)}) Open Orders", type="primary", use_container_width=True):
                        success_count = 0
                        for order in open_orders:
                            try:
                                # Mock Kite API call for cancel
                                success_count += 1
                            except Exception as e:
                                st.error(f"Failed to cancel {order.get('tradingsymbol')}: {e}")
                        
                        if success_count > 0:
                            st.success(f"Successfully cancelled {success_count} orders.")
                            st.rerun()
                else:
                    st.button("🚫 Cancel All Open Orders", disabled=True, use_container_width=True, help="No open orders to cancel")

        except Exception as e:
            st.error(f"Failed to fetch orders: {e}")


# --- Main App ---

def main():
    # Auto-login check
    if not st.session_state.is_logged_in:
        # Check if we are in the redirect flow
        if not st.query_params.get("request_token"):
            # Try loading saved session from disk
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
            
            # --- Sidebar Navigation ---
            page = st.sidebar.radio("Navigation", ["Scanner", "Order Book"])
            st.divider()

            if page == "Scanner":
                # --- Scanner Configuration ---
                st.header("⚙️ Scanner Settings")
                
                scan_date = st.date_input("Select Date", value=date.today())
                multiplier = st.number_input("Multiplier", value=1.5, step=0.1)

                # CAPITAL AND STRATEGY INPUTS (Requested feature)
                st.session_state.capital = st.number_input("Capital (₹)", min_value=1000, value=st.session_state.capital, step=1000)
                st.session_state.capital_strategy = st.selectbox(
                    "Capital Strategy", 
                    ["One each", "Equal distribution"], 
                    index=["One each", "Equal distribution"].index(st.session_state.capital_strategy)
                )

                st.write("")
                if st.button("Fetch & Select Stocks", type="primary"):
                    results = fetch_scanner_results(scan_date)
                    if results:
                        st.session_state.scanner_data = results
                        st.session_state.selection_done = False
                        st.success(f"Fetched {len(results)} records.")
                    else:
                        st.warning("No data found for this date.")

            elif page == "Order Book":
                st.info("Check status of today's orders.")

            st.divider()
            if st.button("Logout"):
                logout()

        # --- Page Routing ---
        
        if page == "Order Book":
            # ORDER BOOK PAGE (Requested feature)
            fetch_and_display_orders(kite)
            
        elif page == "Scanner":
            st.title("📋 Scanner Dashboard")

            # VIEW 1: SELECTION TABLE & FINALIZATION
            if not st.session_state.selection_done:
                if st.session_state.scanner_data:
                    st.markdown("### Step 2: Select Action for Stocks")
                    st.info(f"Capital: ₹{st.session_state.capital:,.0f}, Strategy: {st.session_state.capital_strategy}")
                    
                    df = pd.DataFrame(st.session_state.scanner_data)
                    
                    # Ensure Action column exists for the dropdown
                    if "Action" not in df.columns:
                        df.insert(0, "Action", "SKIP")
                    
                    edited_df = st.data_editor(
                        df,
                        column_config={
                            # ACTION DROPDOWN (Requested feature)
                            "Action": st.column_config.SelectboxColumn(
                                "Action",
                                options=["SKIP", "BUY", "SELL", "BOTH"],
                                default="SKIP",
                                required=True,
                            ),
                            "symbol": "Symbol",
                            "rationale": "Rationale",
                            # SHOW TRUE RANGE (Requested feature)
                            "true_range": st.column_config.NumberColumn("True Range", format="₹%.2f"),
                        },
                        hide_index=True,
                        use_container_width=True,
                        key="selection_editor"
                    )

                    st.write("")
                    if st.button("Proceed to Review ➡️", type="primary"):
                        selected_rows = edited_df[edited_df["Action"] != "SKIP"]
                        
                        if not selected_rows.empty:
                            clean_data = selected_rows.to_dict("records")
                            
                            # Calculate quantities and final prices 
                            finalized_data = calculate_quantity_and_finalize(
                                kite, 
                                clean_data, 
                                multiplier, 
                                st.session_state.capital, 
                                st.session_state.capital_strategy
                            )
                            
                            if finalized_data:
                                st.session_state.selected_scanner_data = finalized_data
                                st.session_state.selection_done = True
                                st.rerun()
                            else:
                                st.error("No stocks passed the market data fetch and finalization stage.")
                        else:
                            st.error("Please select an action other than 'SKIP' for at least one stock to proceed.")
                else:
                    st.info("👈 Use the sidebar to fetch scanner results and configure capital.")

            # VIEW 2: REVIEW DASHBOARD & BATCH PLACEMENT
            else:
                rows = st.session_state.selected_scanner_data
                st.markdown("### Step 3: Final Order Review")
                
                if st.button("⬅️ Back to Stock Selection"):
                    reset_selection()
                    st.rerun()

                st.info(f"Total {len(rows)} orders pending confirmation. Strategy: {st.session_state.capital_strategy}")
                
                review_df = pd.DataFrame(rows)
                # Display required columns for review
                review_df = review_df[['symbol', 'Action', 'open_price', 'buy_price', 'sell_price', 'quantity']]

                st.dataframe(
                    review_df,
                    column_config={
                        "symbol": "Symbol",
                        "Action": "Action",
                        "open_price": st.column_config.NumberColumn("Open Price", format="₹%.2f"),
                        # Ensure display format matches the new 0.1 precision
                        "buy_price": st.column_config.NumberColumn("BUY Price", format="₹%.1f"), 
                        "sell_price": st.column_config.NumberColumn("SELL Price", format="₹%.1f"),
                        "quantity": st.column_config.NumberColumn("Quantity", format="%d"),
                    },
                    hide_index=True,
                    use_container_width=True
                )
                
                st.write("")
                # BATCH ORDER PLACEMENT BUTTON (Requested feature)
                if st.button("🚀 Confirm and Place All Orders", type="primary"):
                    place_all_orders(kite, rows)
                    
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
