// TFA Order Book — front-end.
// Vanilla JS, no build step. The server pushes the full market state over a WebSocket
// (at most 10 times per second); every render function below is a pure function of it.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const ROW_HEIGHT = 21;
const css = getComputedStyle(document.documentElement);
const color = (name) => css.getPropertyValue(name).trim();
const C = {
  up: color("--up"), down: color("--down"), upBg: color("--up-bg"), downBg: color("--down-bg"),
  text: color("--text"), dim: color("--dim"), faint: color("--faint"),
  border: color("--border"), accent: color("--accent"), panel: color("--panel-3"),
};

const ui = {
  data: null,
  me: loadTrader(),
  side: "buy",
  type: "limit",
  tab: "orders",
  decimals: 2,
  tick: 0.01,
  prevLevels: new Map(),
  lastTradeId: null,
  seenActivity: null, // keys of the activity events already looked at
  editing: null, // id of the order being edited in the open orders table
  candleHover: null,
  depthHover: null,
  pending: false,
  autoTif: false,
};

// ------------------------------------------------------------------ helpers

function loadTrader() {
  let name = null;
  try { name = localStorage.getItem("tfa.trader"); } catch { /* storage unavailable */ }
  return name || `trader-${Math.random().toString(36).slice(2, 6)}`;
}

function saveTrader(name) {
  try { localStorage.setItem("tfa.trader", name); } catch { /* storage unavailable */ }
}

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const fmtP = (p) => (p == null ? "—" : Number(p).toFixed(ui.decimals));
const fmtQ = (q) => (q == null ? "—" : Number(q).toLocaleString("en-US"));
const fmtMoney = (v) => (v == null ? "—" : `${v < 0 ? "−" : v > 0 ? "+" : ""}${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString("en-GB", { hour12: false });
const sign = (v) => (v > 0 ? "up" : v < 0 ? "down" : "");
const snap = (p) => Math.round(p / ui.tick) * ui.tick;

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((d) => `${d.loc?.at(-1)}: ${d.msg}`).join(", ")
      : data.detail;
    throw new Error(detail || `HTTP ${res.status}`);
  }
  return data;
}

function toast(title, body = "", kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.innerHTML = `<b>${esc(title)}</b>${body ? `<span>${esc(body)}</span>` : ""}`;
  $("#toasts").prepend(el);
  while ($("#toasts").children.length > 4) $("#toasts").lastChild.remove();
  setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 300); }, 4000);
}

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const { width, height } = canvas.getBoundingClientRect();
  if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.font = `10px ${css.getPropertyValue("--mono")}`;
  ctx.textBaseline = "middle";
  return { ctx, w: width, h: height };
}

// ------------------------------------------------------------------ connection

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  const conn = $("#conn");
  ws.onopen = () => { conn.className = "conn live"; conn.querySelector("b").textContent = "Live"; };
  ws.onmessage = (event) => render(JSON.parse(event.data));
  ws.onclose = () => {
    conn.className = "conn down";
    conn.querySelector("b").textContent = "Reconnecting";
    setTimeout(connect, 1000);
  };
}

// ------------------------------------------------------------------ render

function render(data) {
  const first = ui.data === null;
  ui.data = data;
  ui.tick = data.tick_size;
  ui.decimals = Math.max(0, -Math.floor(Math.log10(data.tick_size) + 1e-9));
  if (first && $("#price").value === "" && (data.mid ?? data.last)) {
    $("#price").value = fmtP(snap(data.mid ?? data.last));
  }
  notifyPassiveFills(data);
  notifyCancellations(data);
  renderHeader(data);
  renderLadder(data);
  drawCandles();
  drawDepth();
  renderTicket();
  renderAccount(data);
  renderTab();
}

function renderHeader(d) {
  $("#symbol").textContent = d.symbol;
  const last = $("#last");
  last.textContent = fmtP(d.last);
  const change = d.last != null && d.open ? ((d.last - d.open) / d.open) * 100 : null;
  last.className = `last mono ${sign(change)}`;
  const ch = $("#change");
  ch.textContent = change == null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`;
  ch.className = `change mono ${sign(change)}`;
  $("#stat-bid").textContent = fmtP(d.best_bid);
  $("#stat-ask").textContent = fmtP(d.best_ask);
  $("#stat-spread").textContent = fmtP(d.spread);
  $("#stat-high").textContent = fmtP(d.high);
  $("#stat-low").textContent = fmtP(d.low);
  $("#stat-volume").textContent = fmtQ(d.volume);
  $("#stat-trades").textContent = fmtQ(d.trade_count);
  document.title = d.last != null ? `${fmtP(d.last)} · ${d.symbol} Order Book` : "TFA Order Book";

  const toggle = $("#sim-toggle");
  toggle.classList.toggle("sim-on", d.sim.running);
  $("#sim-label").textContent = d.sim.running ? "Bots on" : "Bots paused";
  if (document.activeElement !== $("#sim-speed")) $("#sim-speed").value = String(d.sim.speed);
}

