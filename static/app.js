async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

function render(status) {
  document.querySelector("#message").textContent = status.last_message;
  document.querySelector("#mode").textContent = status.paper ? "Paper" : "Live";
  document.querySelector("#running").textContent = status.running ? "Yes" : "No";
  document.querySelector("#symbol").textContent = status.symbol;
  document.querySelector("#signal").textContent = status.last_signal;
  document.querySelector("#trades").textContent = status.trades_today;
  document.querySelector("#pnl").textContent = `$${Number(status.realized_pnl_today).toFixed(2)}`;
}

async function refresh() {
  render(await api("/api/status"));
}

function switchView(name) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === `${name}-view`));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === name));
}

async function loadSettings() {
  const settings = await api("/api/settings");
  const form = document.querySelector("#settings-form");
  form.alpaca_api_key.value = settings.alpaca_api_key;
  form.alpaca_secret_key.value = settings.alpaca_secret_key;
  form.fmp_api_key.value = settings.fmp_api_key;
  form.alpaca_paper.checked = settings.alpaca_paper;
  form.allow_live_trading.checked = settings.allow_live_trading;
}

function renderQuote(quote) {
  document.querySelector("#quote-bid").textContent = quote.bid_price ? `$${Number(quote.bid_price).toFixed(2)}` : "-";
  document.querySelector("#quote-ask").textContent = quote.ask_price ? `$${Number(quote.ask_price).toFixed(2)}` : "-";
  document.querySelector("#quote-mid").textContent = quote.midpoint ? `$${Number(quote.midpoint).toFixed(2)}` : "-";
  document.querySelector("#quote-time").textContent = quote.timestamp ? new Date(quote.timestamp).toLocaleTimeString() : "-";
}

async function refreshQuote(symbol = "AAPL") {
  const quote = await api(`/api/quote/${encodeURIComponent(symbol.toUpperCase())}`);
  renderQuote(quote);
}

async function refreshChart(symbol = getManualSymbol()) {
  const selectedDate = document.querySelector("#chart-date").value;
  const params = new URLSearchParams({ limit: selectedDate ? "390" : "120" });
  if (selectedDate) params.set("trading_date", selectedDate);
  const response = await api(`/api/bars/${encodeURIComponent(symbol.toUpperCase())}?${params.toString()}`);
  drawCandlestickChart(response.symbol, response.bars);
}

let chartState = null;

function getManualSymbol() {
  return document.querySelector("#trade-form input[name='symbol']").value || "AAPL";
}

function drawCandlestickChart(symbol, bars) {
  const canvas = document.querySelector("#candlestick-chart");
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  const padding = { top: 34, right: 70, bottom: 34, left: 54 };
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "rgba(4, 11, 19, 0.5)";
  ctx.fillRect(0, 0, width, height);

  ctx.fillStyle = "#eef6fb";
  ctx.font = "16px Segoe UI, sans-serif";
  ctx.fillText(`${symbol} 1-minute candles`, padding.left, 24);

  if (!bars.length) {
    chartState = null;
    ctx.fillStyle = "#a8b9c8";
    ctx.fillText("No recent candle data available", padding.left, height / 2);
    return;
  }

  const highs = bars.map((bar) => bar.high);
  const lows = bars.map((bar) => bar.low);
  const maxPrice = Math.max(...highs);
  const minPrice = Math.min(...lows);
  const priceRange = maxPrice - minPrice || 1;
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const candleGap = 2;
  const candleWidth = Math.max(3, chartWidth / bars.length - candleGap);
  const yFor = (price) => padding.top + ((maxPrice - price) / priceRange) * chartHeight;
  const candleStep = chartWidth / bars.length;

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

  bars.forEach((bar, index) => {
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

  chartState = {
    symbol,
    bars,
    padding,
    chartWidth,
    chartHeight,
    candleStep,
    candleWidth,
    minPrice,
    maxPrice,
  };
}

function showChartTooltip(event) {
  if (!chartState) return;

  const canvas = document.querySelector("#candlestick-chart");
  const tooltip = document.querySelector("#chart-tooltip");
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const x = (event.clientX - rect.left) * scaleX;
  const y = (event.clientY - rect.top) * scaleY;
  const { padding, chartWidth, chartHeight, candleStep, bars } = chartState;

  if (x < padding.left || x > padding.left + chartWidth || y < padding.top || y > padding.top + chartHeight) {
    tooltip.hidden = true;
    drawCandlestickChart(chartState.symbol, bars);
    return;
  }

  const index = Math.max(0, Math.min(bars.length - 1, Math.floor((x - padding.left) / candleStep)));
  const bar = bars[index];
  const candleX = padding.left + index * candleStep + candleStep / 2;
  drawCandlestickChart(chartState.symbol, bars);

  const ctx = canvas.getContext("2d");
  ctx.strokeStyle = "rgba(238, 246, 251, 0.38)";
  ctx.beginPath();
  ctx.moveTo(candleX, padding.top);
  ctx.lineTo(candleX, padding.top + chartHeight);
  ctx.stroke();

  tooltip.hidden = false;
  tooltip.innerHTML = `
    <strong>${chartState.symbol}</strong>
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
  if (chartState) drawCandlestickChart(chartState.symbol, chartState.bars);
}

function renderScanner(results) {
  const body = document.querySelector("#scanner-results");
  if (!results.length) {
    body.innerHTML = `<tr><td colspan="9">No candidates found</td></tr>`;
    return;
  }
  body.innerHTML = results.map((result) => `
    <tr title="${[...result.reasons, result.news_headline].filter(Boolean).join(" | ")}">
      <td>${result.symbol}</td>
      <td>$${Number(result.price).toFixed(2)}</td>
      <td class="${result.percent_change >= 0 ? "up" : "down"}">${Number(result.percent_change).toFixed(2)}%</td>
      <td>${result.relative_volume === null ? "-" : `${Number(result.relative_volume).toFixed(2)}x`}</td>
      <td>${Number(result.total_volume).toLocaleString()}</td>
      <td>${result.float_shares ? compactNumber(result.float_shares) : "-"}</td>
      <td>${result.has_news ? "News" : result.sector ? result.sector : "-"}</td>
      <td>${result.score}/5</td>
      <td>${result.signal}</td>
    </tr>
  `).join("");
}

function compactNumber(value) {
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
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

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", async () => {
    switchView(tab.dataset.view);
    if (tab.dataset.view === "settings") await loadSettings();
  });
});

document.querySelector("#settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const settings = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({
      alpaca_api_key: form.alpaca_api_key.value,
      alpaca_secret_key: form.alpaca_secret_key.value,
      fmp_api_key: form.fmp_api_key.value,
      alpaca_paper: form.alpaca_paper.checked,
      allow_live_trading: form.allow_live_trading.checked,
    }),
  });
  form.alpaca_api_key.value = settings.alpaca_api_key;
  form.alpaca_secret_key.value = settings.alpaca_secret_key;
  form.fmp_api_key.value = settings.fmp_api_key;
  document.querySelector("#message").textContent = "Settings saved";
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
      }),
    });
    document.querySelector("#message").textContent = `Order ${result.status}: ${result.symbol}`;
    await refresh();
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

document.querySelector("#candlestick-chart").addEventListener("mousemove", showChartTooltip);
document.querySelector("#candlestick-chart").addEventListener("mouseleave", hideChartTooltip);

document.querySelector("#chart-date").addEventListener("change", async () => {
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
    await refreshQuote(String(form.get("symbol") || "AAPL"));
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
