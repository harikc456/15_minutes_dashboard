from kiteconnect import KiteConnect


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


def place_buy_order(kite: KiteConnect, symbol, buy_price, qty):
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
            price=buy_price,
            validity=kite.VALIDITY_DAY,
        )
        print(f"{symbol} | BUY placed  @ {buy_price} | ID: {buy_id}")

    except Exception as e:
        print(f"Failed for {symbol}: {e}")
        raise e


def place_sell_order(kite: KiteConnect, symbol, sell_price, qty):

    try:
        # SELL order (MIS + Limit) – placed immediately
        sell_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=sell_price,
            validity=kite.VALIDITY_DAY,
        )
        print(f"{symbol} | SELL placed @ {sell_price} | ID: {sell_id}")

    except Exception as e:
        print(f"Failed for {symbol}: {e}")
        raise e


def calculate_quantity_and_finalize(
    kite_client, selected_data, multiplier, capital, strategy
):
    """
    Calculates final prices, quantities, and prepares the data for review.

    UPDATED: Buy/Sell prices rounded to 1 decimal place (multiple of 0.1).
    UPDATED: Equal distribution quantity based on Open Price.
    """
    finalized_data = []

    # 1. Fetch OHLC data for all selected stocks
    symbols = [row["symbol"] for row in selected_data]
    ohlc_map = {}
    for symbol in symbols:
        _, open_price = get_ohlc_data(kite_client, symbol)
        ohlc_map[symbol] = open_price

    # 2. Calculate final order details (Buy Price, Sell Price, Delta)
    for row in selected_data:
        symbol = row["symbol"]
        open_price = ohlc_map.get(symbol, 0.0)

        if open_price == 0.0:
            st.warning(
                f"Skipping {symbol}: Could not fetch Open Price. Ensure the market is open or data is available."
            )
            continue

        metric_base_val = float(row.get("true_range", 0))
        delta = metric_base_val * multiplier

        # Calculate raw Buy and Sell prices
        calc_buy = open_price - delta
        calc_sell = open_price + delta

        # Apply rounding to 1 decimal place (multiple of 0.1) as requested
        row["open_price"] = float(f"{open_price:.2f}")
        row["buy_price"] = round(calc_buy, 1)  # Rounded to 0.1 precision
        row["sell_price"] = round(calc_sell, 1)  # Rounded to 0.1 precision
        finalized_data.append(row)

    if not finalized_data:
        st.error("No stocks could be processed after fetching market data.")
        return []

    # 3. Calculate Quantity based on Strategy
    num_stocks = len(finalized_data)

    for row in finalized_data:
        if strategy == "One each":
            row["quantity"] = 1

        elif strategy == "Equal distribution":
            if num_stocks > 0:
                # Calculate budget per stock
                budget_per_stock = capital / num_stocks

                # Use Open Price as the basis for quantity calculation (Requested change)
                price_proxy = row["open_price"]

                if price_proxy > 0:
                    quantity = math.floor(budget_per_stock / price_proxy)
                    # Ensure minimum quantity of 1 if budget allows
                    row["quantity"] = max(1, quantity)
                else:
                    row["quantity"] = 1  # Fallback

    return finalized_data