function renderLadder(d) {
  const rows = Math.max(1, Math.floor($("#asks").clientHeight / ROW_HEIGHT));
  const asks = d.book.asks.slice(0, rows);
  const bids = d.book.bids.slice(0, rows);

  // Cumulative size from the best price outwards; bars are scaled on the larger side.
  const cumulate = (levels) => { let t = 0; return levels.map(([p, q, n]) => [p, q, n, (t += q)]); };
  const a = cumulate(asks);
  const b = cumulate(bids);
  const max = Math.max(1, a.at(-1)?.[3] ?? 0, b.at(-1)?.[3] ?? 0);

  const mine = new Map();
  for (const o of d.orders) {
    if (o.trader === ui.me) mine.set(`${o.side}:${o.price}`, (mine.get(`${o.side}:${o.price}`) ?? 0) + o.remaining);
  }

  const next = new Map();
  const row = (kind, [price, qty, n, cum]) => {
    const key = `${kind}:${price}`;
    next.set(key, qty);
    const prev = ui.prevLevels.get(key);
    const flash = prev === undefined || prev === qty ? "" : qty > prev ? "flash-up" : "flash-down";
    const own = mine.get(`${kind === "ask" ? "sell" : "buy"}:${price}`);
    return `<div class="lrow ${kind} ${flash}" data-price="${price}" title="${n} order${n > 1 ? "s" : ""}${own ? ` · yours: ${own}` : ""}">
      <div class="bar" style="width:${((cum / max) * 100).toFixed(1)}%"></div>
      ${own ? '<i class="mine"></i>' : ""}
      <span class="px">${fmtP(price)}</span><span>${fmtQ(qty)}</span><span class="cum">${fmtQ(cum)}</span>
    </div>`;
  };
  const filler = (n) => '<div class="lrow empty"></div>'.repeat(Math.max(0, n));

  $("#asks").innerHTML = filler(rows - a.length) + a.map((l) => row("ask", l)).reverse().join("");
  $("#bids").innerHTML = b.map((l) => row("bid", l)).join("") + filler(rows - b.length);
  ui.prevLevels = next;

  $("#mid").textContent = d.mid != null ? fmtP(d.mid) : fmtP(d.last);
  $("#mid").className = `mid mono ${sign(d.last != null && d.open ? d.last - d.open : 0)}`;
  $("#spread").textContent = d.spread != null && d.mid
    ? `spread ${fmtP(d.spread)} · ${((d.spread / d.mid) * 1e4).toFixed(1)} bps`
    : "no spread";
}

// ------------------------------------------------------------------ charts

