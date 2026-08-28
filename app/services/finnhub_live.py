"""Real-time, per-second price ticks for the candlestick chart's optional 'Live'
overlay, via Finnhub's trade websocket (real-time US equity trades are included on
Finnhub's free tier). This is a genuinely separate data path from the Alpaca-fed
historical bars everything else in this app - including the bot's own trading
decisions - reads: nothing here is consumed by TradingBot/MarketScanner, it only
ever feeds the chart while someone is actively watching it.

One dedicated upstream Finnhub connection per viewer (not a shared/multiplexed
pool) - simpler to reason about, and appropriate at this app's scale of a handful
of people watching a chart at once."""

import asyncio
import json
import logging

import websockets
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

FINNHUB_WS_URL = "wss://ws.finnhub.io"


async def stream_trades(client_ws: WebSocket, symbol: str, api_key: str) -> None:
    """Relays Finnhub trade ticks for `symbol` to `client_ws` (already accepted)
    until either side disconnects or the upstream connection fails."""
    symbol = symbol.upper()
    try:
        async with websockets.connect(f"{FINNHUB_WS_URL}?token={api_key}") as upstream:
            await upstream.send(json.dumps({"type": "subscribe", "symbol": symbol}))

            async def pump_upstream() -> None:
                async for raw in upstream:
                    message = json.loads(raw)
                    if message.get("type") != "trade":
                        continue  # ping / subscribe-ack / etc. - nothing to forward
                    for trade in message.get("data", []):
                        if str(trade.get("s", "")).upper() != symbol:
                            continue
                        await client_ws.send_json(
                            {
                                "price": trade["p"],
                                "volume": trade.get("v", 0),
                                "timestamp": trade["t"],  # ms since epoch, Finnhub's own trade time
                            }
                        )

            async def watch_client() -> None:
                # Only exists to notice the browser closing the tab / toggling
                # off - the client itself never sends anything meaningful.
                while True:
                    await client_ws.receive_text()

            pump_task = asyncio.ensure_future(pump_upstream())
            watch_task = asyncio.ensure_future(watch_client())
            try:
                await asyncio.wait({pump_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                pump_task.cancel()
                watch_task.cancel()
            try:
                await upstream.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Finnhub live stream failed for %s", symbol)
        try:
            await client_ws.send_json({"error": "Live data stream failed - check your Finnhub API key in Settings."})
        except Exception:
            pass
