async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = null;
  try {
    data = await response.json();
  } catch {
    // Non-JSON body (e.g. an upstream proxy's error page) - fall through with
    // no parsed data rather than throwing an unhelpful "Unexpected token" error.
  }
  if (!response.ok) throw new Error(data?.detail || `Request failed (${response.status})`);
  return data;
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

// Session-cookie login gate. Shown by default (see the `hidden`-less markup in
// index.html) and only hidden once /api/me confirms a valid session - fail closed
// if the auth check itself errors out, rather than risk flashing the dashboard.
const authGateEl = document.querySelector("#auth-gate");
const bootstrapForm = document.querySelector("#bootstrap-form");
const loginForm = document.querySelector("#login-form");

async function initAuthGate() {
  try {
    const status = await api("/api/auth/status");
    if (status.needs_bootstrap) {
      bootstrapForm.hidden = false;
      return;
    }
  } catch (error) {
    loginForm.hidden = false;
    return;
  }

  try {
    const me = await api("/api/me");
    authGateEl.hidden = true;
    document.querySelector("#session-username").hidden = false;
    document.querySelector("#session-username").textContent = me.username;
    document.querySelector("#logout").hidden = false;
    document.querySelector("#settings-username").textContent = me.username;
    if (me.is_admin) {
      document.querySelector("#users-panel").hidden = false;
      loadUsers();
    }
  } catch (error) {
    loginForm.hidden = false;
  }
}

bootstrapForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const errorEl = document.querySelector("#bootstrap-error");
  errorEl.hidden = true;
  try {
    await api("/api/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    window.location.reload();
  } catch (error) {
    errorEl.textContent = error.message;
    errorEl.hidden = false;
  }
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const errorEl = document.querySelector("#login-error");
  errorEl.hidden = true;
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ username: form.username.value, password: form.password.value }),
    });
    window.location.reload();
  } catch (error) {
    errorEl.textContent = error.message;
    errorEl.hidden = false;
  }
});

document.querySelector("#logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
  } finally {
    window.location.reload();
  }
});

initAuthGate();

const WIZARD_DISMISS_KEY = "rolling_papers_bot_wizard_dismissed";

const WIZARD_STEPS = [
  {
    title: "Welcome to Rolling Papers Bot",
    body: `
      <p>Rolling Papers Bot is a <strong>paper-first</strong> day-trading assistant for stocks, built on FastAPI and Alpaca.</p>
      <p>Paper trading is on by default and live trading is blocked until you explicitly enable and confirm it — so it's safe to explore and experiment.</p>
      <p>This wizard walks through connecting your account, funding your trading bankroll, placing a trade, tracking it, automating the strategy, and getting trade alerts on your phone. Reopen it anytime with the <strong>Getting Started</strong> button.</p>
    `,
  },
  {
    title: "Connect your Alpaca account",
    body: `
      <p>Open the <strong>Settings</strong> tab in the top navigation.</p>
      <ul>
        <li>Don't have keys yet? <a href="https://app.alpaca.markets/signup" target="_blank" rel="noopener">Get free Alpaca paper-trading keys</a> — required for quotes, charts, and trading.</li>
        <li>Paste your Alpaca <strong>paper trading</strong> API key and secret key</li>
        <li>Optionally <a href="https://site.financialmodelingprep.com/developer/docs" target="_blank" rel="noopener">get a free FMP key</a> too — the scanner's first choice for float-share data (it falls back to Yahoo Finance automatically, so everything works without it)</li>
        <li>Click <strong>Save Settings</strong>, then <strong>Test Connection</strong> to confirm both keys actually work</li>
      </ul>
      <p>Leave "Allow live trading" unchecked unless you specifically intend to trade real money.</p>
    `,
  },
  {
    title: "Fund your trading bankroll",
    body: `
      <p>The bot never trades with your whole account. Your Alpaca balance is treated as <strong>savings</strong>, and the bot can only ever spend what you've explicitly moved into its <strong>trading bankroll</strong> — like withdrawing cash from the bank before a trip.</p>
      <ul>
        <li>Open the <strong>Bankroll</strong> tab and use <strong>Savings &rarr; Trading Bankroll</strong> to withdraw an amount you're comfortable risking (e.g. $2,000)</li>
        <li>Every trade — manual or automated — is blocked until the bankroll is funded, and no position can exceed what's available in it</li>
        <li>Risk limits scale with the bankroll automatically: ~2% risked per trade, one position capped at 20% of it, and a daily loss limit of 6%</li>
        <li>Move money back with <strong>Return to Savings</strong> anytime it isn't tied up in an open position; the Statement lists every transfer</li>
      </ul>
    `,
  },
  {
    title: "Get to know the dashboard",
    body: `
      <ul>
        <li><strong>Status</strong> — bot mode (Paper or LIVE), running state, today's trades and realized P&amp;L</li>
        <li><strong>Market Clock</strong> — live clock plus a countdown to the next market open/close (NYSE hours)</li>
        <li><strong>Alpaca Ticker</strong> — quick bid/ask/mid quote for any symbol</li>
        <li><strong>Market Scanner</strong> — scores symbols against the strategy's five stock-selection pillars (relative volume, total volume, % change, price range, float); the built-in universe is pre-screened to genuinely small-float $2–$20 stocks</li>
        <li><strong>Backtest</strong> tab — replay the real strategy against historical minute data without touching your account</li>
      </ul>
    `,
  },
  {
    title: "Read the candlestick chart",
    body: `
      <ul>
        <li>Switch ranges with <strong>1D / 5D / 10D</strong> up to <strong>All</strong>, or pick an exact date</li>
        <li><strong>Scroll</strong> to zoom, <strong>drag</strong> to pan, <strong>double-click</strong> (or Reset Zoom) to reset</li>
        <li>The drawing toolbar adds trend lines, horizontal support/resistance, Fibonacci retracement, channels, rectangles, and text notes — pick a tool, click the chart to place points, <code>Esc</code> cancels</li>
      </ul>
    `,
  },
  {
    title: "Place your first (paper) trade",
    body: `
      <p>In <strong>Manual Paper Order</strong>: enter a symbol, quantity, an optional estimated price, choose Buy or Sell, then Submit.</p>
      <p>Since paper trading is on by default, this uses simulated money — a safe way to try things out.</p>
      <p>Two gates apply to every buy: your <strong>bankroll</strong> must have enough available, and a single position can't exceed 20% of it. Attaching a stop loss is strongly recommended — a position without one is only protected by the bot's exit-signal monitoring.</p>
    `,
  },
  {
    title: "Track what you bought",
    body: `
      <ul>
        <li><strong>Positions</strong> panel — every open position with entry price, current price, market value, and unrealized profit/loss in $ and %. <span class="up">Green</span> means you're up, <span class="down">red</span> means you're down. Refreshes automatically every 20 seconds.</li>
        <li><strong>Trade History</strong> — every order this app ever submitted, with fill prices, exit price, <em>why</em> it exited (target, stop, or exit signal), and realized P&amp;L per trade</li>
        <li><strong>Performance</strong> panel — total account equity over time (1D/1W/1M) with overall change in $ and %</li>
        <li>The <strong>Bankroll</strong> tab shows how much is deployed in open positions vs. still available, and the bankroll's own realized P&amp;L</li>
      </ul>
    `,
  },
  {
    title: "Automate it (optional)",
    body: `
      <p><strong>Auto-Trading</strong> (top bar, off by default) runs the strategy hands-free during market hours: every minute it sweeps the <strong>entire market</strong> (~8,000+ tradable stocks) for anything fitting the five-pillar profile, and for a genuine buy signal — a first pullback holding above VWAP and the 9&nbsp;EMA with a new-high break — it buys with a <strong>stop-loss resting at the broker</strong>.</p>
      <p>There's deliberately <em>no</em> automatic profit target: winners are held past the first level, and the bot re-checks every open position each cycle for real exit indicators (a red candle, a topping tail) and sells when one fires. That position monitoring runs <strong>even when auto-trading is off</strong> — anything you hold is always being watched while the market is open.</p>
      <p>Daily discipline rules pause new entries for the rest of the day: 3 losing trades in a row, giving back half the day's peak profit, or an hour passing without a trade. All of this state survives restarts. <strong>Run Tick</strong> is the manual version — it evaluates one symbol and reports what it sees without trading.</p>
    `,
  },
  {
    title: "Phone alerts & staying safe",
    body: `
      <p><strong>Get pushed when something real happens:</strong> install the free <a href="https://ntfy.sh" target="_blank" rel="noopener">ntfy</a> app, subscribe to a long random topic name (it acts like a password), and paste that topic into <strong>Settings &rarr; Phone notifications</strong>. Saving sends a test push immediately. You'll then get alerts for every trade opened, every exit with its realized P&amp;L, and any walk-away — nothing noisier.</p>
      <p><strong>Two protections worth knowing:</strong></p>
      <ul>
        <li><strong>Live trading</strong> can only be armed by turning paper mode off <em>and</em> checking "Allow live trading" <em>and</em> confirming a warning dialog — and the Status panel then shows a red <strong>LIVE — REAL MONEY</strong> so there's never doubt about which world the money is in</li>
        <li><strong>Dashboard Login</strong> (Settings) puts a username/password on this whole dashboard — strongly recommended if it's reachable from outside your machine</li>
      </ul>
    `,
  },
];

let wizardStep = 0;

function renderWizardStep() {
  const step = WIZARD_STEPS[wizardStep];
  document.querySelector("#wizard-title").textContent = step.title;
  document.querySelector("#wizard-body").innerHTML = step.body;
  document.querySelector("#wizard-back").disabled = wizardStep === 0;
  document.querySelector("#wizard-next").textContent = wizardStep === WIZARD_STEPS.length - 1 ? "Finish" : "Next";

  const dots = document.querySelector("#wizard-dots");
  dots.innerHTML = WIZARD_STEPS.map((_, index) => {
    const cls = index === wizardStep ? "wizard-dot active" : index < wizardStep ? "wizard-dot complete" : "wizard-dot";
    return `<span class="${cls}" data-step="${index}"></span>`;
  }).join("");
  dots.querySelectorAll(".wizard-dot").forEach((dot) => {
    dot.addEventListener("click", () => {
      wizardStep = Number(dot.dataset.step);
      renderWizardStep();
    });
  });
}

function openWizard() {
  wizardStep = 0;
  renderWizardStep();
  document.querySelector("#wizard-overlay").hidden = false;
}

function closeWizard() {
  document.querySelector("#wizard-overlay").hidden = true;
  if (document.querySelector("#wizard-dont-show").checked) {
    localStorage.setItem(WIZARD_DISMISS_KEY, "true");
  }
}

function getEtParts(date = new Date()) {
  const parts = {};
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    weekday: "short",
  })
    .formatToParts(date)
    .forEach(({ type, value }) => {
      parts[type] = value;
    });
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
    second: Number(parts.second),
  };
}

function isWeekdayUtc(year, month, day) {
  return ![0, 6].includes(new Date(Date.UTC(year, month - 1, day)).getUTCDay());
}

function nextMarketOpen(nowMs) {
  const parts = getEtParts(new Date(nowMs));
  const offsetMs = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second) - nowMs;
  const minutesNow = parts.hour * 60 + parts.minute;
  const opensToday = isWeekdayUtc(parts.year, parts.month, parts.day) && minutesNow < 9 * 60 + 30;

  const candidate = new Date(Date.UTC(parts.year, parts.month - 1, parts.day));
  if (!opensToday) candidate.setUTCDate(candidate.getUTCDate() + 1);
  while ([0, 6].includes(candidate.getUTCDay())) candidate.setUTCDate(candidate.getUTCDate() + 1);

  const pretendUtcOpen = Date.UTC(candidate.getUTCFullYear(), candidate.getUTCMonth(), candidate.getUTCDate(), 9, 30, 0);
  return pretendUtcOpen - offsetMs;
}

function nextMarketClose(nowMs) {
  if (isMarketOpen(nowMs)) {
    const parts = getEtParts(new Date(nowMs));
    const offsetMs = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second) - nowMs;
    const pretendUtcClose = Date.UTC(parts.year, parts.month - 1, parts.day, 16, 0, 0);
    return pretendUtcClose - offsetMs;
  }
  return nextMarketOpen(nowMs) + 6.5 * 60 * 60 * 1000;
}

function isMarketOpen(nowMs) {
  const parts = getEtParts(new Date(nowMs));
  const minutesNow = parts.hour * 60 + parts.minute;
  return isWeekdayUtc(parts.year, parts.month, parts.day) && minutesNow >= 9 * 60 + 30 && minutesNow < 16 * 60;
}