function drawCandles() {
  const d = ui.data;
  const { ctx, w, h } = setupCanvas($("#candles"));
  const axisW = 62;
  const axisH = 20;
  const plotW = w - axisW;
  const volH = (h - axisH) * 0.18;
  const priceH = h - axisH - volH - 8;
  const step = 9;
  const candles = d.candles.slice(-Math.floor(plotW / step));

  if (!candles.length) {
    ctx.fillStyle = C.faint;
    ctx.textAlign = "center";
    ctx.fillText("Waiting for the first trade…", w / 2, h / 2);
    $("#ohlc").textContent = "";
    return;
  }

  let lo = Math.min(...candles.map((c) => c.l));
  let hi = Math.max(...candles.map((c) => c.h));
  const pad = Math.max((hi - lo) * 0.12, ui.tick * 5);
  lo -= pad;
  hi += pad;
  const y = (p) => 6 + ((hi - p) / (hi - lo)) * priceH;
  const maxV = Math.max(...candles.map((c) => c.v));
  const x0 = plotW - candles.length * step;

  // Grid and price axis.
  ctx.strokeStyle = C.border;
  ctx.fillStyle = C.faint;
  ctx.textAlign = "left";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const p = lo + ((hi - lo) * i) / 4;
    const yy = Math.round(y(p)) + 0.5;
    ctx.beginPath(); ctx.moveTo(0, yy); ctx.lineTo(plotW, yy); ctx.stroke();
    if (d.last == null || Math.abs(yy - y(d.last)) > 14) ctx.fillText(fmtP(p), plotW + 8, yy);
  }

  // Candles and volume.
  candles.forEach((c, i) => {
    const x = x0 + i * step + step / 2;
    const col = c.c >= c.o ? C.up : C.down;
    ctx.strokeStyle = col;
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, y(c.h)); ctx.lineTo(Math.round(x) + 0.5, y(c.l)); ctx.stroke();
    const top = y(Math.max(c.o, c.c));
    ctx.fillRect(Math.round(x - 3), top, 6, Math.max(1, y(Math.min(c.o, c.c)) - top));
    ctx.globalAlpha = 0.35;
    const vh = (c.v / maxV) * volH;
    ctx.fillRect(Math.round(x - 3), h - axisH - vh, 6, vh);
    ctx.globalAlpha = 1;
    if (i % 12 === 0) {
      ctx.fillStyle = C.faint;
      ctx.textAlign = "center";
      ctx.fillText(fmtTime(c.t), x, h - axisH / 2);
    }
  });

  // Best bid / ask guides and the last price tag.
  const guide = (p, col, dash) => {
    if (p == null || p < lo || p > hi) return;
    ctx.strokeStyle = col;
    ctx.setLineDash(dash);
    ctx.beginPath(); ctx.moveTo(0, Math.round(y(p)) + 0.5); ctx.lineTo(plotW, Math.round(y(p)) + 0.5); ctx.stroke();
    ctx.setLineDash([]);
  };
  ctx.globalAlpha = 0.35;
  guide(d.best_bid, C.up, [2, 3]);
  guide(d.best_ask, C.down, [2, 3]);
  ctx.globalAlpha = 1;
  const last = candles.at(-1);
  const lastCol = last.c >= last.o ? C.up : C.down;
  guide(d.last, lastCol, [4, 4]);
  if (d.last != null) {
    const yy = y(d.last);
    ctx.fillStyle = lastCol;
    ctx.fillRect(plotW + 2, yy - 9, axisW - 4, 18);
    ctx.fillStyle = "#0a0d13";
    ctx.textAlign = "left";
    ctx.fillText(fmtP(d.last), plotW + 8, yy);
  }

  // Crosshair.
  const hover = ui.candleHover;
  let shown = last;
  if (hover && hover.x < plotW) {
    const i = Math.floor((hover.x - x0) / step);
    if (candles[i]) shown = candles[i];
    ctx.strokeStyle = C.faint;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(hover.x + 0.5, 0); ctx.lineTo(hover.x + 0.5, h - axisH); ctx.stroke();
    if (hover.y < 6 + priceH) {
      ctx.beginPath(); ctx.moveTo(0, hover.y + 0.5); ctx.lineTo(plotW, hover.y + 0.5); ctx.stroke();
      const p = hi - ((hover.y - 6) / priceH) * (hi - lo);
      ctx.setLineDash([]);
      ctx.fillStyle = C.panel;
      ctx.fillRect(plotW + 2, hover.y - 9, axisW - 4, 18);
      ctx.fillStyle = C.text;
      ctx.fillText(fmtP(p), plotW + 8, hover.y);
    }
    ctx.setLineDash([]);
  }
  $("#ohlc").innerHTML = `${fmtTime(shown.t)} &nbsp;O ${fmtP(shown.o)} H ${fmtP(shown.h)} L ${fmtP(shown.l)} C <span class="${shown.c >= shown.o ? "up" : "down"}">${fmtP(shown.c)}</span> &nbsp;V ${fmtQ(shown.v)}`;
}

