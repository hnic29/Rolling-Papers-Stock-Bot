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
    # Extended-hours (premarket/after-hours) order: Alpaca requires a LIMIT order with
    # no bracket legs, using estimated_price as the limit price - a market order or a
    # bracket (stop/target) is rejected outright outside 9:30-16:00 ET.
    extended_hours: bool = False


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
    news_headline: str | None = None
    # Set by app.services.catalyst.classify_catalyst - "none" when has_news is False,
    # "other" for a headline that doesn't match a known pattern, or a specific category
    # (contract, fda_clinical, offering_dilution, ...). news_sentiment is "negative" for
    # dilution-risk catalysts (proposed offering, reverse split, ...), "positive" for a
    # recognized constructive catalyst, "neutral" otherwise.
    news_category: str = "none"
    news_sentiment: str = "neutral"
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
    macd_signal: float = 0.0
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
    news_category: str = "none"
    news_sentiment: str = "neutral"
    score: int
    signal: Signal
    reasons: list[str]


class ScannerResponse(BaseModel):
    results: list[ScannerResult]
    # How many shortlisted symbols got the full evaluation (universe + top-gainers +
    # sweep hits), which `results` alone can't tell you - scan_universe truncates
    # results to the top N.
    scanned_count: int = 0
    # How many whole-market symbols the sweep screened this cycle (0 = sweep
    # unavailable/failed, e.g. no asset list yet).
    swept_count: int = 0


class AppSettingsResponse(BaseModel):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    fmp_api_key: str = ""
    allow_live_trading: bool = False
    ntfy_topic: str = ""


class AppSettingsUpdate(BaseModel):
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    fmp_api_key: str = ""
    allow_live_trading: bool = False
    # Must be explicitly true on any save that arms live trading (alpaca_paper off AND
    # allow_live_trading on) - deliberate server-side friction so neither two mis-clicked
    # checkboxes nor a bare API call can put real money in play silently.
    confirm_live_trading: bool = False
    ntfy_topic: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class BootstrapRequest(BaseModel):
    """Creates the first (admin) account. Only accepted while zero users exist -
    see main.bootstrap."""

    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UserPublic(BaseModel):
    id: int
    username: str
    is_admin: bool


class UserListItem(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: str


class CreateUserRequest(BaseModel):
    """Admin-provisioned account creation - see main.create_user_route. Not a public
    sign-up endpoint; only an existing admin can call it."""

    username: str
    password: str
    is_admin: bool = False


class ResetPasswordRequest(BaseModel):
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
    day: date
    # None/empty = use the real live universe (data/stock_universe.txt) - this is what
    # makes it "run THIS strategy" rather than a hypothetical one-off symbol test.
    symbols: list[str] | None = None
    starting_capital: float = 10000.0
    position_value: float = 1000.0
