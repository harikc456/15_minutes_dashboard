from kiteconnect import KiteConnect
    
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
            validity=kite.VALIDITY_DAY
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
            validity=kite.VALIDITY_DAY
        )
        print(f"{symbol} | SELL placed @ {sell_price} | ID: {sell_id}")

    except Exception as e:
        print(f"Failed for {symbol}: {e}")
        raise e