function drawDepth() {
  const d = ui.data;
  const { ctx, w, h } = setupCanvas($("#depth"));
  const bids = d.book.bids;
  const asks = d.book.asks;
  if (!bids.length && !asks.length) {
    ctx.fillStyle = C.faint;
    ctx.textAlign = "center";
    ctx.fillText("Empty book", w / 2, h / 2);
    return;
  }
  const axisH = 18;
  const plotH = h - axisH - 6;
  const cum = (levels) => { let t = 0; return levels.map(([p, q]) => [p, (t += q)]); };
  const b = cum(bids);
  const a = cum(asks);
  const mid = d.mid ?? (bids[0]?.[0] ?? asks[0]?.[0]);
  const span = Math.max(mid - (b.at(-1)?.[0] ?? mid), (a.at(-1)?.[0] ?? mid) - mid, ui.tick * 10);
  const lo = mid - span;
  const hi = mid + span;
  const maxQ = Math.max(b.at(-1)?.[1] ?? 0, a.at(-1)?.[1] ?? 0, 1);
  const x = (p) => ((p - lo) / (hi - lo)) * w;
  const y = (q) => 6 + plotH - (q / maxQ) * plotH;

  const area = (points, line, fill, towards) => {
    if (!points.length) return;
    ctx.beginPath();
    ctx.moveTo(x(points[0][0]), y(0));
    let prevQ = 0;
    for (const [p, q] of points) {
      ctx.lineTo(x(p), y(prevQ));
      ctx.lineTo(x(p), y(q));
      prevQ = q;
    }
    ctx.lineTo(x(towards), y(prevQ));
    ctx.lineTo(x(towards), y(0));
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.strokeStyle = line;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.lineWidth = 1;
  };
  area(b, C.up, C.upBg, lo);
  area(a, C.down, C.downBg, hi);

  ctx.strokeStyle = C.faint;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(x(mid) + 0.5, 0); ctx.lineTo(x(mid) + 0.5, h - axisH); ctx.stroke();
  ctx.setLineDash([]);

  ctx.fillStyle = C.faint;
  ctx.textAlign = "center";
  for (let i = 1; i < 6; i++) {
    const p = lo + ((hi - lo) * i) / 6;
    ctx.fillText(fmtP(p), x(p), h - axisH / 2);
  }
  ctx.textAlign = "right";
  ctx.fillStyle = C.dim;
  ctx.fillText(`max ${fmtQ(maxQ)}`, w - 6, 12);

  const hover = ui.depthHover;
  if (hover) {
    const p = lo + (hover.x / w) * (hi - lo);
    const side = p < mid ? b : a;
    const level = p < mid
      ? [...side].reverse().find(([lp]) => lp >= p) ?? null
      : [...side].reverse().find(([lp]) => lp <= p) ?? null;
    ctx.strokeStyle = C.dim;
    ctx.beginPath(); ctx.moveTo(hover.x + 0.5, 0); ctx.lineTo(hover.x + 0.5, h - axisH); ctx.stroke();
    $("#depth-hint").textContent = level
      ? `${p < mid ? "bids ≥" : "asks ≤"} ${fmtP(level[0])}: ${fmtQ(level[1])} total`
      : "cumulative size per price";
  } else {
    $("#depth-hint").textContent = "cumulative size per price";
  }
}

// ------------------------------------------------------------------ ticket

function ticketValues() {
  const qty = Number($("#qty").value);
  const price = Number($("#price").value);
  return { qty, price, tif: $("#tif").value, postOnly: $("#post-only").checked };
}

function setSide(side) {
  ui.side = side;
  $$(".seg.side button").forEach((b) => b.classList.toggle("active", b.dataset.side === side));
  renderTicket();
}

function setType(type) {
  ui.type = type;
  $$(".seg.type button").forEach((b) => b.classList.toggle("active", b.dataset.type === type));
  const tif = $("#tif");
  tif.querySelector('[value="gtc"]').disabled = type === "market";
  if (type === "market" && tif.value === "gtc") {
    tif.value = "ioc";
    ui.autoTif = true; // switched for the user: switch back when returning to limit
  } else if (type === "limit" && ui.autoTif) {
    tif.value = "gtc";
    ui.autoTif = false;
  }
  renderTicket();
}

function renderTicket() {
  const { qty, price, tif } = ticketValues();
  const market = ui.type === "market";
  $("#price-field").hidden = market;
  const postOnlyAllowed = !market && tif === "gtc";
  if (!postOnlyAllowed) $("#post-only").checked = false;
  $("#post-only-field").classList.toggle("disabled", !postOnlyAllowed);
  const btn = $("#submit");
  btn.className = `submit ${ui.side}`;
  const verb = ui.side === "buy" ? "Buy" : "Sell";
  btn.textContent = market
    ? `${verb} ${fmtQ(qty || 0)} at market`
    : `${verb} ${fmtQ(qty || 0)} @ ${price ? fmtP(price) : "—"}`;
  renderPreview();
}