function formatCountdown(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

function updateMarketClock() {
  const now = Date.now();
  document.querySelector("#clock-local").textContent = new Date(now).toLocaleTimeString();
  document.querySelector("#clock-market").textContent = new Date(now).toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
  });

  const statusEl = document.querySelector("#market-status");
  const openCountdownEl = document.querySelector("#market-open-countdown");
  const closeCountdownEl = document.querySelector("#market-close-countdown");
  const marketOpen = isMarketOpen(now);

  statusEl.textContent = marketOpen ? "Open" : "Closed";
  statusEl.className = marketOpen ? "up" : "down";
  openCountdownEl.textContent = marketOpen ? "Market is open" : formatCountdown(nextMarketOpen(now) - now);
  closeCountdownEl.textContent = formatCountdown(nextMarketClose(now) - now);
}

function render(status) {
  document.querySelector("#message").textContent = status.last_message;
  const modeEl = document.querySelector("#mode");
  modeEl.textContent = status.paper ? "Paper" : "LIVE — REAL MONEY";
  modeEl.className = status.paper ? "" : "live-mode";
  document.querySelector("#running").textContent = status.running ? "Yes" : "No";
  const startBtn = document.querySelector("#start");
  startBtn.setAttribute("aria-pressed", String(Boolean(status.running)));
  startBtn.textContent = status.running ? "Started" : "Start";
  document.querySelector("#symbol").textContent = status.symbol;
  document.querySelector("#signal").textContent = status.last_signal;
  document.querySelector("#trades").textContent = status.trades_today;
  const pnl = Number(status.daily_pnl);
  const pnlEl = document.querySelector("#pnl");
  pnlEl.textContent = `${pnl >= 0 ? "+" : ""}$${pnl.toFixed(2)}`;
  pnlEl.className = pnl >= 0 ? "up" : "down";
  document.querySelector("#auto-trading-status").textContent = status.auto_trading_enabled ? "On" : "Off";
  const autoBtn = document.querySelector("#toggle-auto-trading");
  autoBtn.setAttribute("aria-pressed", String(Boolean(status.auto_trading_enabled)));
  autoBtn.textContent = status.auto_trading_enabled ? "Auto-Trading: On" : "Auto-Trading: Off";
  document.querySelector("#last-automation-run").textContent = status.last_automation_run_at
    ? new Date(status.last_automation_run_at).toLocaleString()
    : "Never";
}

async function refresh() {
  render(await api("/api/status"));
}

