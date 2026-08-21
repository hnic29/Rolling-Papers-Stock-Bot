from datetime import date
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
    daily_pnl: float = 0.0
    auto_trading_enabled: bool = False
    last_automation_run_at: str | None = None
    peak_daily_pnl: float = 0.0
    consecutive_losses: int = 0
    walked_away_for_day: bool = False
    walk_away_reason: str | None = None


class TradeRequest(BaseModel):
    symbol: str
    qty: float
    side: Signal
    estimated_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None


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
    macd: float
    vwap: float
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


class AppSettingsResponse(BaseModel):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    fmp_api_key: str = ""
    allow_live_trading: bool = False
    dashboard_username: str = ""


class AppSettingsUpdate(BaseModel):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    fmp_api_key: str = ""
    allow_live_trading: bool = False


class DashboardAuthUpdate(BaseModel):
    # Blank current_password is only accepted when no dashboard password is
    # configured yet - see the check in main.update_dashboard_auth.
    current_password: str = ""
    new_username: str
    new_password: str


class BankrollTransaction(BaseModel):
    id: int
    created_at: str
    kind: str
    amount: float
    note: str | None = None


class BankrollStatus(BaseModel):
    bankroll_balance: float
    deployed_capital: float
    available_to_trade: float
    realized_pnl: float
    savings_balance: float | None = None
    savings_unavailable_reason: str | None = None
    transactions: list[BankrollTransaction] = []


class BankrollWithdrawRequest(BaseModel):
    amount: float
    note: str | None = None


class BankrollReturnRequest(BaseModel):
    amount: float
    note: str | None = None


class BacktestRequest(BaseModel):
    symbol: str
    start: date
    end: date
    starting_capital: float = 10000.0
    position_value: float = 1000.0