// Walks the visible opposite side of the book to estimate what the order would do.
// It is only an estimate: the server is the source of truth (it also applies
// self-trade prevention and sees levels beyond the ones sent to the browser).
function renderPreview() {
  if (!ui.data) return;
  const { qty, price, tif, postOnly } = ticketValues();
  const el = $("#preview");
  if (!qty || qty <= 0 || (ui.type === "limit" && !(price > 0))) {
    el.innerHTML = '<div class="note">Enter a price and a quantity.</div>';
    return;
  }
  const levels = ui.side === "buy" ? ui.data.book.asks : ui.data.book.bids;
  const crosses = (p) => ui.type === "market" || (ui.side === "buy" ? p <= price + 1e-9 : p >= price - 1e-9);
  let left = qty;
  let notional = 0;
  for (const [p, q] of levels) {
    if (left <= 0 || !crosses(p)) break;
    const take = Math.min(left, q);
    notional += take * p;
    left -= take;
  }
  const filled = qty - left;
  const avg = filled ? notional / filled : null;
  const ref = ui.side === "buy" ? ui.data.best_ask : ui.data.best_bid;
  const slip = avg != null && ref ? Math.abs(avg - ref) / ref * 1e4 : null;
  const rows = [];
  let note = "";

  if (postOnly && filled > 0) {
    note = "Post-only: would be rejected (it would take liquidity).";
  } else if (tif === "fok" && left > 0) {
    note = "Fill-or-kill: not enough liquidity, would be cancelled.";
  }
  rows.push(["Fills now", filled ? `${fmtQ(filled)} @ ${fmtP(avg)}` : "—"]);
  if (slip != null && filled) rows.push(["Slippage vs best", `${slip.toFixed(1)} bps`]);
  if (left > 0 && !note) {
    const rests = ui.type === "limit" && tif === "gtc";
    rows.push([rests ? "Rests in book" : "Cancelled", `${fmtQ(left)}${rests ? ` @ ${fmtP(price)}` : ""}`]);
  }
  const notionalTotal = notional + (ui.type === "limit" ? left * price : 0);
  rows.push(["Notional", fmtMoney(notionalTotal).replace("+", "")]);
  el.innerHTML = rows.map(([k, v]) => `<div><span>${k}</span><span>${v}</span></div>`).join("")
    + (note ? `<div class="note">${note}</div>` : "");
}

async function submitOrder(event) {
  event.preventDefault();
  if (ui.pending) return;
  const { qty, price, tif, postOnly } = ticketValues();
  const body = { trader: ui.me, side: ui.side, type: ui.type, quantity: qty, tif };
  if (ui.type === "limit") {
    body.price = $("#price").value.trim();
    body.post_only = postOnly;
  }
  if (!Number.isInteger(qty) || qty <= 0) return toast("Invalid quantity", "Use a whole number of units", "error");
  if (ui.type === "limit" && !(price > 0)) return toast("Invalid price", "", "error");

  ui.pending = true;
  $("#submit").disabled = true;
  try {
    const r = await api("POST", "/api/orders", body);
    const verb = ui.side === "buy" ? "Bought" : "Sold";
    const kind = r.status === "rejected" || (r.status === "cancelled" && !r.filled) ? "error" : ui.side === "buy" ? "up" : "down";
    const parts = [`#${r.order_id}`];
    if (r.resting) parts.push(`${fmtQ(r.resting)} resting @ ${body.price}`);
    if (r.reason) parts.push(r.reason);
    const title = r.filled
      ? `${verb} ${fmtQ(r.filled)} @ ${fmtP(r.average_price)}`
      : r.status === "new" ? "Order placed" : `Order ${r.status}`;
    toast(title, parts.join(" · "), kind);
  } catch (err) {
    toast("Order refused", err.message, "error");
  } finally {
    ui.pending = false;
    $("#submit").disabled = false;
  }
}

// Fills on my resting orders happen while I am not looking: tell me about them.
function notifyPassiveFills(d) {
  const newest = d.trades[0]?.id ?? 0;
  if (ui.lastTradeId === null || newest < ui.lastTradeId) {
    ui.lastTradeId = newest; // first message, or the market was reset
    return;
  }
  const fresh = d.trades.filter((t) => t.id > ui.lastTradeId).reverse();
  ui.lastTradeId = newest;
  for (const t of fresh) {
    const iBought = t.buyer === ui.me && t.aggressor === "sell";
    const iSold = t.seller === ui.me && t.aggressor === "buy";
    if (iBought || iSold) {
      toast(`${iBought ? "Bought" : "Sold"} ${fmtQ(t.quantity)} @ ${fmtP(t.price)}`,
        `your resting order #${iBought ? t.buy_order_id : t.sell_order_id} was hit by ${t[iBought ? "seller" : "buyer"] ?? "someone"}`,
        iBought ? "up" : "down");
    }
  }
}

