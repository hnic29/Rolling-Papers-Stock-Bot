from enum import Enum

from pydantic import BaseModel


class Signal(str, Enum):
    buy = "buy"
    sell = "sell"
    hold = "hold"


class BotStatus(BaseModel):
    running: bool
    symbol: str
    paper: bool
    last_signal: Signal = Signal.hold
    last_message: str = "Idle"
    trades_today: int = 0
    realized_pnl_today: float = 0.0


class TradeRequest(BaseModel):
    symbol: str
    qty: int
    side: Signal
    estimated_price: float | None = None


class Candle(BaseModel):
    open: float
    high: float
    low: float
    close: float
    volume: int


class StockCandidate(BaseModel):
    symbol: str
    price: float
    percent_change: float
    relative_volume: float
    total_volume: int
    float_shares: int | None = None
    has_news: bool = False
    sector: str | None = None
    is_leading_gainer: bool = False


class LevelTwoSnapshot(BaseModel):
    largest_ask_size: int = 0
    hidden_seller_detected: bool = False
    red_tape_burst: bool = False
    buying_slowing: bool = False


class PullbackSetup(BaseModel):
    candidate: StockCandidate
    candles: list[Candle]
    ema9: float
    high_of_day: float
    pullback_low: float
    proposed_entry: float
    proposed_stop: float
    level_two: LevelTwoSnapshot | None = None


class StrategyDecision(BaseModel):
    signal: Signal
    confidence: float
    reasons: list[str]
    risk_per_share: float | None = None
    first_target: float | None = None


class ScannerRequest(BaseModel):
    symbols: list[str]


class AutoScannerRequest(BaseModel):
    limit: int = 25
    max_symbols: int = 250


class ScannerResult(BaseModel):
    symbol: str
    price: float
    percent_change: float
    total_volume: int
    relative_volume: float | None = None
    bid_price: float | None = None
    ask_price: float | None = None
    float_shares: int | None = None
    sector: str | None = None
    has_news: bool = False
    news_headline: str | None = None
    news_url: str | None = None
    score: int
    signal: Signal
    reasons: list[str]


class ScannerResponse(BaseModel):
    results: list[ScannerResult]
