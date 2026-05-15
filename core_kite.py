"""
Kite Connect helper — GTT placement and auth utilities.
"""
import logging
import os

logger = logging.getLogger(__name__)


def get_kite():
    """Return an authenticated KiteConnect instance, or None if not configured."""
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        logger.warning("[kite] kiteconnect not installed — run: pip install kiteconnect")
        return None

    api_key = os.getenv("KITE_API_KEY", "").strip()
    token   = os.getenv("KITE_ACCESS_TOKEN", "").strip()
    if not api_key or not token or api_key == "your_api_key_here":
        return None

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(token)
    return kite


def place_gtt(symbol: str, qty: int, last_price: float, sl: float, target: float):
    """
    Place a two-leg OCO GTT on Kite (SL + T2 target).
    Returns trigger_id (int) on success, None on failure or if Kite not configured.
    """
    kite = get_kite()
    if not kite:
        return None
    try:
        result = kite.place_gtt(
            trigger_type=kite.GTT_TYPE_OCO,
            tradingsymbol=symbol,
            exchange="NSE",
            trigger_values=[round(sl, 2), round(target, 2)],
            last_price=last_price,
            orders=[
                {
                    "exchange":         "NSE",
                    "tradingsymbol":    symbol,
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity":         int(qty),
                    "order_type":       kite.ORDER_TYPE_LIMIT,
                    "product":          kite.PRODUCT_CNC,
                    "price":            round(sl, 2),
                },
                {
                    "exchange":         "NSE",
                    "tradingsymbol":    symbol,
                    "transaction_type": kite.TRANSACTION_TYPE_SELL,
                    "quantity":         int(qty),
                    "order_type":       kite.ORDER_TYPE_LIMIT,
                    "product":          kite.PRODUCT_CNC,
                    "price":            round(target, 2),
                },
            ],
        )
        trigger_id = result.get("trigger_id")
        logger.info("[kite] GTT placed for %s — trigger_id %s", symbol, trigger_id)
        return trigger_id
    except Exception as exc:
        logger.warning("[kite] GTT failed for %s: %s", symbol, exc)
        return None