// Orders cancelled by the engine rather than by me (self-trade prevention) deserve a heads-up.
function notifyCancellations(d) {
  const key = (e) => `${e.ts}:${e.order_id}:${e.status}`;
  const first = ui.seenActivity === null;
  const seen = ui.seenActivity ?? new Set();
  for (const e of [...d.activity].reverse()) {
    if (seen.has(key(e))) continue;
    seen.add(key(e));
    if (!first && e.trader === ui.me && e.reason === "self-trade prevention" && e.price != null && e.status === "cancelled") {
      toast(`Order #${e.order_id} cancelled`, "self-trade prevention: your new order would have traded with it", "error");
    }
  }
  ui.seenActivity = new Set(d.activity.map(key)); // only keep what the server still sends
}

function renderAccount(d) {
  const acc = d.accounts.find((a) => a.trader === ui.me);
  const rank = d.accounts.findIndex((a) => a.trader === ui.me) + 1;
  const open = d.orders.filter((o) => o.trader === ui.me).length;
  $("#account").innerHTML = `
    <h3><span>${esc(ui.me)}</span><span>${rank ? `rank ${rank}/${d.accounts.length}` : "no trades yet"}</span></h3>
    <dl>
      <div><dt>Position</dt><dd class="${sign(acc?.position ?? 0)}">${fmtQ(acc?.position ?? 0)}</dd></div>
      <div><dt>Avg price</dt><dd>${fmtP(acc?.average_price)}</dd></div>
      <div><dt>Cash</dt><dd>${fmtMoney(acc?.cash ?? 0)}</dd></div>
      <div><dt>Open orders</dt><dd>${open}</dd></div>
      <div class="pnl"><dt>PnL (marked at last ${fmtP(d.last)})</dt><dd class="${sign(acc?.pnl ?? 0)}">${fmtMoney(acc?.pnl ?? 0)}</dd></div>
    </dl>`;
}

// ------------------------------------------------------------------ bottom tabs