function switchView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${name}-view`));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
}

async function loadSettings() {
  try {
    const settings = await api("/api/settings");
    const form = document.querySelector("#settings-form");
    form.alpaca_api_key.value = settings.alpaca_api_key;
    form.alpaca_secret_key.value = settings.alpaca_secret_key;
    form.fmp_api_key.value = settings.fmp_api_key;
    form.ntfy_topic.value = settings.ntfy_topic || "";
    form.alpaca_paper.checked = settings.alpaca_paper;
    form.allow_live_trading.checked = settings.allow_live_trading;
  } catch (error) {
    document.querySelector("#message").textContent = `Could not load settings: ${error.message}`;
  }
}

async function checkApiKeysConfigured() {
  try {
    const settings = await api("/api/settings");
    document.querySelector("#setup-banner").hidden = Boolean(settings.alpaca_api_key);
  } catch (error) {
    // Don't block the UI over a settings-check failure — the rest of the app
    // already surfaces broker errors where they matter.
  }
}

const DEFAULT_SYMBOL = "AAPL";
const BARS_PER_DAY = 390;

function symbolForUrl(symbol) {
  return symbol.toUpperCase();
}

function renderQuote(quote) {
  document.querySelector("#quote-bid").textContent = quote.bid_price ? `$${Number(quote.bid_price).toFixed(2)}` : "-";
  document.querySelector("#quote-ask").textContent = quote.ask_price ? `$${Number(quote.ask_price).toFixed(2)}` : "-";
  document.querySelector("#quote-mid").textContent = quote.midpoint ? `$${Number(quote.midpoint).toFixed(2)}` : "-";
  document.querySelector("#quote-time").textContent = quote.timestamp ? new Date(quote.timestamp).toLocaleTimeString() : "-";
}

async function refreshQuote(symbol = DEFAULT_SYMBOL) {
  const quote = await api(`/api/quote/${encodeURIComponent(symbolForUrl(symbol))}`);
  renderQuote(quote);
}

let chartRangeDays = 1;
let chartRangePeriod = null;

async function refreshChart(symbol = getManualSymbol()) {
  const selectedDate = document.querySelector("#chart-date").value;
  const params = new URLSearchParams();
  if (selectedDate) {
    params.set("trading_date", selectedDate);
    params.set("limit", String(BARS_PER_DAY));
  } else if (chartRangePeriod) {
    params.set("period", chartRangePeriod);
  } else {
    params.set("days", String(chartRangeDays));
    params.set("limit", String(chartRangeDays * BARS_PER_DAY));
  }
  const response = await api(`/api/bars/${encodeURIComponent(symbolForUrl(symbol))}?${params.toString()}`);
  setChartData(response.symbol, response.bars);
}

function setChartRange(button) {
  chartRangeDays = button.dataset.days ? Number(button.dataset.days) : chartRangeDays;
  chartRangePeriod = button.dataset.period || null;
  document.querySelector("#chart-date").value = "";
  document.querySelectorAll("#range-buttons .range-btn").forEach((btn) => {
    btn.classList.toggle("active", btn === button);
  });
}

let chartState = null;
const MIN_VISIBLE_CANDLES = 10;

function getManualSymbol() {
  return document.querySelector("#trade-form input[name='symbol']").value || DEFAULT_SYMBOL;
}

function getTickerSymbol() {
  return document.querySelector("#quote-form input[name='symbol']").value || DEFAULT_SYMBOL;
}

// Polls fn on an interval, skipping ticks while the tab is hidden so background
// tabs don't burn API calls the user isn't looking at.
function startAutoRefresh(fn, intervalMs) {
  setInterval(() => {
    if (document.hidden) return;
    fn().catch(() => {});
  }, intervalMs);
}

function setChartData(symbol, bars) {
  pendingDrawing = null;
  previewPoint = null;
  stopReplay();
  chartState = { symbol, bars, view: { start: 0, end: bars.length } };
  renderChart();
}

function resetChartZoom() {
  if (!chartState) return;
  stopReplay();
  chartState.view = { start: 0, end: chartState.bars.length };
  renderChart();
}

// Bar replay: reuses the chart's own zoom view ({start, end} into chartState.bars) -
// "replaying" is just growing view.end one step at a time and re-rendering, the same
// mechanism the zoom/pan already relies on. Practice tool, not a strategy backtest:
// this only reveals bars already fetched at whatever granularity the current range
// uses (1-minute bars intraday, daily/weekly further out) - there's no per-second
// data behind this app to replay at that resolution.
let replayState = null; // { startIndex, currentIndex, playing }
let replayIntervalId = null;
let awaitingReplayStart = false;

function barIndexFromCanvasXY(x) {
  const { padding, candleStep, view, bars } = chartState;
  const fracIndex = view.start + (x - padding.left) / candleStep;
  return Math.max(0, Math.min(bars.length - 1, Math.round(fracIndex)));
}

function armReplaySelection() {
  if (!chartState || !chartState.bars.length) {
    document.querySelector("#message").textContent = "Load a chart before starting a replay.";
    return;
  }
  pauseReplay();
  awaitingReplayStart = true;
  document.querySelector("#candlestick-chart").classList.add("replay-arming");
  document.querySelector("#chart-hint").textContent = "Click a candle to start the replay from there.";
}

function startReplay(startIndex) {
  awaitingReplayStart = false;
  document.querySelector("#candlestick-chart").classList.remove("replay-arming");
  const start = Math.max(MIN_VISIBLE_CANDLES, startIndex);
  replayState = { startIndex: start, currentIndex: start, playing: false };
  chartState.view.end = replayState.currentIndex;
  document.querySelector("#replay-toolbar").hidden = false;
  document.querySelector("#replay-play-pause").innerHTML = "&#9654;";
  document.querySelector("#chart-hint").textContent = "Step or play through the chart. Exit Replay to see the full picture again.";
  updateReplayPositionLabel();
  renderChart();
}

function replayStepSize() {
  return Number(document.querySelector("#replay-step-size").value);
}

function stepReplay(direction) {
  if (!replayState || !chartState) return;
  const next = replayState.currentIndex + direction * replayStepSize();
  replayState.currentIndex = Math.max(MIN_VISIBLE_CANDLES, Math.min(chartState.bars.length, next));
  chartState.view.end = replayState.currentIndex;
  updateReplayPositionLabel();
  renderChart();
  if (replayState.currentIndex >= chartState.bars.length) pauseReplay();
}

function updateReplayPositionLabel() {
  if (!replayState || !chartState) return;
  const bar = chartState.bars[replayState.currentIndex - 1];
  const label = bar
    ? new Date(bar.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "-";
  document.querySelector("#replay-position").textContent = `Bar ${replayState.currentIndex} of ${chartState.bars.length} - ${label}`;
}

function playReplay() {
  if (!replayState || replayState.playing) return;
  replayState.playing = true;
  document.querySelector("#replay-play-pause").innerHTML = "&#9208;";
  const speedMs = Number(document.querySelector("#replay-speed").value);
  replayIntervalId = setInterval(() => stepReplay(1), speedMs);
}

function pauseReplay() {
  if (!replayState) return;
  replayState.playing = false;
  document.querySelector("#replay-play-pause").innerHTML = "&#9654;";
  if (replayIntervalId) {
    clearInterval(replayIntervalId);
    replayIntervalId = null;
  }
}

function toggleReplayPlay() {
  if (!replayState) return;
  if (replayState.playing) pauseReplay();
  else playReplay();
}

// Called whenever the chart data or zoom changes for a reason other than replay
// itself (a new symbol/range, or Reset Zoom) - always safe to call even when no
// replay is active.
function stopReplay() {
  pauseReplay();
  awaitingReplayStart = false;
  const canvas = document.querySelector("#candlestick-chart");
  if (canvas) canvas.classList.remove("replay-arming");
  if (replayState) {
    replayState = null;
    document.querySelector("#replay-toolbar").hidden = true;
    document.querySelector("#chart-hint").textContent = DRAW_HINTS[activeDrawTool] || DRAW_HINTS.cursor;
  }
}

function exitReplay() {
  stopReplay();
  if (chartState) chartState.view = { start: 0, end: chartState.bars.length };
  renderChart();
}

function inferGranularity(bars) {
  if (bars.length < 2) return "minute";
  const spanMs = new Date(bars[bars.length - 1].timestamp).getTime() - new Date(bars[0].timestamp).getTime();
  const avgGapMs = spanMs / (bars.length - 1);
  const HOUR = 60 * 60 * 1000;
  if (avgGapMs < 12 * HOUR) return "minute";
  if (avgGapMs < 4 * 24 * HOUR) return "day";
  if (avgGapMs < 20 * 24 * HOUR) return "week";
  return "month";
}

const GRANULARITY_LABEL = {
  minute: "1-minute candles",
  day: "daily candles",
  week: "weekly candles",
  month: "monthly candles",
};

function renderChart() {
  const canvas = document.querySelector("#candlestick-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const padding = { top: 34, right: 70, bottom: 46, left: 54 };
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(4, 11, 19, 0.5)";
  ctx.fillRect(0, 0, width, height);

  if (!chartState || !chartState.bars.length) {
    ctx.fillStyle = "#eef6fb";
    ctx.font = "16px Segoe UI, sans-serif";
    ctx.fillText(chartState ? `${chartState.symbol} candles` : "Candlestick chart", padding.left, 24);
    ctx.fillStyle = "#a8b9c8";
    ctx.fillText("No recent candle data available", padding.left, height / 2);
    return;
  }

  const { symbol, bars, view } = chartState;
  const visibleBars = bars.slice(view.start, view.end);
  const granularity = inferGranularity(visibleBars);

  ctx.fillStyle = "#eef6fb";
  ctx.font = "16px Segoe UI, sans-serif";
  ctx.fillText(`${symbol} ${GRANULARITY_LABEL[granularity]}`, padding.left, 24);

  const highs = visibleBars.map((bar) => bar.high);
  const lows = visibleBars.map((bar) => bar.low);
  const maxPrice = Math.max(...highs);
  const minPrice = Math.min(...lows);
  const priceRange = maxPrice - minPrice || 1;
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const candleGap = 2;
  const candleWidth = Math.max(1, chartWidth / visibleBars.length - candleGap);
  const yFor = (price) => padding.top + ((maxPrice - price) / priceRange) * chartHeight;
  const candleStep = chartWidth / visibleBars.length;

  chartState.padding = padding;
  chartState.chartWidth = chartWidth;
  chartState.chartHeight = chartHeight;
  chartState.candleStep = candleStep;
  chartState.candleWidth = candleWidth;
  chartState.minPrice = minPrice;
  chartState.maxPrice = maxPrice;
  chartState.visibleBars = visibleBars;

  ctx.strokeStyle = "rgba(190, 213, 230, 0.14)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + (chartHeight / 4) * i;
    const price = maxPrice - (priceRange / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    ctx.fillStyle = "#a8b9c8";
    ctx.font = "12px Segoe UI, sans-serif";
    ctx.fillText(`$${price.toFixed(2)}`, width - padding.right + 10, y + 4);
  }

  visibleBars.forEach((bar, index) => {
    const x = padding.left + index * candleStep + candleGap / 2;
    const openY = yFor(bar.open);
    const closeY = yFor(bar.close);
    const highY = yFor(bar.high);
    const lowY = yFor(bar.low);
    const up = bar.close >= bar.open;
    const color = up ? "#7cf0b3" : "#ff9b9b";

    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(x + candleWidth / 2, highY);
    ctx.lineTo(x + candleWidth / 2, lowY);
    ctx.stroke();
    ctx.fillRect(x, Math.min(openY, closeY), candleWidth, Math.max(2, Math.abs(closeY - openY)));
  });

  drawXAxis(ctx, visibleBars, padding, chartWidth, chartHeight, candleStep, granularity);
  drawAnnotations(ctx);
}

function xAxisLabels(timestamp, granularity) {
  if (granularity === "minute") {
    return [
      timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      timestamp.toLocaleDateString([], { month: "short", day: "numeric" }),
    ];
  }
  if (granularity === "month") {
    return [timestamp.toLocaleDateString([], { month: "short" }), String(timestamp.getFullYear())];
  }
  return [timestamp.toLocaleDateString([], { month: "short", day: "numeric" }), String(timestamp.getFullYear())];
}

function drawXAxis(ctx, visibleBars, padding, chartWidth, chartHeight, candleStep, granularity) {
  const axisY = padding.top + chartHeight;
  const targetLabels = Math.max(3, Math.min(10, Math.floor(chartWidth / 90)));
  const step = Math.max(1, Math.ceil(visibleBars.length / targetLabels));

  ctx.strokeStyle = "rgba(190, 213, 230, 0.14)";
  ctx.fillStyle = "#a8b9c8";
  ctx.font = "11px Segoe UI, sans-serif";
  ctx.textAlign = "center";

  for (let index = 0; index < visibleBars.length; index += step) {
    const bar = visibleBars[index];
    const x = padding.left + index * candleStep + candleStep / 2;
    const timestamp = new Date(bar.timestamp);
    const [primaryLabel, secondaryLabel] = xAxisLabels(timestamp, granularity);

    ctx.strokeStyle = "rgba(190, 213, 230, 0.14)";
    ctx.beginPath();
    ctx.moveTo(x, axisY);
    ctx.lineTo(x, axisY + 4);
    ctx.stroke();

    ctx.fillStyle = "#a8b9c8";
    ctx.fillText(primaryLabel, x, axisY + 16);

    ctx.fillStyle = "#7f93a4";
    ctx.fillText(secondaryLabel, x, axisY + 30);
  }

  ctx.textAlign = "left";
}

let activeDrawTool = "cursor";
let pendingDrawing = null;
let previewPoint = null;
const drawingsBySymbol = {};
let activeDrawColor = "#8ab4f8";

const DRAWING_POINTS_REQUIRED = {
  trendline: 2,
  ray: 2,
  info_line: 2,
  extended_line: 2,
  trend_angle: 2,
  horizontal: 1,
  horizontal_ray: 1,
  vertical: 1,
  crossline: 1,
  parallel_channel: 3,
  regression: 2,
  flat_channel: 3,
  disjoint_channel: 4,
  pitchfork: 3,
  schiff_pitchfork: 3,
  modified_schiff_pitchfork: 3,
  inside_pitchfork: 3,
  fib: 2,
  rectangle: 2,
  text: 1,
};

const DRAW_HINTS = {
  cursor: "Scroll to zoom, drag to pan, double-click to reset zoom.",
  trendline: "Click a start point, then an end point to draw a trend line.",
  ray: "Click a start point, then a second point - the line continues past it.",
  info_line: "Click a start point, then an end point to see the price/time change between them.",
  extended_line: "Click two points - the line extends across the whole chart both ways.",
  trend_angle: "Click a start point, then an end point to measure the angle.",
  horizontal: "Click a price level to draw a horizontal line.",
  horizontal_ray: "Click a point - the line extends right from there.",
  vertical: "Click a point in time to mark it with a vertical line.",
  crossline: "Click a point to mark it with crossed horizontal and vertical lines.",
  parallel_channel: "Click two points for the base line, then a third point to set channel width.",
  regression: "Click a start point and an end point - draws the best-fit trend line (and its bands) between them.",
  flat_channel: "Click two points for the sloped side, then a third point for the flat side.",
  disjoint_channel: "Click two points for one line, then two more for the other - they don't need to be parallel.",
  pitchfork: "Click three points: the handle, then the two prongs.",
  schiff_pitchfork: "Click three points - same as Pitchfork, with an adjusted starting point.",
  modified_schiff_pitchfork: "Click three points - same as Pitchfork, with a further-adjusted starting point.",
  inside_pitchfork: "Click three points - a Pitchfork drawn from the opposite end.",
  fib: "Click the start extreme, then the end extreme for a Fibonacci retracement.",
  rectangle: "Click one corner, then the opposite corner.",
  text: "Click a point on the chart to place a text note.",
  erase: "Click a drawing to remove it.",
};

function currentDrawings() {
  if (!chartState) return [];
  if (!drawingsBySymbol[chartState.symbol]) drawingsBySymbol[chartState.symbol] = [];
  return drawingsBySymbol[chartState.symbol];
}

function eventToCanvasXY(event) {
  const canvas = document.querySelector("#candlestick-chart");
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return { x: (event.clientX - rect.left) * scaleX, y: (event.clientY - rect.top) * scaleY };
}

function timeForFractionalIndex(bars, fracIndex) {
  const clamped = Math.max(0, Math.min(bars.length - 1, fracIndex));
  const lo = Math.floor(clamped);
  const hi = Math.min(bars.length - 1, lo + 1);
  const loTime = new Date(bars[lo].timestamp).getTime();
  const hiTime = new Date(bars[hi].timestamp).getTime();
  return loTime + (hiTime - loTime) * (clamped - lo);
}

function fractionalIndexForTime(bars, timeMs) {
  if (!bars.length) return 0;
  const firstTime = new Date(bars[0].timestamp).getTime();
  if (timeMs <= firstTime) return 0;
  const lastTime = new Date(bars[bars.length - 1].timestamp).getTime();
  if (timeMs >= lastTime) return bars.length - 1;
  let lo = 0;
  let hi = bars.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (new Date(bars[mid].timestamp).getTime() <= timeMs) lo = mid;
    else hi = mid;
  }
  const loTime = new Date(bars[lo].timestamp).getTime();
  const hiTime = new Date(bars[hi].timestamp).getTime();
  const ratio = hiTime > loTime ? (timeMs - loTime) / (hiTime - loTime) : 0;
  return lo + ratio;
}

function dataPointFromCanvasXY(x, y) {
  const { padding, chartHeight, candleStep, view, bars, minPrice, maxPrice } = chartState;
  const fracIndex = view.start + (x - padding.left) / candleStep;
  const time = timeForFractionalIndex(bars, fracIndex);
  const priceRange = maxPrice - minPrice || 1;
  const price = maxPrice - ((y - padding.top) / chartHeight) * priceRange;
  return { time, price };
}

function xForPoint(point) {
  const { padding, candleStep, view, bars } = chartState;
  return padding.left + (fractionalIndexForTime(bars, point.time) - view.start) * candleStep;
}

function yForPrice(price) {
  const { padding, chartHeight, minPrice, maxPrice } = chartState;
  const priceRange = maxPrice - minPrice || 1;
  return padding.top + ((maxPrice - price) / priceRange) * chartHeight;
}

function channelOffset(p1, p2, p3) {
  const ratio = p2.time !== p1.time ? (p3.time - p1.time) / (p2.time - p1.time) : 0;
  const priceOnBase = p1.price + (p2.price - p1.price) * ratio;
  return p3.price - priceOnBase;
}

// Extends the line through pixel points (x1,y1)-(x2,y2) to the given target x,
// returning the y it would cross there. Used to draw rays/extended lines/channel
// edges/pitchfork teeth all the way to the chart's edge in screen space, which is
// simpler and visually correct regardless of how the underlying time axis maps to
// pixels (it isn't linear in wall-clock time - x is linear in bar index).
function extendLineToX(x1, y1, x2, y2, targetX) {
  if (x2 === x1) return y1;
  const slope = (y2 - y1) / (x2 - x1);
  return y1 + slope * (targetX - x1);
}

function hexToRgbComponents(hex) {
  const clean = (hex || "#8ab4f8").replace("#", "");
  const value = parseInt(clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean, 16);
  return { r: (value >> 16) & 255, g: (value >> 8) & 255, b: value & 255 };
}

function withAlpha(hex, alpha) {
  const { r, g, b } = hexToRgbComponents(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function drawingColor(drawing) {
  return drawing.color || "#8ab4f8";
}

// Least-squares fit of closing price against bar index over bars[startIdx..endIdx].
function linearRegression(bars, startIdx, endIdx) {
  const n = endIdx - startIdx + 1;
  if (n < 2) return null;
  let sumX = 0;
  let sumY = 0;
  let sumXY = 0;
  let sumXX = 0;
  for (let i = startIdx; i <= endIdx; i += 1) {
    const x = i - startIdx;
    const y = bars[i].close;
    sumX += x;
    sumY += y;
    sumXY += x * y;
    sumXX += x * x;
  }
  const denominator = n * sumXX - sumX * sumX;
  const slope = denominator ? (n * sumXY - sumX * sumY) / denominator : 0;
  const intercept = (sumY - slope * sumX) / n;
  return { slope, intercept, n };
}

// Shared by every pitchfork variant: draws the median line from `handle` to the
// midpoint of toothA/toothB, extended to the chart edge, plus two lines parallel to
// it passing through toothA and toothB. The variants below only differ in which of
// the three clicked points play the handle/tooth roles.
function renderPitchforkLines(ctx, handle, toothA, toothB, color) {
  const midAB = { time: (toothA.time + toothB.time) / 2, price: (toothA.price + toothB.price) / 2 };
  const hx = xForPoint(handle);
  const hy = yForPrice(handle.price);
  const mx = xForPoint(midAB);
  const my = yForPrice(midAB.price);
  const { padding, chartWidth } = chartState;
  const forwardX = mx >= hx ? padding.left + chartWidth : padding.left;
  const medianEndY = extendLineToX(hx, hy, mx, my, forwardX);

  ctx.strokeStyle = color;
  ctx.setLineDash([]);
  ctx.beginPath();
  ctx.moveTo(hx, hy);
  ctx.lineTo(forwardX, medianEndY);
  ctx.stroke();

  const dx = mx - hx;
  const dy = my - hy;
  [toothA, toothB].forEach((p) => {
    const px = xForPoint(p);
    const py = yForPrice(p.price);
    const farX = dx >= 0 ? padding.left + chartWidth : padding.left;
    const t = dx !== 0 ? (farX - px) / dx : 0;
    const farY = py + dy * t;
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.lineTo(farX, farY);
    ctx.stroke();
  });
}

function setDrawTool(tool) {
  activeDrawTool = tool;
  pendingDrawing = null;
  previewPoint = null;
  document.querySelectorAll(".draw-tool-item").forEach((btn) => btn.classList.toggle("active", btn.dataset.tool === tool));
  const active = document.querySelector(`.draw-tool-item[data-tool="${tool}"]`);
  document.querySelector("#draw-tool-label").textContent = active ? active.textContent : "Cursor";
  document.querySelector("#erase-tool").classList.toggle("active", tool === "erase");
  const canvas = document.querySelector("#candlestick-chart");
  canvas.classList.toggle("drawing", tool !== "cursor" && tool !== "erase");
  canvas.classList.toggle("erasing", tool === "erase");
  document.querySelector("#chart-hint").textContent = DRAW_HINTS[tool] || DRAW_HINTS.cursor;
  if (chartState) renderChart();
}

function cancelPendingDrawing() {
  pendingDrawing = null;
  previewPoint = null;
  if (chartState) renderChart();
}

function handleChartClick(event) {
  if (!chartState || !chartState.padding) return;

  if (awaitingReplayStart) {
    const { x, y } = eventToCanvasXY(event);
    const { padding, chartWidth, chartHeight } = chartState;
    if (x < padding.left || x > padding.left + chartWidth || y < padding.top || y > padding.top + chartHeight) return;
    startReplay(barIndexFromCanvasXY(x));
    return;
  }

  if (activeDrawTool === "cursor") return;

  const { x, y } = eventToCanvasXY(event);
  const { padding, chartWidth, chartHeight } = chartState;
  if (x < padding.left || x > padding.left + chartWidth || y < padding.top || y > padding.top + chartHeight) return;

  if (activeDrawTool === "erase") {
    eraseDrawingNear(x, y);
    return;
  }

  const point = dataPointFromCanvasXY(x, y);

  if (activeDrawTool === "text") {
    const text = window.prompt("Annotation text:", "");
    if (text) currentDrawings().push({ type: "text", points: [point], text, color: activeDrawColor });
    renderChart();
    return;
  }

  if (!pendingDrawing || pendingDrawing.type !== activeDrawTool) {
    pendingDrawing = { type: activeDrawTool, points: [], color: activeDrawColor };
  }
  pendingDrawing.points.push(point);

  if (pendingDrawing.points.length >= (DRAWING_POINTS_REQUIRED[activeDrawTool] || 1)) {
    currentDrawings().push(pendingDrawing);
    pendingDrawing = null;
    previewPoint = null;
  }
  renderChart();
}

function distanceToSegment(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lengthSq = dx * dx + dy * dy;
  let t = lengthSq === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

// Shared by every pitchfork variant's hit test, mirroring renderPitchforkLines'
// geometry exactly so a click "on the line" always matches what's actually drawn.
function pitchforkHitTest(handle, toothA, toothB, x, y, threshold) {
  const midAB = { time: (toothA.time + toothB.time) / 2, price: (toothA.price + toothB.price) / 2 };
  const hx = xForPoint(handle);
  const hy = yForPrice(handle.price);
  const mx = xForPoint(midAB);
  const my = yForPrice(midAB.price);
  const { padding, chartWidth } = chartState;
  const forwardX = mx >= hx ? padding.left + chartWidth : padding.left;
  const medianEndY = extendLineToX(hx, hy, mx, my, forwardX);
  if (distanceToSegment(x, y, hx, hy, forwardX, medianEndY) <= threshold) return true;

  const dx = mx - hx;
  const dy = my - hy;
  return [toothA, toothB].some((p) => {
    const px = xForPoint(p);
    const py = yForPrice(p.price);
    const farX = dx >= 0 ? padding.left + chartWidth : padding.left;
    const t = dx !== 0 ? (farX - px) / dx : 0;
    const farY = py + dy * t;
    return distanceToSegment(x, y, px, py, farX, farY) <= threshold;
  });
}

function hitTestDrawing(drawing, x, y, threshold) {
  const { padding, chartWidth } = chartState;
  const leftX = padding.left;
  const rightX = padding.left + chartWidth;
  const pts = drawing.points;

  if (drawing.type === "horizontal") {
    return Math.abs(y - yForPrice(pts[0].price)) <= threshold;
  }
  if (drawing.type === "horizontal_ray") {
    const y0 = yForPrice(pts[0].price);
    return distanceToSegment(x, y, xForPoint(pts[0]), y0, rightX, y0) <= threshold;
  }
  if (drawing.type === "vertical") {
    const x0 = xForPoint(pts[0]);
    return Math.abs(x - x0) <= threshold;
  }
  if (drawing.type === "crossline") {
    const x0 = xForPoint(pts[0]);
    const y0 = yForPrice(pts[0].price);
    return Math.abs(x - x0) <= threshold || Math.abs(y - y0) <= threshold;
  }
  if (drawing.type === "trendline" || drawing.type === "trend_angle" || drawing.type === "info_line") {
    if (pts.length < 2) return false;
    const [p1, p2] = pts;
    return distanceToSegment(x, y, xForPoint(p1), yForPrice(p1.price), xForPoint(p2), yForPrice(p2.price)) <= threshold;
  }
  if (drawing.type === "ray") {
    if (pts.length < 2) return false;
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    const targetX = x2 >= x1 ? rightX : leftX;
    return distanceToSegment(x, y, x1, y1, targetX, extendLineToX(x1, y1, x2, y2, targetX)) <= threshold;
  }
  if (drawing.type === "extended_line") {
    if (pts.length < 2) return false;
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    return (
      distanceToSegment(x, y, leftX, extendLineToX(x1, y1, x2, y2, leftX), rightX, extendLineToX(x1, y1, x2, y2, rightX)) <=
      threshold
    );
  }
  if (drawing.type === "rectangle") {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    const y1 = yForPrice(p1.price);
    const y2 = yForPrice(p2.price);
    return x >= Math.min(x1, x2) && x <= Math.max(x1, x2) && y >= Math.min(y1, y2) && y <= Math.max(y1, y2);
  }
  if (drawing.type === "text") {
    const p = pts[0];
    return Math.abs(x - xForPoint(p)) <= 60 && Math.abs(y - yForPrice(p.price)) <= 14;
  }
  if (drawing.type === "fib") {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    if (x < Math.min(x1, x2) || x > Math.max(x1, x2)) return false;
    return [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].some(
      (level) => Math.abs(y - yForPrice(p1.price + (p2.price - p1.price) * level)) <= threshold
    );
  }
  if (drawing.type === "parallel_channel") {
    const [p1, p2, p3] = pts;
    if (distanceToSegment(x, y, xForPoint(p1), yForPrice(p1.price), xForPoint(p2), yForPrice(p2.price)) <= threshold) return true;
    if (!p3) return false;
    const offset = channelOffset(p1, p2, p3);
    const o1 = { time: p1.time, price: p1.price + offset };
    const o2 = { time: p2.time, price: p2.price + offset };
    return distanceToSegment(x, y, xForPoint(o1), yForPrice(o1.price), xForPoint(o2), yForPrice(o2.price)) <= threshold;
  }
  if (drawing.type === "flat_channel") {
    if (pts.length < 2) return false;
    const [p1, p2, p3] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    if (
      distanceToSegment(x, y, leftX, extendLineToX(x1, y1, x2, y2, leftX), rightX, extendLineToX(x1, y1, x2, y2, rightX)) <=
      threshold
    )
      return true;
    if (!p3) return false;
    const flatY = yForPrice(p3.price);
    return Math.abs(y - flatY) <= threshold;
  }
  if (drawing.type === "disjoint_channel") {
    if (pts.length < 2) return false;
    const [p1, p2] = pts;
    if (distanceToSegment(x, y, xForPoint(p1), yForPrice(p1.price), xForPoint(p2), yForPrice(p2.price)) <= threshold) return true;
    if (pts.length < 4) return false;
    const [, , p3, p4] = pts;
    return distanceToSegment(x, y, xForPoint(p3), yForPrice(p3.price), xForPoint(p4), yForPrice(p4.price)) <= threshold;
  }
  if (drawing.type === "regression") {
    if (pts.length < 2) return false;
    const [p1, p2] = pts;
    const bars = chartState.bars;
    let idx1 = Math.round(fractionalIndexForTime(bars, p1.time));
    let idx2 = Math.round(fractionalIndexForTime(bars, p2.time));
    if (idx1 > idx2) [idx1, idx2] = [idx2, idx1];
    const reg = linearRegression(bars, idx1, idx2);
    if (!reg) return false;
    const start = { time: new Date(bars[idx1].timestamp).getTime(), price: reg.intercept };
    const end = { time: new Date(bars[idx2].timestamp).getTime(), price: reg.intercept + reg.slope * (idx2 - idx1) };
    return distanceToSegment(x, y, xForPoint(start), yForPrice(start.price), xForPoint(end), yForPrice(end.price)) <= threshold;
  }
  if (drawing.type === "pitchfork") {
    if (pts.length < 2) return false;
    const [p1, p2, p3] = pts;
    return pitchforkHitTest(p1, p2, p3 || p2, x, y, threshold);
  }
  if (drawing.type === "schiff_pitchfork") {
    if (pts.length < 2) return false;
    const [p1, p2, p3] = pts;
    const handle = { time: (p1.time + p2.time) / 2, price: (p1.price + p2.price) / 2 };
    return pitchforkHitTest(handle, p2, p3 || p2, x, y, threshold);
  }
  if (drawing.type === "modified_schiff_pitchfork") {
    if (pts.length < 2) return false;
    const [p1, p2, p3] = pts;
    const schiffHandle = { time: (p1.time + p2.time) / 2, price: (p1.price + p2.price) / 2 };
    const handle = { time: (p1.time + schiffHandle.time) / 2, price: (p1.price + schiffHandle.price) / 2 };
    return pitchforkHitTest(handle, p2, p3 || p2, x, y, threshold);
  }
  if (drawing.type === "inside_pitchfork") {
    if (pts.length < 2) return false;
    const [p1, p2, p3] = pts;
    if (!p3) return pitchforkHitTest(p1, p2, p2, x, y, threshold);
    return pitchforkHitTest(p3, p1, p2, x, y, threshold);
  }
  return false;
}

function eraseDrawingNear(x, y) {
  const list = currentDrawings();
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (hitTestDrawing(list[i], x, y, 8)) {
      list.splice(i, 1);
      renderChart();
      return;
    }
  }
}

function drawPriceLabel(ctx, price, y, color) {
  const { padding, chartWidth } = chartState;
  const label = `$${price.toFixed(2)}`;
  ctx.font = "11px Segoe UI, sans-serif";
  const textWidth = ctx.measureText(label).width;
  const x = padding.left + chartWidth - textWidth - 6;
  ctx.fillStyle = "rgba(3, 8, 14, 0.85)";
  ctx.fillRect(x - 4, y - 12, textWidth + 8, 16);
  ctx.fillStyle = color;
  ctx.textAlign = "left";
  ctx.fillText(label, x, y + 2);
}

function renderDrawing(ctx, drawing, isPreview = false) {
  ctx.lineWidth = 1.5;
  const color = drawingColor(drawing);
  const stroke = isPreview ? withAlpha(color, 0.6) : color;
  const { padding, chartWidth } = chartState;
  const leftX = padding.left;
  const rightX = padding.left + chartWidth;
  const pts = drawing.points;

  if (drawing.type === "horizontal" && pts[0]) {
    const y = yForPrice(pts[0].price);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(leftX, y);
    ctx.lineTo(rightX, y);
    ctx.stroke();
    drawPriceLabel(ctx, pts[0].price, y, stroke);
    return;
  }

  if (drawing.type === "horizontal_ray" && pts[0]) {
    const y = yForPrice(pts[0].price);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(xForPoint(pts[0]), y);
    ctx.lineTo(rightX, y);
    ctx.stroke();
    drawPriceLabel(ctx, pts[0].price, y, stroke);
    return;
  }

  if (drawing.type === "vertical" && pts[0]) {
    const x = xForPoint(pts[0]);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + chartState.chartHeight);
    ctx.stroke();
    return;
  }

  if (drawing.type === "crossline" && pts[0]) {
    const x = xForPoint(pts[0]);
    const y = yForPrice(pts[0].price);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(leftX, y);
    ctx.lineTo(rightX, y);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x, padding.top);
    ctx.lineTo(x, padding.top + chartState.chartHeight);
    ctx.stroke();
    drawPriceLabel(ctx, pts[0].price, y, stroke);
    return;
  }

  if (drawing.type === "trendline" && pts.length >= 2) {
    const [p1, p2] = pts;
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(xForPoint(p1), yForPrice(p1.price));
    ctx.lineTo(xForPoint(p2), yForPrice(p2.price));
    ctx.stroke();
    return;
  }

  if (drawing.type === "ray" && pts.length >= 2) {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    const targetX = x2 >= x1 ? rightX : leftX;
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(targetX, extendLineToX(x1, y1, x2, y2, targetX));
    ctx.stroke();
    return;
  }

  if (drawing.type === "extended_line" && pts.length >= 2) {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(leftX, extendLineToX(x1, y1, x2, y2, leftX));
    ctx.lineTo(rightX, extendLineToX(x1, y1, x2, y2, rightX));
    ctx.stroke();
    return;
  }

  if (drawing.type === "trend_angle" && pts.length >= 2) {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    const angleDeg = (Math.atan2(-(y2 - y1), x2 - x1) * 180) / Math.PI;
    ctx.fillStyle = stroke;
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`${angleDeg.toFixed(1)}°`, (x1 + x2) / 2 + 6, (y1 + y2) / 2 - 6);
    return;
  }

  if (drawing.type === "info_line" && pts.length >= 2) {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.stroke();
    const priceDelta = p2.price - p1.price;
    const pctDelta = p1.price ? (priceDelta / p1.price) * 100 : 0;
    const barDelta = Math.round(
      fractionalIndexForTime(chartState.bars, p2.time) - fractionalIndexForTime(chartState.bars, p1.time)
    );
    const sign = priceDelta >= 0 ? "+" : "";
    const label = `${sign}$${priceDelta.toFixed(2)} (${sign}${pctDelta.toFixed(2)}%), ${Math.abs(barDelta)} bars`;
    const midX = (x1 + x2) / 2;
    const midY = (y1 + y2) / 2;
    ctx.font = "11px Segoe UI, sans-serif";
    const textWidth = ctx.measureText(label).width;
    ctx.fillStyle = "rgba(3, 8, 14, 0.85)";
    ctx.fillRect(midX - textWidth / 2 - 4, midY - 20, textWidth + 8, 16);
    ctx.fillStyle = stroke;
    ctx.textAlign = "center";
    ctx.fillText(label, midX, midY - 8);
    ctx.textAlign = "left";
    return;
  }

  if (drawing.type === "rectangle" && pts.length >= 2) {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    const y1 = yForPrice(p1.price);
    const y2 = yForPrice(p2.price);
    ctx.fillStyle = withAlpha(color, isPreview ? 0.08 : 0.14);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    ctx.fillRect(left, top, Math.abs(x2 - x1), Math.abs(y2 - y1));
    ctx.strokeRect(left, top, Math.abs(x2 - x1), Math.abs(y2 - y1));
    return;
  }

  if (drawing.type === "fib" && pts.length >= 2) {
    const [p1, p2] = pts;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    const left = Math.min(x1, x2);
    const right = Math.max(x1, x2);
    [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].forEach((level) => {
      const price = p1.price + (p2.price - p1.price) * level;
      const y = yForPrice(price);
      ctx.strokeStyle = isPreview ? withAlpha(color, 0.5) : withAlpha(color, 0.85);
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(right, y);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#a8b9c8";
      ctx.font = "10px Segoe UI, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(`${(level * 100).toFixed(1)}%  $${price.toFixed(2)}`, left + 4, y - 3);
    });
    return;
  }

  if (drawing.type === "parallel_channel" && pts.length >= 2) {
    const [p1, p2, p3] = pts;
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(xForPoint(p1), yForPrice(p1.price));
    ctx.lineTo(xForPoint(p2), yForPrice(p2.price));
    ctx.stroke();

    if (p3) {
      const offset = channelOffset(p1, p2, p3);
      const o1 = { time: p1.time, price: p1.price + offset };
      const o2 = { time: p2.time, price: p2.price + offset };
      ctx.beginPath();
      ctx.moveTo(xForPoint(o1), yForPrice(o1.price));
      ctx.lineTo(xForPoint(o2), yForPrice(o2.price));
      ctx.stroke();

      ctx.fillStyle = withAlpha(color, 0.08);
      ctx.beginPath();
      ctx.moveTo(xForPoint(p1), yForPrice(p1.price));
      ctx.lineTo(xForPoint(p2), yForPrice(p2.price));
      ctx.lineTo(xForPoint(o2), yForPrice(o2.price));
      ctx.lineTo(xForPoint(o1), yForPrice(o1.price));
      ctx.closePath();
      ctx.fill();
    }
    return;
  }

  if (drawing.type === "flat_channel" && pts.length >= 2) {
    const [p1, p2, p3] = pts;
    const x1 = xForPoint(p1);
    const y1 = yForPrice(p1.price);
    const x2 = xForPoint(p2);
    const y2 = yForPrice(p2.price);
    const leftSlopeY = extendLineToX(x1, y1, x2, y2, leftX);
    const rightSlopeY = extendLineToX(x1, y1, x2, y2, rightX);
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(leftX, leftSlopeY);
    ctx.lineTo(rightX, rightSlopeY);
    ctx.stroke();

    if (p3) {
      const flatY = yForPrice(p3.price);
      ctx.beginPath();
      ctx.moveTo(leftX, flatY);
      ctx.lineTo(rightX, flatY);
      ctx.stroke();
      ctx.fillStyle = withAlpha(color, 0.08);
      ctx.beginPath();
      ctx.moveTo(leftX, leftSlopeY);
      ctx.lineTo(rightX, rightSlopeY);
      ctx.lineTo(rightX, flatY);
      ctx.lineTo(leftX, flatY);
      ctx.closePath();
      ctx.fill();
    }
    return;
  }

  if (drawing.type === "disjoint_channel" && pts.length >= 2) {
    const [p1, p2, p3, p4] = pts;
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(xForPoint(p1), yForPrice(p1.price));
    ctx.lineTo(xForPoint(p2), yForPrice(p2.price));
    ctx.stroke();

    if (p3) {
      ctx.beginPath();
      ctx.arc(xForPoint(p3), yForPrice(p3.price), 2.5, 0, Math.PI * 2);
      ctx.fillStyle = stroke;
      ctx.fill();
    }
    if (p4) {
      ctx.beginPath();
      ctx.moveTo(xForPoint(p3), yForPrice(p3.price));
      ctx.lineTo(xForPoint(p4), yForPrice(p4.price));
      ctx.stroke();
      ctx.fillStyle = withAlpha(color, 0.08);
      ctx.beginPath();
      ctx.moveTo(xForPoint(p1), yForPrice(p1.price));
      ctx.lineTo(xForPoint(p2), yForPrice(p2.price));
      ctx.lineTo(xForPoint(p4), yForPrice(p4.price));
      ctx.lineTo(xForPoint(p3), yForPrice(p3.price));
      ctx.closePath();
      ctx.fill();
    }
    return;
  }

  if (drawing.type === "regression" && pts.length >= 2) {
    const [p1, p2] = pts;
    const bars = chartState.bars;
    let idx1 = Math.round(fractionalIndexForTime(bars, p1.time));
    let idx2 = Math.round(fractionalIndexForTime(bars, p2.time));
    if (idx1 > idx2) [idx1, idx2] = [idx2, idx1];
    const reg = linearRegression(bars, idx1, idx2);
    if (!reg) return;
    let sumSq = 0;
    for (let i = idx1; i <= idx2; i += 1) {
      const predicted = reg.intercept + reg.slope * (i - idx1);
      sumSq += (bars[i].close - predicted) ** 2;
    }
    const stddev = Math.sqrt(sumSq / reg.n);
    const priceAt = (i) => reg.intercept + reg.slope * (i - idx1);
    const start = { time: new Date(bars[idx1].timestamp).getTime(), price: priceAt(idx1) };
    const end = { time: new Date(bars[idx2].timestamp).getTime(), price: priceAt(idx2) };
    ctx.strokeStyle = stroke;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(xForPoint(start), yForPrice(start.price));
    ctx.lineTo(xForPoint(end), yForPrice(end.price));
    ctx.stroke();
    ctx.strokeStyle = withAlpha(color, 0.5);
    ctx.setLineDash([4, 3]);
    [1, -1].forEach((sign) => {
      const s = { time: start.time, price: start.price + sign * stddev };
      const e = { time: end.time, price: end.price + sign * stddev };
      ctx.beginPath();
      ctx.moveTo(xForPoint(s), yForPrice(s.price));
      ctx.lineTo(xForPoint(e), yForPrice(e.price));
      ctx.stroke();
    });
    ctx.setLineDash([]);
    return;
  }

  if (drawing.type === "pitchfork" && pts.length >= 2) {
    const [p1, p2, p3] = pts;
    renderPitchforkLines(ctx, p1, p2, p3 || p2, stroke);
    return;
  }

  if (drawing.type === "schiff_pitchfork" && pts.length >= 2) {
    const [p1, p2, p3] = pts;
    const handle = { time: (p1.time + p2.time) / 2, price: (p1.price + p2.price) / 2 };
    renderPitchforkLines(ctx, handle, p2, p3 || p2, stroke);
    return;
  }

  if (drawing.type === "modified_schiff_pitchfork" && pts.length >= 2) {
    const [p1, p2, p3] = pts;
    const schiffHandle = { time: (p1.time + p2.time) / 2, price: (p1.price + p2.price) / 2 };
    const handle = { time: (p1.time + schiffHandle.time) / 2, price: (p1.price + schiffHandle.price) / 2 };
    renderPitchforkLines(ctx, handle, p2, p3 || p2, stroke);
    return;
  }

  if (drawing.type === "inside_pitchfork" && pts.length >= 2) {
    const [p1, p2, p3] = pts;
    if (!p3) {
      renderPitchforkLines(ctx, p1, p2, p2, stroke);
      return;
    }
    renderPitchforkLines(ctx, p3, p1, p2, stroke);
    return;
  }

  if (drawing.type === "text" && pts[0]) {
    const p = pts[0];
    const x = xForPoint(p);
    const y = yForPrice(p.price);
    ctx.font = "12px Segoe UI, sans-serif";
    const textWidth = ctx.measureText(drawing.text).width;
    ctx.fillStyle = "rgba(3, 8, 14, 0.85)";
    ctx.fillRect(x - 4, y - 14, textWidth + 8, 18);
    ctx.strokeStyle = "rgba(190, 213, 230, 0.24)";
    ctx.strokeRect(x - 4, y - 14, textWidth + 8, 18);
    ctx.fillStyle = stroke;
    ctx.textAlign = "left";
    ctx.fillText(drawing.text, x, y);
  }
}

function drawAnnotations(ctx) {
  const { padding, chartWidth, chartHeight } = chartState;
  ctx.save();
  ctx.beginPath();
  ctx.rect(padding.left, padding.top, chartWidth, chartHeight);
  ctx.clip();

  currentDrawings().forEach((drawing) => renderDrawing(ctx, drawing));

  if (pendingDrawing && previewPoint) {
    renderDrawing(ctx, { ...pendingDrawing, points: [...pendingDrawing.points, previewPoint] }, true);
  }

  ctx.restore();
  ctx.setLineDash([]);
}

function zoomChart(event) {
  if (!chartState || !chartState.bars.length || !chartState.padding) return;
  event.preventDefault();

  const canvas = document.querySelector("#candlestick-chart");
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const x = (event.clientX - rect.left) * scaleX;
  const { padding, chartWidth, candleStep, view, bars } = chartState;
  const relX = Math.max(0, Math.min(chartWidth, x - padding.left));
  const hoverIndex = view.start + relX / candleStep;

  const currentCount = view.end - view.start;
  const zoomFactor = event.deltaY < 0 ? 0.85 : 1 / 0.85;
  const minCount = Math.min(MIN_VISIBLE_CANDLES, bars.length);
  const maxCount = bars.length;
  const newCount = Math.round(Math.max(minCount, Math.min(maxCount, currentCount * zoomFactor)));

  const ratio = (hoverIndex - view.start) / currentCount;
  let newStart = Math.round(hoverIndex - ratio * newCount);
  let newEnd = newStart + newCount;

  if (newStart < 0) {
    newStart = 0;
    newEnd = newCount;
  }
  if (newEnd > bars.length) {
    newEnd = bars.length;
    newStart = newEnd - newCount;
  }

  chartState.view = { start: newStart, end: newEnd };
  renderChart();
}

let panState = null;

function startPan(event) {
  if (activeDrawTool !== "cursor") return;
  if (!chartState || !chartState.bars.length) return;
  panState = { startX: event.clientX, view: { ...chartState.view } };
  document.querySelector("#candlestick-chart").classList.add("panning");
}

function panChart(event) {
  if (!panState || !chartState || !chartState.candleStep) return;
  const canvas = document.querySelector("#candlestick-chart");
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const dx = (event.clientX - panState.startX) * scaleX;
  const count = panState.view.end - panState.view.start;
  const shift = Math.round(-dx / chartState.candleStep);
  let newStart = panState.view.start + shift;
  let newEnd = newStart + count;

  if (newStart < 0) {
    newStart = 0;
    newEnd = count;
  }
  if (newEnd > chartState.bars.length) {
    newEnd = chartState.bars.length;
    newStart = newEnd - count;
  }

  chartState.view = { start: newStart, end: newEnd };
  renderChart();
}

function endPan() {
  if (!panState) return;
  panState = null;
  document.querySelector("#candlestick-chart").classList.remove("panning");
}

function showChartTooltip(event) {
  if (panState) return;
  if (!chartState || !chartState.padding || !chartState.visibleBars || !chartState.visibleBars.length) return;

  const canvas = document.querySelector("#candlestick-chart");
  const tooltip = document.querySelector("#chart-tooltip");
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const x = (event.clientX - rect.left) * scaleX;
  const y = (event.clientY - rect.top) * scaleY;
  const { padding, chartWidth, chartHeight, candleStep, visibleBars } = chartState;

  if (x < padding.left || x > padding.left + chartWidth || y < padding.top || y > padding.top + chartHeight) {
    tooltip.hidden = true;
    renderChart();
    return;
  }

  const index = Math.max(0, Math.min(visibleBars.length - 1, Math.floor((x - padding.left) / candleStep)));
  const bar = visibleBars[index];
  const candleX = padding.left + index * candleStep + candleStep / 2;
  renderChart();

  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "rgba(238, 246, 251, 0.38)";
  ctx.beginPath();
  ctx.moveTo(candleX, padding.top);
  ctx.lineTo(candleX, padding.top + chartHeight);
  ctx.stroke();

  tooltip.hidden = false;
  tooltip.innerHTML = `
    <strong>${escapeHtml(chartState.symbol)}</strong>
    <span>${new Date(bar.timestamp).toLocaleString()}</span>
    <span>O $${Number(bar.open).toFixed(4)}</span>
    <span>H $${Number(bar.high).toFixed(4)}</span>
    <span>L $${Number(bar.low).toFixed(4)}</span>
    <span>C $${Number(bar.close).toFixed(4)}</span>
    <span>Vol ${Number(bar.volume).toLocaleString()}</span>
  `;

  const left = Math.min(rect.width - 168, Math.max(8, event.clientX - rect.left + 14));
  const top = Math.min(rect.height - 152, Math.max(8, event.clientY - rect.top + 14));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideChartTooltip() {
  const tooltip = document.querySelector("#chart-tooltip");
  tooltip.hidden = true;
  if (chartState) renderChart();
}

function renderScanner(results) {
  const body = document.querySelector("#scanner-results");
  if (!results.length) {
    body.innerHTML = `<tr><td colspan="9">No candidates found</td></tr>`;
    return;
  }
  body.innerHTML = results.map((result) => `
    <tr title="${escapeHtml([...result.reasons, result.news_headline].filter(Boolean).join(" | "))}">
      <td>${escapeHtml(result.symbol)}</td>
      <td>$${Number(result.price).toFixed(2)}</td>
      <td class="${result.percent_change >= 0 ? "up" : "down"}">${Number(result.percent_change).toFixed(2)}%</td>
      <td>${result.relative_volume === null ? "-" : `${Number(result.relative_volume).toFixed(2)}x`}</td>
      <td>${Number(result.total_volume).toLocaleString()}</td>
      <td>${result.float_shares ? compactNumber(result.float_shares) : "-"}</td>
      <td>${result.has_news ? "News" : result.sector ? escapeHtml(result.sector) : "-"}</td>
      <td>${result.score}/5</td>
      <td>${escapeHtml(result.signal)}</td>
    </tr>
  `).join("");
}

function compactNumber(value) {
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function renderPositions(positions) {
  const body = document.querySelector("#positions-results");
  if (!positions.length) {
    body.innerHTML = `<tr><td colspan="9">No open positions yet — place a trade to see it here.</td></tr>`;
    return;
  }
  body.innerHTML = positions.map((position) => {
    const pnlClass = position.unrealized_pl >= 0 ? "up" : "down";
    const todayClass = position.change_today >= 0 ? "up" : "down";
    return `
    <tr>
      <td>${escapeHtml(position.symbol)}</td>
      <td>${escapeHtml(position.side)}</td>
      <td>${Number(position.qty)}</td>
      <td>$${Number(position.avg_entry_price).toFixed(2)}</td>
      <td>$${Number(position.current_price).toFixed(2)}</td>
      <td>$${Number(position.market_value).toFixed(2)}</td>
      <td class="${pnlClass}">${position.unrealized_pl >= 0 ? "+" : ""}$${Number(position.unrealized_pl).toFixed(2)}</td>
      <td class="${pnlClass}">${position.unrealized_plpc >= 0 ? "+" : ""}${(Number(position.unrealized_plpc) * 100).toFixed(2)}%</td>
      <td class="${todayClass}">${position.change_today >= 0 ? "+" : ""}${(Number(position.change_today) * 100).toFixed(2)}%</td>
    </tr>
  `;
  }).join("");
}

function formatUsd(value) {
  if (value === null || value === undefined) return "-";
  const num = Number(value);
  const sign = num < 0 ? "-" : "";
  return `${sign}$${Math.abs(num).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function renderBankroll(status) {
  document.querySelector("#bankroll-balance").textContent = formatUsd(status.bankroll_balance);
  document.querySelector("#bankroll-deployed").textContent = formatUsd(status.deployed_capital);
  document.querySelector("#bankroll-available").textContent = formatUsd(status.available_to_trade);

  const pnlEl = document.querySelector("#bankroll-pnl");
  pnlEl.textContent = formatUsd(status.realized_pnl);
  pnlEl.className = status.realized_pnl >= 0 ? "up" : "down";

  document.querySelector("#bankroll-savings").textContent =
    status.savings_balance === null ? status.savings_unavailable_reason || "Unavailable" : formatUsd(status.savings_balance);

  const list = document.querySelector("#bankroll-transactions");
  if (!status.transactions.length) {
    list.innerHTML = "<li class=\"txn-empty\">No transactions yet.</li>";
    return;
  }
  list.innerHTML = status.transactions
    .map((txn) => {
      const isWithdrawal = txn.kind === "withdrawal";
      const when = new Date(txn.created_at).toLocaleString();
      const desc = isWithdrawal ? "Withdrawal to bankroll" : "Return to savings";
      const note = txn.note ? ` — ${escapeHtml(txn.note)}` : "";
      return `<li class="${isWithdrawal ? "up" : "down"}">
        <span class="txn-icon" aria-hidden="true">${isWithdrawal ? "&darr;" : "&uarr;"}</span>
        <span class="txn-detail">
          <span class="txn-desc">${desc}${note}</span>
          <span class="txn-date">${when}</span>
        </span>
        <span class="txn-amount">${isWithdrawal ? "+" : "-"}${formatUsd(txn.amount)}</span>
      </li>`;
    })
    .join("");
}

async function refreshBankroll() {
  const status = await api("/api/bankroll");
  renderBankroll(status);
}

async function refreshPositions() {
  const response = await api("/api/positions");
  renderPositions(response.positions);
}

const TRADE_STATUS_CLASS = {
  filled: "up",
  canceled: "down",
  rejected: "down",
  expired: "down",
};

function renderTradeHistory(trades) {
  const body = document.querySelector("#trade-history-results");
  if (!trades.length) {
    body.innerHTML = `<tr><td colspan="12">No trades submitted yet.</td></tr>`;
    return;
  }
  body.innerHTML = trades.map((trade) => {
    const statusClass = TRADE_STATUS_CLASS[trade.status] || "";
    const pnlClass = trade.realized_pnl === null || trade.realized_pnl === undefined ? "" : trade.realized_pnl >= 0 ? "up" : "down";
    return `
    <tr>
      <td>${new Date(trade.submitted_at).toLocaleString()}</td>
      <td>${escapeHtml(trade.symbol)}</td>
      <td>${escapeHtml(trade.side)}</td>
      <td>${Number(trade.qty)}</td>
      <td class="${statusClass}">${escapeHtml(trade.status)}</td>
      <td>${trade.stop_loss_price !== null && trade.stop_loss_price !== undefined ? `$${Number(trade.stop_loss_price).toFixed(2)}` : "-"}</td>
      <td>${trade.take_profit_price !== null && trade.take_profit_price !== undefined ? `$${Number(trade.take_profit_price).toFixed(2)}` : "-"}</td>
      <td>${trade.filled_avg_price !== null ? `$${Number(trade.filled_avg_price).toFixed(2)}` : "-"}</td>
      <td>${trade.filled_at ? new Date(trade.filled_at).toLocaleString() : "-"}</td>
      <td>${trade.exit_price !== null && trade.exit_price !== undefined ? `$${Number(trade.exit_price).toFixed(2)}` : "-"}</td>
      <td>${trade.exit_reason ? escapeHtml(trade.exit_reason) : "-"}</td>
      <td class="${pnlClass}">${trade.realized_pnl !== null && trade.realized_pnl !== undefined ? `$${Number(trade.realized_pnl).toFixed(2)}` : "-"}</td>
    </tr>
  `;
  }).join("");
}

async function refreshTradeHistory() {
  const response = await api("/api/trades/history?limit=50");
  renderTradeHistory(response.trades);
}

async function syncTradeHistory() {
  const response = await api("/api/trades/history/sync", { method: "POST" });
  renderTradeHistory(response.trades);
}

function renderBacktestChart(points) {
  const canvas = document.querySelector("#backtest-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const padding = { top: 20, right: 70, bottom: 30, left: 16 };
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(4, 11, 19, 0.5)";
  ctx.fillRect(0, 0, width, height);

  if (points.length < 2) {
    ctx.fillStyle = "#a8b9c8";
    ctx.font = "14px Segoe UI, sans-serif";
    ctx.fillText("No simulated trades that day", padding.left, height / 2);
    return;
  }

  const equities = points.map((point) => point.equity);
  const maxEquity = Math.max(...equities);
  const minEquity = Math.min(...equities);
  const range = maxEquity - minEquity || 1;
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const xFor = (index) => padding.left + (index / (points.length - 1)) * chartWidth;
  const yFor = (equity) => padding.top + ((maxEquity - equity) / range) * chartHeight;

  const isUp = equities[equities.length - 1] >= equities[0];
  const lineColor = isUp ? "#7cf0b3" : "#ff9b9b";

  ctx.strokeStyle = "rgba(190, 213, 230, 0.14)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = padding.top + (chartHeight / 3) * i;
    const value = maxEquity - (range / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    ctx.fillStyle = "#a8b9c8";
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillText(`$${value.toFixed(0)}`, width - padding.right + 8, y + 4);
  }

  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xFor(index);
    const y = yFor(point.equity);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.lineTo(xFor(points.length - 1), padding.top + chartHeight);
  ctx.lineTo(xFor(0), padding.top + chartHeight);
  ctx.closePath();
  ctx.fillStyle = isUp ? "rgba(124, 240, 179, 0.12)" : "rgba(255, 155, 155, 0.12)";
  ctx.fill();
}

function renderBacktestTrades(trades) {
  const body = document.querySelector("#backtest-trades");
  if (!trades.length) {
    body.innerHTML = `<tr><td colspan="9">No simulated trades that day.</td></tr>`;
    return;
  }
  body.innerHTML = trades.map((trade) => {
    const pnlClass = trade.pnl >= 0 ? "up" : "down";
    return `
    <tr>
      <td>${escapeHtml(trade.symbol)}</td>
      <td>${new Date(trade.entry_time).toLocaleString()}</td>
      <td>$${Number(trade.entry_price).toFixed(2)}</td>
      <td>${new Date(trade.exit_time).toLocaleString()}</td>
      <td>$${Number(trade.exit_price).toFixed(2)}</td>
      <td>${Number(trade.qty)}</td>
      <td class="${pnlClass}">${trade.pnl >= 0 ? "+" : ""}$${Number(trade.pnl).toFixed(2)}</td>
      <td class="${pnlClass}">${trade.pnl_pct >= 0 ? "+" : ""}${Number(trade.pnl_pct).toFixed(2)}%</td>
      <td>${escapeHtml(trade.exit_reason)}</td>
    </tr>
  `;
  }).join("");
}

function renderBacktestCandidates(candidates) {
  document.querySelector("#backtest-candidates-panel").hidden = !candidates.length;
  const body = document.querySelector("#backtest-candidates");
  body.innerHTML = candidates.map((candidate) => `
    <tr>
      <td>${escapeHtml(candidate.symbol)}</td>
      <td>${candidate.score}/5</td>
      <td class="${candidate.qualified ? "up" : "down"}">${candidate.qualified ? "Yes" : "No"}</td>
      <td>${candidate.reasons.map(escapeHtml).join("; ")}</td>
    </tr>
  `).join("");
}

function renderBacktestResult(result) {
  document.querySelector("#backtest-results").hidden = false;
  document.querySelector("#bt-equity").textContent = `$${Number(result.ending_equity).toFixed(2)}`;

  const returnEl = document.querySelector("#bt-return");
  const returnClass = result.total_return_pct >= 0 ? "up" : "down";
  returnEl.textContent = `${result.total_return_pct >= 0 ? "+" : ""}${Number(result.total_return_pct).toFixed(2)}%`;
  returnEl.className = returnClass;

  document.querySelector("#bt-trades").textContent = result.trade_count;
  document.querySelector("#bt-winrate").textContent = `${Number(result.win_rate_pct).toFixed(1)}%`;

  const bestEl = document.querySelector("#bt-best");
  bestEl.textContent = `${result.best_trade_pct >= 0 ? "+" : ""}${Number(result.best_trade_pct).toFixed(2)}%`;
  bestEl.className = result.best_trade_pct >= 0 ? "up" : "down";

  const worstEl = document.querySelector("#bt-worst");
  worstEl.textContent = `${result.worst_trade_pct >= 0 ? "+" : ""}${Number(result.worst_trade_pct).toFixed(2)}%`;
  worstEl.className = result.worst_trade_pct >= 0 ? "up" : "down";

  document.querySelector("#bt-scanned").textContent = result.symbols_scanned;
  document.querySelector("#bt-qualified").textContent = result.symbols_qualified;

  renderBacktestChart(result.equity_curve);
  renderBacktestTrades(result.trades);
  renderBacktestCandidates(result.candidates);
}

async function runBacktest(payload) {
  return api("/api/backtest", { method: "POST", body: JSON.stringify(payload) });
}

let performancePeriod = "1W";
let performanceTimeframe = "1H";

function meaningfulEquityPoints(history) {
  const points = history.timestamp
    .map((timestamp, index) => ({ timestamp, equity: history.equity[index] }))
    .filter((point) => point.equity !== null && point.equity !== undefined);
  // Alpaca leaves equity at 0 for snapshots recorded before any account activity ever happened —
  // strip that leading placeholder run rather than plotting/reporting a false $0 balance.
  const firstRealIndex = points.findIndex((point) => point.equity !== 0);
  return firstRealIndex === -1 ? [] : points.slice(firstRealIndex);
}

function renderPerformanceChart(points) {
  const canvas = document.querySelector("#performance-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const padding = { top: 20, right: 70, bottom: 30, left: 16 };
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(4, 11, 19, 0.5)";
  ctx.fillRect(0, 0, width, height);

  if (points.length < 2) {
    ctx.fillStyle = "#a8b9c8";
    ctx.font = "14px Segoe UI, sans-serif";
    ctx.fillText("No trading history yet — place a trade to start tracking performance", padding.left, height / 2);
    return;
  }

  const equities = points.map((point) => point.equity);
  const maxEquity = Math.max(...equities);
  const minEquity = Math.min(...equities);
  const range = maxEquity - minEquity || 1;
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const xFor = (index) => padding.left + (index / (points.length - 1)) * chartWidth;
  const yFor = (equity) => padding.top + ((maxEquity - equity) / range) * chartHeight;

  const first = equities[0];
  const last = equities[equities.length - 1];
  const isUp = last >= first;
  const lineColor = isUp ? "#7cf0b3" : "#ff9b9b";

  ctx.strokeStyle = "rgba(190, 213, 230, 0.14)";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = padding.top + (chartHeight / 3) * i;
    const value = maxEquity - (range / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    ctx.fillStyle = "#a8b9c8";
    ctx.font = "11px Segoe UI, sans-serif";
    ctx.fillText(`$${value.toFixed(0)}`, width - padding.right + 8, y + 4);
  }

  ctx.beginPath();
  points.forEach((point, index) => {
    const x = xFor(index);
    const y = yFor(point.equity);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.lineTo(xFor(points.length - 1), padding.top + chartHeight);
  ctx.lineTo(xFor(0), padding.top + chartHeight);
  ctx.closePath();
  ctx.fillStyle = isUp ? "rgba(124, 240, 179, 0.12)" : "rgba(255, 155, 155, 0.12)";
  ctx.fill();
}

async function refreshPerformance() {
  const history = await api(`/api/portfolio/history?period=${performancePeriod}&timeframe=${performanceTimeframe}`);
  const points = meaningfulEquityPoints(history);
  renderPerformanceChart(points);

  const equityEl = document.querySelector("#perf-equity");
  const changeEl = document.querySelector("#perf-change");
  const changePctEl = document.querySelector("#perf-change-pct");

  if (points.length < 2) {
    changeEl.textContent = "-";
    changePctEl.textContent = "-";
    changeEl.className = "";
    changePctEl.className = "";
    try {
      const account = await api("/api/account");
      equityEl.textContent = `$${Number(account.equity).toFixed(2)}`;
    } catch {
      equityEl.textContent = "-";
    }
    return;
  }

  const equities = points.map((point) => point.equity);
  const first = equities[0];
  const last = equities[equities.length - 1];
  const change = last - first;
  const changePct = first ? (change / first) * 100 : 0;
  const cls = change >= 0 ? "up" : "down";

  equityEl.textContent = `$${last.toFixed(2)}`;
  changeEl.textContent = `${change >= 0 ? "+" : ""}$${change.toFixed(2)}`;
  changeEl.className = cls;
  changePctEl.textContent = `${change >= 0 ? "+" : ""}${changePct.toFixed(2)}%`;
  changePctEl.className = cls;
}

async function scanSymbols(symbolText) {
  const symbols = symbolText.split(",").map((symbol) => symbol.trim()).filter(Boolean);
  const response = await api("/api/scanner", {
    method: "POST",
    body: JSON.stringify({ symbols }),
  });
  renderScanner(response.results);
}

async function autoScan() {
  document.querySelector("#message").textContent = "Scanning market universe...";
  const response = await api("/api/scanner/auto", {
    method: "POST",
    body: JSON.stringify({ limit: 25, max_symbols: 250 }),
  });
  renderScanner(response.results);
  document.querySelector("#message").textContent = `Auto scan complete: ${response.results.length} candidates ranked`;
}

document.querySelector("#start").addEventListener("click", async () => render(await api("/api/start", { method: "POST" })));
document.querySelector("#stop").addEventListener("click", async () => render(await api("/api/stop", { method: "POST" })));
document.querySelector("#tick").addEventListener("click", async () => render(await api("/api/tick", { method: "POST" })));

document.querySelector("#toggle-auto-trading").addEventListener("click", async (event) => {
  const isOn = event.currentTarget.getAttribute("aria-pressed") === "true";
  if (!isOn) {
    // Auto-trading depends on the bot actually being started — start it first
    // (a no-op if it's already running) so one click is enough.
    await api("/api/start", { method: "POST" });
  }
  render(await api(isOn ? "/api/automation/stop" : "/api/automation/start", { method: "POST" }));
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", async () => {
    switchView(tab.dataset.view);
    if (tab.dataset.view === "settings") await loadSettings();
  });
});

document.querySelector("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;

  // Arming live trading (paper off + allow on) moves REAL money - make it impossible
  // to do by accident. The server independently rejects the save without this flag.
  const armingLive = !form.alpaca_paper.checked && form.allow_live_trading.checked;
  let confirmLive = false;
  if (armingLive) {
    confirmLive = window.confirm(
      "⚠️ You are about to enable LIVE trading with REAL money.\n\n" +
        "Paper mode will be OFF. If auto-trading is running, the bot could place real-money " +
        "orders within a couple of minutes of saving.\n\nAre you absolutely sure?"
    );
    if (!confirmLive) {
      document.querySelector("#message").textContent = "Live trading NOT enabled — settings unchanged.";
      return;
    }
  }

  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  try {
    const settings = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        alpaca_api_key: form.alpaca_api_key.value,
        alpaca_secret_key: form.alpaca_secret_key.value,
        fmp_api_key: form.fmp_api_key.value,
        ntfy_topic: form.ntfy_topic.value,
        alpaca_paper: form.alpaca_paper.checked,
        allow_live_trading: form.allow_live_trading.checked,
        confirm_live_trading: confirmLive,
      }),
    });
    form.alpaca_api_key.value = settings.alpaca_api_key;
    form.alpaca_secret_key.value = settings.alpaca_secret_key;
    form.fmp_api_key.value = settings.fmp_api_key;
    document.querySelector("#message").textContent = "Settings saved";
    await checkApiKeysConfigured();
  } catch (error) {
    document.querySelector("#message").textContent = `Could not save settings: ${error.message}`;
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

document.querySelector("#change-password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submitButton = form.querySelector('button[type="submit"]');

  if (submitButton) submitButton.disabled = true;
  try {
    await api("/api/me/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: form.current_password.value,
        new_password: form.new_password.value,
      }),
    });
    form.reset();
    document.querySelector("#message").textContent = "Password updated.";
  } catch (error) {
    document.querySelector("#message").textContent = `Could not update password: ${error.message}`;
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

function renderUserRow(user) {
  return `
    <li class="user-row" data-user-id="${user.id}">
      <span>${escapeHtml(user.username)}${user.is_admin ? " <em>(admin)</em>" : ""}</span>
      <form class="reset-password-form">
        <input type="password" name="new_password" placeholder="New password" minlength="8" autocomplete="new-password" required />
        <button type="submit">Reset Password</button>
      </form>
    </li>
  `;
}

async function loadUsers() {
  const list = document.querySelector("#users-list");
  try {
    const userList = await api("/api/users");
    list.innerHTML = userList.map(renderUserRow).join("");
  } catch (error) {
    list.innerHTML = `<li>Could not load users: ${escapeHtml(error.message)}</li>`;
  }
}

document.querySelector("#add-user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submitButton = form.querySelector('button[type="submit"]');

  if (submitButton) submitButton.disabled = true;
  try {
    await api("/api/users", {
      method: "POST",
      body: JSON.stringify({
        username: form.username.value,
        password: form.password.value,
        is_admin: form.is_admin.checked,
      }),
    });
    form.reset();
    document.querySelector("#message").textContent = "User added.";
    await loadUsers();
  } catch (error) {
    document.querySelector("#message").textContent = `Could not add user: ${error.message}`;
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

document.querySelector("#users-list").addEventListener("submit", async (event) => {
  if (!event.target.matches(".reset-password-form")) return;
  event.preventDefault();
  const form = event.target;
  const row = form.closest(".user-row");
  const userId = row.dataset.userId;
  const submitButton = form.querySelector('button[type="submit"]');

  if (submitButton) submitButton.disabled = true;
  try {
    await api(`/api/users/${userId}/reset-password`, {
      method: "POST",
      body: JSON.stringify({ new_password: form.new_password.value }),
    });
    form.reset();
    document.querySelector("#message").textContent = "Password reset.";
  } catch (error) {
    document.querySelector("#message").textContent = `Could not reset password: ${error.message}`;
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

document.querySelector("#setup-banner-settings-link").addEventListener("click", async () => {
  document.querySelector('.tab[data-view="settings"]').click();
});

function renderConnectionTestRow(label, result) {
  const icon = result.ok ? "✅" : result.configured ? "❌" : "⚠";
  return `<li class="${result.ok ? "up" : result.configured ? "down" : ""}">${icon} <strong>${escapeHtml(label)}:</strong> ${escapeHtml(result.detail)}</li>`;
}

document.querySelector("#test-connection").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  const results = document.querySelector("#connection-test-results");
  button.disabled = true;
  button.textContent = "Testing...";
  results.hidden = false;
  results.innerHTML = `<li>Testing...</li>`;
  try {
    const response = await api("/api/settings/test");
    results.innerHTML = renderConnectionTestRow("Alpaca", response.alpaca) + renderConnectionTestRow("FMP", response.fmp);
  } catch (error) {
    results.innerHTML = `<li class="down">❌ ${escapeHtml(error.message)}</li>`;
  } finally {
    button.disabled = false;
    button.textContent = "Test Connection";
  }
});

document.querySelector("#trade-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const result = await api("/api/trade", {
      method: "POST",
      body: JSON.stringify({
        symbol: form.get("symbol"),
        qty: Number(form.get("qty")),
        estimated_price: form.get("estimated_price") ? Number(form.get("estimated_price")) : null,
        side: form.get("side"),
        stop_loss_price: form.get("stop_loss_price") ? Number(form.get("stop_loss_price")) : null,
        take_profit_price: form.get("take_profit_price") ? Number(form.get("take_profit_price")) : null,
      }),
    });
    document.querySelector("#message").textContent = `Order ${result.status}: ${result.symbol}`;
    await refresh();
    await refreshTradeHistory();
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#trade-form input[name='symbol']").addEventListener("change", async (event) => {
  try {
    await refreshChart(event.target.value);
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#refresh-chart").addEventListener("click", async () => {
  try {
    await refreshChart();
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

const candlestickChart = document.querySelector("#candlestick-chart");
candlestickChart.addEventListener("wheel", zoomChart, { passive: false });
candlestickChart.addEventListener("mousedown", startPan);
candlestickChart.addEventListener("mousemove", (event) => {
  if (panState) {
    panChart(event);
    return;
  }
  if (pendingDrawing) {
    const { x, y } = eventToCanvasXY(event);
    previewPoint = dataPointFromCanvasXY(x, y);
    renderChart();
    return;
  }
  showChartTooltip(event);
});
candlestickChart.addEventListener("mouseup", endPan);
candlestickChart.addEventListener("mouseleave", () => {
  endPan();
  hideChartTooltip();
});
candlestickChart.addEventListener("click", handleChartClick);
candlestickChart.addEventListener("dblclick", () => {
  if (activeDrawTool === "cursor") resetChartZoom();
});
document.querySelector("#reset-zoom").addEventListener("click", resetChartZoom);

// Fixed-position (not absolute) and placed by JS from the toggle button's real
// viewport position, with its max-height clamped to whatever space is actually
// left below it - a long menu (25 tools) anchored with plain CSS `position:
// absolute` can extend past the bottom of the viewport when the button itself
// isn't near the top of the page, and items below the fold become unreachable
// without scrolling the whole page (the menu's own internal scroll never gets a
// chance to help, since the box itself starts off-screen).
function positionDrawToolMenu() {
  const toggle = document.querySelector("#draw-tool-toggle");
  const menu = document.querySelector("#draw-tool-menu");
  const rect = toggle.getBoundingClientRect();
  const margin = 12;
  menu.style.left = `${Math.round(rect.left)}px`;
  menu.style.top = `${Math.round(rect.bottom + 6)}px`;
  menu.style.maxHeight = `${Math.max(120, Math.round(window.innerHeight - rect.bottom - 6 - margin))}px`;
}

document.querySelector("#draw-tool-toggle").addEventListener("click", () => {
  const menu = document.querySelector("#draw-tool-menu");
  menu.hidden = !menu.hidden;
  if (!menu.hidden) positionDrawToolMenu();
});

document.querySelectorAll(".draw-tool-item").forEach((btn) => {
  btn.addEventListener("click", () => {
    setDrawTool(btn.dataset.tool);
    document.querySelector("#draw-tool-menu").hidden = true;
  });
});

document.addEventListener("click", (event) => {
  const picker = document.querySelector("#draw-tool-picker");
  const menu = document.querySelector("#draw-tool-menu");
  if (picker && !menu.hidden && !picker.contains(event.target)) menu.hidden = true;
});

document.querySelector("#erase-tool").addEventListener("click", () => setDrawTool("erase"));

document.querySelector("#draw-color").addEventListener("input", (event) => {
  activeDrawColor = event.target.value;
});

document.querySelector("#clear-drawings").addEventListener("click", () => {
  if (!chartState) return;
  drawingsBySymbol[chartState.symbol] = [];
  renderChart();
});

function resizeCandlestickCanvas() {
  const canvas = document.querySelector("#candlestick-chart");
  const wrap = document.querySelector(".chart-wrap");
  const panel = document.querySelector("#section-chart");
  if (panel.classList.contains("expanded")) {
    const rect = wrap.getBoundingClientRect();
    canvas.width = Math.max(320, Math.round(rect.width));
    canvas.height = Math.max(240, Math.round(rect.height));
  } else {
    canvas.width = 1040;
    canvas.height = 360;
  }
  if (chartState) renderChart();
}

function toggleChartExpanded() {
  const panel = document.querySelector("#section-chart");
  const backdrop = document.querySelector("#chart-expand-backdrop");
  const button = document.querySelector("#toggle-chart-expand");
  const expanding = !panel.classList.contains("expanded");
  panel.classList.toggle("expanded", expanding);
  backdrop.hidden = !expanding;
  button.classList.toggle("active", expanding);
  button.textContent = expanding ? "Collapse" : "Expand";
  // Let the layout settle into its new (fixed-position full-viewport, or normal
  // in-flow) size before measuring it for the canvas resize.
  requestAnimationFrame(resizeCandlestickCanvas);
}

document.querySelector("#toggle-chart-expand").addEventListener("click", toggleChartExpanded);
document.querySelector("#chart-expand-backdrop").addEventListener("click", toggleChartExpanded);
window.addEventListener("resize", () => {
  if (document.querySelector("#section-chart").classList.contains("expanded")) resizeCandlestickCanvas();
});

document.querySelector("#start-replay").addEventListener("click", armReplaySelection);
document.querySelector("#replay-step-back").addEventListener("click", () => stepReplay(-1));
document.querySelector("#replay-step-forward").addEventListener("click", () => stepReplay(1));
document.querySelector("#replay-play-pause").addEventListener("click", toggleReplayPlay);
document.querySelector("#exit-replay").addEventListener("click", exitReplay);
document.querySelector("#replay-speed").addEventListener("change", () => {
  if (replayState && replayState.playing) {
    pauseReplay();
    playReplay(); // restart the interval so the new speed takes effect immediately
  }
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && pendingDrawing) cancelPendingDrawing();
  if (event.key === "Escape" && awaitingReplayStart) stopReplay();
  if (event.key === "Escape" && replayState) exitReplay();
  if (event.key === "Escape" && !document.querySelector("#draw-tool-menu").hidden) {
    document.querySelector("#draw-tool-menu").hidden = true;
  }
  if (event.key === "Escape" && document.querySelector("#section-chart").classList.contains("expanded")) {
    toggleChartExpanded();
  }
});

document.querySelectorAll("#range-buttons .range-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    setChartRange(btn);
    try {
      await refreshChart();
    } catch (error) {
      document.querySelector("#message").textContent = error.message;
    }
  });
});

document.querySelector("#chart-date").addEventListener("change", async () => {
  document.querySelectorAll("#range-buttons .range-btn").forEach((btn) => btn.classList.remove("active"));
  try {
    await refreshChart();
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#quote-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    await refreshQuote(String(form.get("symbol") || DEFAULT_SYMBOL));
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#scanner-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const symbolsText = String(form.get("symbols") || "").trim();
  try {
    // An empty box means "scan everything" - same as the Auto Scan button, instead
    // of a silent empty result.
    if (symbolsText) {
      await scanSymbols(symbolsText);
    } else {
      await autoScan();
    }
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#auto-scan").addEventListener("click", async () => {
  try {
    await autoScan();
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#backtest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const statusEl = document.querySelector("#backtest-status");
  const submitBtn = document.querySelector("#run-backtest");
  const symbolsText = (form.get("symbols") || "").trim();
  statusEl.textContent = symbolsText
    ? "Running backtest..."
    : "Running backtest against your live universe — this scans every symbol in it...";
  submitBtn.disabled = true;
  try {
    const result = await runBacktest({
      day: form.get("day"),
      symbols: symbolsText ? symbolsText.split(",").map((s) => s.trim()).filter(Boolean) : null,
      starting_capital: Number(form.get("starting_capital")),
      position_value: Number(form.get("position_value")),
    });
    renderBacktestResult(result);
    statusEl.textContent = `Done — scanned ${result.symbols_scanned} symbols, ${result.symbols_qualified} qualified, ${result.symbols_traded} traded.`;
  } catch (error) {
    statusEl.textContent = error.message;
  } finally {
    submitBtn.disabled = false;
  }
});

document.querySelector("#bankroll-withdraw-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  try {
    const status = await api("/api/bankroll/withdraw", {
      method: "POST",
      body: JSON.stringify({ amount: Number(form.amount.value) }),
    });
    renderBankroll(status);
    form.reset();
    document.querySelector("#message").textContent = "Withdrawal added to your trading bankroll.";
  } catch (error) {
    document.querySelector("#message").textContent = `Could not withdraw: ${error.message}`;
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

document.querySelector("#bankroll-return-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submitButton = form.querySelector('button[type="submit"]');
  if (submitButton) submitButton.disabled = true;
  try {
    const status = await api("/api/bankroll/return", {
      method: "POST",
      body: JSON.stringify({ amount: Number(form.amount.value) }),
    });
    renderBankroll(status);
    form.reset();
    document.querySelector("#message").textContent = "Moved back to savings.";
  } catch (error) {
    document.querySelector("#message").textContent = `Could not return funds: ${error.message}`;
  } finally {
    if (submitButton) submitButton.disabled = false;
  }
});

document.querySelector("#refresh-positions").addEventListener("click", async () => {
  try {
    await refreshPositions();
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelector("#sync-trade-history").addEventListener("click", async () => {
  try {
    await syncTradeHistory();
  } catch (error) {
    document.querySelector("#message").textContent = error.message;
  }
});

document.querySelectorAll("#performance-range-buttons .range-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    performancePeriod = btn.dataset.period;
    performanceTimeframe = btn.dataset.timeframe;
    document.querySelectorAll("#performance-range-buttons .range-btn").forEach((other) => {
      other.classList.toggle("active", other === btn);
    });
    try {
      await refreshPerformance();
    } catch (error) {
      document.querySelector("#message").textContent = error.message;
    }
  });
});

updateMarketClock();
setInterval(updateMarketClock, 1000);

checkApiKeysConfigured();
refresh();
refreshQuote().catch((error) => {
  document.querySelector("#message").textContent = error.message;
});
refreshChart().catch((error) => {
  document.querySelector("#message").textContent = error.message;
});
scanSymbols("AAPL, TSLA, NVDA, AMD, PLTR").catch((error) => {
  document.querySelector("#message").textContent = error.message;
});
refreshPositions().catch((error) => {
  document.querySelector("#message").textContent = error.message;
});
refreshBankroll().catch((error) => {
  document.querySelector("#message").textContent = error.message;
});
refreshPerformance().catch((error) => {
  document.querySelector("#message").textContent = error.message;
});
refreshTradeHistory().catch((error) => {
  document.querySelector("#message").textContent = error.message;
});

// Live-data panels poll every 20s (paused while the tab is hidden). The chart and
// scanner deliberately don't auto-refresh: reloading the chart would reset zoom/pan/
// in-progress drawings, and the scanner is an on-demand research tool, not a feed.
const LIVE_REFRESH_INTERVAL_MS = 20000;
startAutoRefresh(refresh, LIVE_REFRESH_INTERVAL_MS);
startAutoRefresh(refreshPositions, LIVE_REFRESH_INTERVAL_MS);
startAutoRefresh(refreshBankroll, LIVE_REFRESH_INTERVAL_MS);
startAutoRefresh(refreshPerformance, LIVE_REFRESH_INTERVAL_MS);
startAutoRefresh(() => refreshQuote(getTickerSymbol()), LIVE_REFRESH_INTERVAL_MS);

// Trade history sync is heavier than the other polls above — it makes one Alpaca
// call per still-open order to check for fills/exits — so it runs on a slower cadence.
const TRADE_HISTORY_SYNC_INTERVAL_MS = 45000;
startAutoRefresh(syncTradeHistory, TRADE_HISTORY_SYNC_INTERVAL_MS);

document.querySelector("#open-wizard").addEventListener("click", openWizard);
document.querySelector("#wizard-close").addEventListener("click", closeWizard);
document.querySelector("#wizard-overlay").addEventListener("click", (event) => {
  if (event.target.id === "wizard-overlay") closeWizard();
});
document.querySelector("#wizard-back").addEventListener("click", () => {
  if (wizardStep > 0) {
    wizardStep -= 1;
    renderWizardStep();
  }
});
document.querySelector("#wizard-next").addEventListener("click", () => {
  if (wizardStep < WIZARD_STEPS.length - 1) {
    wizardStep += 1;
    renderWizardStep();
  } else {
    closeWizard();
  }
});
window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !document.querySelector("#wizard-overlay").hidden) closeWizard();
});

if (!localStorage.getItem(WIZARD_DISMISS_KEY)) {
  openWizard();
}

const sectionNavLinks = document.querySelectorAll(".section-nav-link");
if (sectionNavLinks.length) {
  const sections = Array.from(sectionNavLinks)
    .map((link) => document.getElementById(link.getAttribute("href").slice(1)))
    .filter(Boolean);

  const setActiveSectionLink = (id) => {
    sectionNavLinks.forEach((link) => {
      link.classList.toggle("active", link.getAttribute("href") === `#${id}`);
    });
  };

  const sectionObserver = new IntersectionObserver(
    (entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting);
      if (!visible.length) return;
      const topMost = visible.reduce((a, b) => (a.boundingClientRect.top < b.boundingClientRect.top ? a : b));
      setActiveSectionLink(topMost.target.id);
    },
    { rootMargin: "-80px 0px -70% 0px", threshold: 0 }
  );

  sections.forEach((section) => sectionObserver.observe(section));
}
