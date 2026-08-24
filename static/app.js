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

const WIZARD_DISMISS_KEY = "rolling_papers_bot_wizard_dismissed";

const WIZARD_STEPS = [
  {
    title: "Welcome to Rolling Papers Bot",
    body: `
      <p>Rolling Papers Bot is a <strong>paper-first</strong> day-trading assistant for stocks, built on FastAPI and Alpaca.</p>
      <p>Paper trading is on by default and live trading is blocked until you explicitly enable it — so it's safe to explore and experiment.</p>
      <p>This wizard walks through connecting your account, reading the chart, placing a trade, and tracking whether it made or lost money. You can reopen it anytime with the <strong>Getting Started</strong> button.</p>
    `,
  },
  {
    title: "Connect your Alpaca account",
    body: `
      <p>Open the <strong>Settings</strong> tab in the top navigation.</p>
      <ul>
        <li>Don't have keys yet? <a href="https://app.alpaca.markets/signup" target="_blank" rel="noopener">Get free Alpaca paper-trading keys</a> — required for quotes, charts, and trading.</li>
        <li>Paste your Alpaca <strong>paper trading</strong> API key and secret key</li>
        <li>Optionally <a href="https://site.financialmodelingprep.com/developer/docs" target="_blank" rel="noopener">get a free FMP key</a> too — it unlocks live float-share data for the scanner (everything else works without it)</li>
        <li>Click <strong>Save Settings</strong>, then <strong>Test Connection</strong> to confirm both keys actually work</li>
      </ul>
      <p>Leave "Allow live trading" unchecked unless you specifically intend to trade real money.</p>
    `,
  },
  {
    title: "Get to know the dashboard",
    body: `
      <ul>
        <li><strong>Status</strong> — bot mode, running state, today's trades and realized P&amp;L</li>
        <li><strong>Market Clock</strong> — live clock plus a countdown to the next market open/close (NYSE hours)</li>
        <li><strong>Alpaca Ticker</strong> — quick bid/ask/mid quote for any symbol</li>
      </ul>
    `,
  },
  {
    title: "Read the candlestick chart",
    body: `
      <ul>
        <li>Switch ranges with <strong>1D / 5D / 10D</strong>, or pick an exact date</li>
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
    `,
  },
  {
    title: "Track what you bought",
    body: `
      <p>This is the part you asked about — after you buy something, here's where to watch it:</p>
      <ul>
        <li><strong>Positions</strong> panel — every open position with entry price, current price, market value, and unrealized profit/loss in $ and %. <span class="up">Green</span> means you're up, <span class="down">red</span> means you're down. Refreshes automatically every 15 seconds.</li>
        <li><strong>Performance</strong> panel — your total account equity over time (1D/1W/1M), with overall change in $ and %, so you can see whether the account as a whole is profitable.</li>
      </ul>
    `,
  },
  {
    title: "Automate it (optional)",
    body: `
      <p>Two ways to run the strategy from the top bar:</p>
      <ul>
        <li><strong>Run Tick</strong> checks one symbol (set by <code>BOT_SYMBOL</code> in <code>.env</code>) and just reports what it sees — nothing gets submitted automatically.</li>
        <li><strong>Auto-Trading</strong> is the real automation: while it's on, the app scans the stock universe every couple of minutes on its own, and for any genuine buy signal, submits a bracket order (stop-loss and take-profit included) without you clicking anything. It's off by default — you turn it on explicitly.</li>
      </ul>
      <p>Safety limits — max daily loss, max trades per day, max position value — apply to every trade Auto-Trading places, exactly like a manual order. And it can only ever place <strong>real</strong>-money trades if you've explicitly turned off paper mode <em>and</em> checked "Allow live trading" in Settings — otherwise every trade, automated or not, stays paper.</p>
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
    form.alpaca_paper.checked = settings.alpaca_paper;
    form.allow_live_trading.checked = settings.allow_live_trading;

    const authForm = document.querySelector("#dashboard-auth-form");
    const hasAuth = Boolean(settings.dashboard_username);
    authForm.new_username.value = settings.dashboard_username || "";
    document.querySelector("#current-password-row").hidden = !hasAuth;
    authForm.current_password.required = hasAuth;
    document.querySelector("#dashboard-auth-hint").textContent = hasAuth
      ? "Login is required to use this dashboard. Enter your current password to change it."
      : "Not set up yet — anyone who can reach this dashboard can use it without logging in. Set a username and password below to require login.";
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
  chartState = { symbol, bars, view: { start: 0, end: bars.length } };
  renderChart();
}

function resetChartZoom() {
  if (!chartState) return;
  chartState.view = { start: 0, end: chartState.bars.length };
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

const DRAWING_POINTS_REQUIRED = {
  trendline: 2,
  horizontal: 1,
  fib: 2,
  channel: 3,
  rectangle: 2,
  text: 1,
};

const DRAW_HINTS = {
  cursor: "Scroll to zoom, drag to pan, double-click to reset zoom.",
  trendline: "Click a start point, then an end point to draw a trend line.",
  horizontal: "Click a price level to draw a horizontal line.",
  fib: "Click the start extreme, then the end extreme for a Fibonacci retracement.",
  channel: "Click two points for the base line, then a third point to set channel width.",
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

function setDrawTool(tool) {
  activeDrawTool = tool;
  pendingDrawing = null;
  previewPoint = null;
  document.querySelectorAll(".draw-btn").forEach((btn) => btn.classList.toggle("active", btn.dataset.tool === tool));
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
  if (!chartState || !chartState.padding || activeDrawTool === "cursor") return;

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
    if (text) currentDrawings().push({ type: "text", points: [point], text });
    renderChart();
    return;
  }

  if (!pendingDrawing || pendingDrawing.type !== activeDrawTool) {
    pendingDrawing = { type: activeDrawTool, points: [] };
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

function hitTestDrawing(drawing, x, y, threshold) {
  if (drawing.type === "horizontal") {
    return Math.abs(y - yForPrice(drawing.points[0].price)) <= threshold;
  }
  if (drawing.type === "trendline") {
    const [p1, p2] = drawing.points;
    return distanceToSegment(x, y, xForPoint(p1), yForPrice(p1.price), xForPoint(p2), yForPrice(p2.price)) <= threshold;
  }
  if (drawing.type === "rectangle") {
    const [p1, p2] = drawing.points;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    const y1 = yForPrice(p1.price);
    const y2 = yForPrice(p2.price);
    return x >= Math.min(x1, x2) && x <= Math.max(x1, x2) && y >= Math.min(y1, y2) && y <= Math.max(y1, y2);
  }
  if (drawing.type === "text") {
    const p = drawing.points[0];
    return Math.abs(x - xForPoint(p)) <= 60 && Math.abs(y - yForPrice(p.price)) <= 14;
  }
  if (drawing.type === "fib") {
    const [p1, p2] = drawing.points;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    if (x < Math.min(x1, x2) || x > Math.max(x1, x2)) return false;
    return [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].some(
      (level) => Math.abs(y - yForPrice(p1.price + (p2.price - p1.price) * level)) <= threshold
    );
  }
  if (drawing.type === "channel") {
    const [p1, p2, p3] = drawing.points;
    if (distanceToSegment(x, y, xForPoint(p1), yForPrice(p1.price), xForPoint(p2), yForPrice(p2.price)) <= threshold) return true;
    if (!p3) return false;
    const offset = channelOffset(p1, p2, p3);
    const o1 = { time: p1.time, price: p1.price + offset };
    const o2 = { time: p2.time, price: p2.price + offset };
    return distanceToSegment(x, y, xForPoint(o1), yForPrice(o1.price), xForPoint(o2), yForPrice(o2.price)) <= threshold;
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

  if (drawing.type === "horizontal" && drawing.points[0]) {
    const y = yForPrice(drawing.points[0].price);
    ctx.strokeStyle = isPreview ? "rgba(255, 209, 102, 0.6)" : "#ffd166";
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(chartState.padding.left, y);
    ctx.lineTo(chartState.padding.left + chartState.chartWidth, y);
    ctx.stroke();
    drawPriceLabel(ctx, drawing.points[0].price, y, ctx.strokeStyle);
    return;
  }

  if (drawing.type === "trendline" && drawing.points.length >= 2) {
    const [p1, p2] = drawing.points;
    ctx.strokeStyle = isPreview ? "rgba(138, 180, 248, 0.6)" : "#8ab4f8";
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(xForPoint(p1), yForPrice(p1.price));
    ctx.lineTo(xForPoint(p2), yForPrice(p2.price));
    ctx.stroke();
    return;
  }

  if (drawing.type === "rectangle" && drawing.points.length >= 2) {
    const [p1, p2] = drawing.points;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    const y1 = yForPrice(p1.price);
    const y2 = yForPrice(p2.price);
    ctx.fillStyle = isPreview ? "rgba(124, 240, 179, 0.08)" : "rgba(124, 240, 179, 0.14)";
    ctx.strokeStyle = isPreview ? "rgba(124, 240, 179, 0.5)" : "#7cf0b3";
    ctx.setLineDash([]);
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    ctx.fillRect(left, top, Math.abs(x2 - x1), Math.abs(y2 - y1));
    ctx.strokeRect(left, top, Math.abs(x2 - x1), Math.abs(y2 - y1));
    return;
  }

  if (drawing.type === "fib" && drawing.points.length >= 2) {
    const [p1, p2] = drawing.points;
    const x1 = xForPoint(p1);
    const x2 = xForPoint(p2);
    const left = Math.min(x1, x2);
    const right = Math.max(x1, x2);
    [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].forEach((level) => {
      const price = p1.price + (p2.price - p1.price) * level;
      const y = yForPrice(price);
      ctx.strokeStyle = isPreview ? "rgba(138, 180, 248, 0.5)" : "rgba(138, 180, 248, 0.85)";
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

  if (drawing.type === "channel" && drawing.points.length >= 2) {
    const [p1, p2, p3] = drawing.points;
    ctx.strokeStyle = isPreview ? "rgba(138, 180, 248, 0.6)" : "#8ab4f8";
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

      ctx.fillStyle = "rgba(138, 180, 248, 0.08)";
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

  if (drawing.type === "text" && drawing.points[0]) {
    const p = drawing.points[0];
    const x = xForPoint(p);
    const y = yForPrice(p.price);
    ctx.font = "12px Segoe UI, sans-serif";
    const textWidth = ctx.measureText(drawing.text).width;
    ctx.fillStyle = "rgba(3, 8, 14, 0.85)";
    ctx.fillRect(x - 4, y - 14, textWidth + 8, 18);
    ctx.strokeStyle = "rgba(190, 213, 230, 0.24)";
    ctx.strokeRect(x - 4, y - 14, textWidth + 8, 18);
    ctx.fillStyle = "#eef6fb";
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
    ctx.fillText("No simulated trades in this range", padding.left, height / 2);
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
    body.innerHTML = `<tr><td colspan="8">No simulated trades in this range.</td></tr>`;
    return;
  }
  body.innerHTML = trades.map((trade) => {
    const pnlClass = trade.pnl >= 0 ? "up" : "down";
    return `
    <tr>
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

  document.querySelector("#bt-scanned").textContent = result.days_scanned;
  document.querySelector("#bt-qualified").textContent = result.days_qualified;

  renderBacktestChart(result.equity_curve);
  renderBacktestTrades(result.trades);
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

document.querySelector("#dashboard-auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const submitButton = form.querySelector('button[type="submit"]');

  if (form.new_password.value !== form.confirm_password.value) {
    document.querySelector("#message").textContent = "New password and confirmation don't match.";
    return;
  }

  if (submitButton) submitButton.disabled = true;
  try {
    await api("/api/settings/dashboard-auth", {
      method: "POST",
      body: JSON.stringify({
        current_password: form.current_password.value,
        new_username: form.new_username.value,
        new_password: form.new_password.value,
      }),
    });
    form.current_password.value = "";
    form.new_password.value = "";
    form.confirm_password.value = "";
    document.querySelector("#message").textContent = "Dashboard login updated — you'll be asked to log in again with your new credentials.";
    await loadSettings();
  } catch (error) {
    document.querySelector("#message").textContent = `Could not update dashboard login: ${error.message}`;
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

document.querySelectorAll(".draw-btn").forEach((btn) => {
  btn.addEventListener("click", () => setDrawTool(btn.dataset.tool));
});

document.querySelector("#clear-drawings").addEventListener("click", () => {
  if (!chartState) return;
  drawingsBySymbol[chartState.symbol] = [];
  renderChart();
});

window.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && pendingDrawing) cancelPendingDrawing();
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
  try {
    await scanSymbols(String(form.get("symbols") || ""));
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
  statusEl.textContent = "Running backtest — this can take a little while for longer ranges...";
  submitBtn.disabled = true;
  try {
    const result = await runBacktest({
      symbol: form.get("symbol"),
      start: form.get("start"),
      end: form.get("end"),
      starting_capital: Number(form.get("starting_capital")),
      position_value: Number(form.get("position_value")),
    });
    renderBacktestResult(result);
    statusEl.textContent = `Done — scanned ${result.days_scanned} trading days, ${result.days_qualified} met the stock-selection gate.`;
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