function renderTab() {
  const d = ui.data;
  if (!d) return;
  const mine = d.orders.filter((o) => o.trader === ui.me);
  $("#count-orders").textContent = mine.length;
  $("#cancel-all").hidden = ui.tab !== "orders" || !mine.length;
  if (ui.editing !== null) return; // do not wipe the inputs while the user is typing
  const body = $("#tab-body");
  const empty = (text) => `<div class="empty-state">${text}</div>`;
  const tag = (side) => `<span class="tag ${side}">${side}</span>`;

  if (ui.tab === "orders") {
    body.innerHTML = mine.length ? `<table>
      <thead><tr><th>ID</th><th>Side</th><th class="num">Price</th><th class="num">Qty</th><th class="num">Filled</th><th class="num">Open</th><th>Status</th><th></th></tr></thead>
      <tbody>${mine.map((o) => `<tr>
        <td class="num">#${o.id}</td><td>${tag(o.side)}</td><td class="num">${fmtP(o.price)}</td>
        <td class="num">${fmtQ(o.quantity)}</td><td class="num">${fmtQ(o.filled)}</td><td class="num">${fmtQ(o.remaining)}</td>
        <td><span class="tag status ${o.status}">${o.status.replace("_", " ")}</span></td>
        <td><div class="actions">
          <button class="icon-btn" data-edit="${o.id}">Modify</button>
          <button class="icon-btn danger" data-cancel="${o.id}">Cancel</button>
        </div></td></tr>`).join("")}</tbody></table>`
      : empty("No open orders. Place a limit order that does not cross the spread and it will rest here.");
  } else if (ui.tab === "trades") {
    body.innerHTML = d.trades.length ? `<table>
      <thead><tr><th>Time</th><th class="num">Price</th><th class="num">Qty</th><th>Aggressor</th><th>Buyer</th><th>Seller</th><th class="num">Buy / sell order</th></tr></thead>
      <tbody>${d.trades.map((t) => `<tr class="${t.buyer === ui.me || t.seller === ui.me ? "is-me" : ""}">
        <td>${fmtTime(t.ts)}</td><td class="num ${t.aggressor === "buy" ? "up" : "down"}">${fmtP(t.price)}</td>
        <td class="num">${fmtQ(t.quantity)}</td><td>${tag(t.aggressor)}</td>
        <td>${esc(t.buyer ?? "—")}</td><td>${esc(t.seller ?? "—")}</td><td class="num">#${t.buy_order_id} / #${t.sell_order_id}</td></tr>`).join("")}</tbody></table>`
      : empty("No trades yet.");
  } else if (ui.tab === "leaderboard") {
    body.innerHTML = d.accounts.length ? `<table>
      <thead><tr><th>#</th><th>Trader</th><th class="num">Position</th><th class="num">Avg price</th><th class="num">Cash</th><th class="num">PnL</th><th class="num">Volume</th><th class="num">Trades</th></tr></thead>
      <tbody>${d.accounts.map((a, i) => `<tr class="${a.trader === ui.me ? "is-me" : ""}">
        <td>${i + 1}</td><td>${esc(a.trader)}${a.bot ? '<span class="tag bot">bot</span>' : ""}${a.trader === ui.me ? '<span class="tag you">you</span>' : ""}</td>
        <td class="num ${sign(a.position)}">${fmtQ(a.position)}</td><td class="num">${fmtP(a.average_price)}</td>
        <td class="num">${fmtMoney(a.cash)}</td><td class="num ${sign(a.pnl)}">${fmtMoney(a.pnl)}</td>
        <td class="num">${fmtQ(a.volume)}</td><td class="num">${fmtQ(a.trades)}</td></tr>`).join("")}</tbody></table>`
      : empty("Nobody has traded yet.");
  } else if (ui.tab === "activity") {
    const events = d.activity.filter((e) => e.trader === ui.me);
    body.innerHTML = events.length ? `<table>
      <thead><tr><th>Time</th><th>Order</th><th>Side</th><th class="num">Price</th><th>Status</th><th class="num">Filled</th><th class="num">Open</th><th>Detail</th></tr></thead>
      <tbody>${events.map((e) => `<tr>
        <td>${fmtTime(e.ts)}</td><td class="num">#${e.order_id}</td><td>${tag(e.side)}</td><td class="num">${e.price == null ? "market" : fmtP(e.price)}</td>
        <td><span class="tag status ${e.status}">${e.status.replace("_", " ")}</span></td>
        <td class="num">${fmtQ(e.filled)}</td><td class="num">${fmtQ(e.remaining)}</td><td class="dim">${esc(e.reason ?? "")}</td></tr>`).join("")}</tbody></table>`
      : empty("Your order events (accepted, filled, cancelled…) will show up here.");
  } else if (!body.querySelector(".about")) {
    body.innerHTML = $("#about").innerHTML;
  }
}

function startEdit(id) {
  const order = ui.data.orders.find((o) => o.id === id);
  if (!order) return;
  ui.editing = id;
  const row = $(`[data-edit="${id}"]`).closest("tr");
  row.children[2].innerHTML = `<input class="edit" id="edit-price" value="${fmtP(order.price)}">`;
  row.children[5].innerHTML = `<input class="edit" id="edit-qty" type="number" min="1" value="${order.remaining}">`;
  row.lastElementChild.innerHTML = `<div class="actions">
    <button class="icon-btn primary" data-save="${id}">Save</button>
    <button class="icon-btn" data-discard>Discard</button></div>`;
  $("#edit-price").focus();
  $("#edit-price").select();
}

async function saveEdit(id) {
  const order = ui.data.orders.find((o) => o.id === id);
  const price = $("#edit-price").value.trim();
  const quantity = Number($("#edit-qty").value);
  const body = { trader: ui.me };
  if (order && Number(price) !== order.price) body.price = price;
  if (order && quantity !== order.remaining) body.quantity = quantity;
  ui.editing = null;
  if (Object.keys(body).length === 1) return renderTab();
  try {
    const r = await api("PATCH", `/api/orders/${id}`, body);
    const keep = body.price === undefined && body.quantity < order.remaining;
    toast(`Order #${id} modified`, keep ? "smaller size: kept its place in the queue" : "new price/size: moved to the back of the queue");
    if (r.trades.length) toast(`Order #${id} traded`, `${r.trades.length} fill(s) after the change`, "up");
  } catch (err) {
    toast("Modify refused", err.message, "error");
  }
  renderTab();
}

// ------------------------------------------------------------------ events

function bind() {
  const trader = $("#trader");
  trader.value = ui.me;
  saveTrader(ui.me);
  $("#avatar").textContent = ui.me[0] ?? "?";
  trader.addEventListener("change", () => {
    const name = trader.value.trim();
    if (!/^[\w.\- ]{1,24}$/.test(name)) {
      trader.value = ui.me;
      return toast("Invalid name", "Letters, digits, '.', '-', '_' and spaces only", "error");
    }
    ui.me = name;
    saveTrader(name);
    $("#avatar").textContent = name[0];
    if (ui.data) render(ui.data);
  });

  $$(".seg.side button").forEach((b) => b.addEventListener("click", () => setSide(b.dataset.side)));
  $$(".seg.type button").forEach((b) => b.addEventListener("click", () => setType(b.dataset.type)));
  $$("[data-step]").forEach((b) => b.addEventListener("click", () => {
    const current = Number($("#price").value) || ui.data?.mid || ui.data?.last || 0;
    $("#price").value = fmtP(Math.max(ui.tick, snap(current + Number(b.dataset.step) * ui.tick)));
    renderTicket();
  }));
  $$("[data-fill]").forEach((b) => b.addEventListener("click", () => {
    const d = ui.data;
    if (!d) return;
    const p = { bid: d.best_bid, ask: d.best_ask, mid: d.mid != null ? snap(d.mid) : null }[b.dataset.fill];
    if (p != null) $("#price").value = fmtP(p);
    renderTicket();
  }));
  $$("[data-qty]").forEach((b) => b.addEventListener("click", () => {
    $("#qty").value = b.dataset.qty;
    renderTicket();
  }));
  ["#price", "#qty", "#tif", "#post-only"].forEach((s) => $(s).addEventListener("input", renderTicket));
  $("#ticket").addEventListener("submit", submitOrder);

  // Click on a level of the ladder: use its price.
  $(".ladder").addEventListener("click", (e) => {
    const row = e.target.closest(".lrow[data-price]");
    if (!row) return;
    if (ui.type === "market") setType("limit");
    $("#price").value = fmtP(Number(row.dataset.price));
    renderTicket();
    $("#qty").focus();
  });

  $("#tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[data-tab]");
    if (!b) return;
    ui.tab = b.dataset.tab;
    ui.editing = null;
    $$("#tabs [data-tab]").forEach((t) => t.classList.toggle("active", t === b));
    $("#tab-body").innerHTML = "";
    renderTab();
  });
  $("#tab-body").addEventListener("click", async (e) => {
    const t = e.target;
    if (t.dataset.cancel) {
      try {
        await api("DELETE", `/api/orders/${t.dataset.cancel}?trader=${encodeURIComponent(ui.me)}`);
        toast(`Order #${t.dataset.cancel} cancelled`);
      } catch (err) { toast("Cancel failed", err.message, "error"); }
    } else if (t.dataset.edit) {
      startEdit(Number(t.dataset.edit));
    } else if (t.dataset.save) {
      saveEdit(Number(t.dataset.save));
    } else if (t.dataset.discard !== undefined) {
      ui.editing = null;
      renderTab();
    }
  });
  $("#tab-body").addEventListener("keydown", (e) => {
    if (ui.editing === null) return;
    if (e.key === "Enter") saveEdit(ui.editing);
    if (e.key === "Escape") { ui.editing = null; renderTab(); }
  });
  $("#cancel-all").addEventListener("click", async () => {
    const r = await api("DELETE", `/api/orders?trader=${encodeURIComponent(ui.me)}`);
    toast(`${r.cancelled} order(s) cancelled`);
  });

  $("#sim-toggle").addEventListener("click", () => api("POST", "/api/sim", { running: !ui.data?.sim.running }));
  $("#sim-speed").addEventListener("change", (e) => api("POST", "/api/sim", { speed: Number(e.target.value) }));
  $("#reset").addEventListener("click", async () => {
    if (!confirm("Start a fresh market? All orders, trades and accounts will be cleared.")) return;
    await api("POST", "/api/reset");
    ui.prevLevels = new Map();
    toast("Market reset", "Fresh book, fresh accounts");
  });

  const hover = (canvas, key, draw) => {
    canvas.addEventListener("mousemove", (e) => {
      const r = canvas.getBoundingClientRect();
      ui[key] = { x: e.clientX - r.left, y: e.clientY - r.top };
      if (ui.data) draw();
    });
    canvas.addEventListener("mouseleave", () => { ui[key] = null; if (ui.data) draw(); });
  };
  hover($("#candles"), "candleHover", drawCandles);
  hover($("#depth"), "depthHover", drawDepth);
  window.addEventListener("resize", () => ui.data && render(ui.data));
}

bind();
setType("limit");
connect();
