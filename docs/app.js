const AUTH_LOGIN = "yevhen";
const PASSWORD_HASH = "e8b724faa5749614b7bfb6e176b2ccaace349368a05924a6fbc6c2094bbb04c9";
const AUTH_KEY = "serp_dashboard_unlocked";

const state = {
  payload: {},
  view: "all",
  queryTab: "",
  filters: { query: "", status: "", sentiment: "", domain: "" },
  sortKey: "current_rank",
  sortDirection: "asc",
  page: 1,
  pageSize: 50,
  screenshotIndex: 0,
  screenshotZoom: 1,
};

const sentimentOrder = { negative: 0, risky: 1, neutral: 2, positive: 3 };
const riskOrder = { high: 0, medium: 1, low: 2, none: 3 };
const chartColors = ["#4fbf9f", "#6bb7ff", "#f2bf63", "#ff7878", "#63d28f"];

const body = document.querySelector("#mentionsBody");
const screenshots = document.querySelector("#screenshots");
const domains = document.querySelector("#domains");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function sha256(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function showDashboard() {
  document.querySelector("#loginScreen").hidden = true;
  document.querySelector("#dashboard").hidden = false;
  bindEvents();
  loadDashboard();
}

function bindAuth() {
  if (sessionStorage.getItem(AUTH_KEY) === "true") {
    showDashboard();
    return;
  }
  document.querySelector("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const login = document.querySelector("#loginInput").value.trim();
    const password = document.querySelector("#passwordInput").value;
    const error = document.querySelector("#loginError");
    if (login === AUTH_LOGIN && await sha256(password) === PASSWORD_HASH) {
      sessionStorage.setItem(AUTH_KEY, "true");
      showDashboard();
      return;
    }
    error.textContent = "Invalid login or password.";
  });
}

function formatDate(value) {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function badge(value) {
  const safe = escapeHtml(value || "none");
  return `<span class="badge ${safe}">${safe}</span>`;
}

function tagBadges(tags) {
  if (!tags || !tags.length) return "-";
  return tags.map((tag) => `<span class="tag ${escapeHtml(tag)}">${escapeHtml(tag)}</span>`).join(" ");
}

function sourceRows() {
  if (state.queryTab) return (state.payload.latest_top10 || []).filter((item) => item.query === state.queryTab);
  if (state.view === "all") return state.payload.latest_top10 || [];
  return (state.payload.views && state.payload.views[state.view]) || [];
}

function filteredRows() {
  return sourceRows().filter((item) => {
    return (!state.filters.query || item.query === state.filters.query)
      && (!state.filters.status || item.status === state.filters.status)
      && (!state.filters.sentiment || item.sentiment === state.filters.sentiment)
      && (!state.filters.domain || item.domain_entity === state.filters.domain || item.parent_domain === state.filters.domain || item.domain === state.filters.domain);
  });
}

function sortedRows(rows) {
  return [...rows].sort((left, right) => {
    let leftValue = left[state.sortKey];
    let rightValue = right[state.sortKey];
    if (state.sortKey === "first_seen" || state.sortKey === "last_seen") {
      leftValue = new Date(leftValue || 0).getTime();
      rightValue = new Date(rightValue || 0).getTime();
    } else if (state.sortKey === "sentiment") {
      leftValue = sentimentOrder[leftValue] ?? 99;
      rightValue = sentimentOrder[rightValue] ?? 99;
    } else if (state.sortKey === "risk_level") {
      leftValue = riskOrder[leftValue] ?? 99;
      rightValue = riskOrder[rightValue] ?? 99;
    } else if (state.sortKey === "current_rank") {
      leftValue = Number(leftValue || 999);
      rightValue = Number(rightValue || 999);
    }
    if (leftValue < rightValue) return state.sortDirection === "asc" ? -1 : 1;
    if (leftValue > rightValue) return state.sortDirection === "asc" ? 1 : -1;
    return 0;
  });
}

function renderSummary(summary) {
  document.querySelector("#totalMentions").textContent = summary.total_mentions || 0;
  document.querySelector("#newMentions").textContent = summary.new_mentions || 0;
  document.querySelector("#changedMentions").textContent = summary.changed_mentions || 0;
  document.querySelector("#disappearedMentions").textContent = summary.disappeared_mentions || 0;
  document.querySelector("#riskMentions").textContent = (summary.risky_mentions || 0) + (summary.negative_mentions || 0);
  document.querySelector("#safeMentions").textContent = (summary.positive_mentions || 0) + (summary.neutral_mentions || 0);
}

function renderVolatility() {
  const volatility = state.payload.volatility || {};
  document.querySelector("#rankIncreases").innerHTML = rankList(volatility.biggest_rank_increases || []);
  document.querySelector("#rankDrops").innerHTML = rankList(volatility.biggest_rank_drops || []);
  const volatile = volatility.most_volatile_query || {};
  document.querySelector("#volatileQuery").textContent = volatile.query ? `${volatile.query} (${volatile.movement})` : "-";
  document.querySelector("#newDomainsToday").innerHTML = compactList(volatility.new_domains_today || []);
  document.querySelector("#disappearedDomainsToday").innerHTML = compactList(volatility.disappeared_domains_today || []);
}

function rankList(rows) {
  if (!rows.length) return "-";
  return rows.slice(0, 4).map((item) => `<button class="text-link" data-url="${escapeHtml(item.url)}" type="button">${escapeHtml(item.domain_entity || item.domain)} ${item.rank_delta > 0 ? "+" : ""}${escapeHtml(item.rank_delta || 0)}</button>`).join("");
}

function compactList(values) {
  if (!values.length) return "-";
  return values.slice(0, 6).map((value) => `<span>${escapeHtml(value)}</span>`).join("");
}

function renderQueryHealth() {
  const root = document.querySelector("#queryHealth");
  const health = state.payload.query_health || {};
  const entries = Object.entries(health);
  if (!entries.length) {
    root.innerHTML = '<p class="empty">Query health scores will appear after snapshot export.</p>';
    return;
  }
  root.innerHTML = entries.map(([query, item]) => `
    <article class="health-card">
      <span>${escapeHtml(query)}</span>
      <strong>${escapeHtml(item.score)}/100</strong>
      <em class="trend ${escapeHtml(item.trend)}">${escapeHtml(item.trend)}</em>
    </article>
  `).join("");
}

function renderTable() {
  const rows = sortedRows(filteredRows());
  const totalPages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.page = Math.min(state.page, totalPages);
  const start = (state.page - 1) * state.pageSize;
  const pageRows = rows.slice(start, start + state.pageSize);
  if (!pageRows.length) {
    body.innerHTML = '<tr><td colspan="17" class="empty">No rows match this view</td></tr>';
  } else {
    body.innerHTML = pageRows.map((item) => `
      <tr>
        <td class="query-cell">${escapeHtml(item.query)}</td>
        <td>${escapeHtml(item.current_rank || "-")}</td>
        <td>${escapeHtml(item.previous_rank || "-")}</td>
        <td>${item.rank_delta === null || item.rank_delta === undefined ? "-" : escapeHtml(item.rank_delta)}</td>
        <td>${badge(item.status)}</td>
        <td class="title-cell">${escapeHtml(item.title)}</td>
        <td class="url-cell"><button class="url-history" data-url="${escapeHtml(item.url)}" type="button">${escapeHtml(item.url)}</button></td>
        <td>${escapeHtml(item.domain)}</td>
        <td>${escapeHtml(item.domain_entity || item.parent_domain || "-")}</td>
        <td>${tagBadges(item.domain_tags)}</td>
        <td>${badge(item.sentiment || "neutral")}</td>
        <td>${badge(item.risk_level || "none")}</td>
        <td>${escapeHtml(item.risk_keywords || "-")}</td>
        <td>${formatDate(item.first_seen)}</td>
        <td>${formatDate(item.last_seen)}</td>
        <td>${item.date_published ? escapeHtml(item.date_published) : "unknown"}</td>
        <td>${badge(item.source_type || "organic")}</td>
      </tr>
    `).join("");
  }
  document.querySelector("#pageInfo").textContent = `Page ${state.page} of ${totalPages} - ${rows.length} rows`;
  document.querySelector("#prevPage").disabled = state.page <= 1;
  document.querySelector("#nextPage").disabled = state.page >= totalPages;
}

function resetAndRender() {
  state.page = 1;
  renderTable();
}

function renderQueryTabs(queries) {
  const root = document.querySelector("#queryTabs");
  root.innerHTML = '<button class="query-tab active" data-query="" type="button">All current top-10</button>'
    + queries.map((query) => `<button class="query-tab" data-query="${escapeHtml(query)}" type="button">${escapeHtml(query)}</button>`).join("");
  root.querySelectorAll(".query-tab").forEach((button) => {
    button.addEventListener("click", () => {
      root.querySelectorAll(".query-tab").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.queryTab = button.dataset.query;
      state.view = "all";
      document.querySelectorAll(".view").forEach((item) => item.classList.toggle("active", item.dataset.view === "all"));
      resetAndRender();
    });
  });
}

function populateFilters(rows) {
  fillSelect("#queryFilter", [...new Set(rows.map((item) => item.query))].sort(), "All queries");
  fillSelect("#domainFilter", [...new Set(rows.flatMap((item) => [item.domain_entity, item.parent_domain, item.domain]).filter(Boolean))].sort(), "All domains");
}

function fillSelect(selector, values, label) {
  const select = document.querySelector(selector);
  select.innerHTML = `<option value="">${label}</option>` + values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
}

function renderDomains() {
  const domainRows = state.payload.domains || [];
  document.querySelector("#domainCount").textContent = `${domainRows.length} entities`;
  if (!domainRows.length) {
    domains.innerHTML = '<p class="empty">Domain summary will appear after the first export.</p>';
    return;
  }
  domains.innerHTML = domainRows.map((domain) => `
    <article class="domain-card">
      <h3>${escapeHtml(domain.domain)}</h3>
      <p>${escapeHtml((domain.raw_domains || []).join(", "))}</p>
      <div>${tagBadges(domain.tags)}</div>
      <div class="domain-stats">
        <span>Total</span><strong>${escapeHtml(domain.total)}</strong>
        <span>Best rank</span><strong>${escapeHtml(domain.best_rank || "-")}</strong>
        <span>Risky</span><strong>${escapeHtml(domain.risky || 0)}</strong>
        <span>Negative</span><strong>${escapeHtml(domain.negative || 0)}</strong>
        <span>Positive</span><strong>${escapeHtml(domain.positive || 0)}</strong>
        <span>Neutral</span><strong>${escapeHtml(domain.neutral || 0)}</strong>
      </div>
    </article>
  `).join("");
}

function renderGallery() {
  const shots = state.payload.screenshots || [];
  document.querySelector("#screenshotCount").textContent = `${shots.length} captures`;
  if (!shots.length) {
    screenshots.innerHTML = '<p class="empty">No SERP screenshots have been captured yet.</p>';
    return;
  }
  screenshots.innerHTML = shots.map((shot, index) => `
    <figure class="capture" data-shot-index="${index}">
      <img src="${escapeHtml(shot.path)}" alt="SERP screenshot for ${escapeHtml(shot.query)}" loading="lazy" />
      <figcaption><strong>${escapeHtml(shot.date)}</strong><br />${escapeHtml(shot.query)}</figcaption>
    </figure>
  `).join("");
}

function openHistory(url) {
  const data = (state.payload.url_histories || {})[url];
  const panel = document.querySelector("#historyPanel");
  const root = document.querySelector("#historyContent");
  if (!data) {
    root.innerHTML = `<p class="empty">No exported history for ${escapeHtml(url)}</p>`;
  } else {
    root.innerHTML = `
      <a class="history-url" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>
      <dl class="history-meta">
        <dt>First seen</dt><dd>${formatDate(data.first_seen)}</dd>
        <dt>Last seen</dt><dd>${formatDate(data.last_seen)}</dd>
        <dt>Disappeared</dt><dd>${formatDate(data.disappeared_at)}</dd>
        <dt>Queries</dt><dd>${escapeHtml((data.queries || []).join(", "))}</dd>
      </dl>
      <canvas id="urlRankChart" height="150"></canvas>
      <h3>Rank history</h3>
      <div class="history-table">${historyRows(data.history || [])}</div>
      <h3>Risk and sentiment changes</h3>
      <div class="history-table">${sentimentRows(data.sentiment_changes || [])}</div>
    `;
    drawLineChart("urlRankChart", (data.rank_series || []).map((item) => item.date), (data.rank_series || []).map((item) => item.rank ? 11 - item.rank : 0), "Rank visibility");
  }
  panel.hidden = false;
}

function historyRows(rows) {
  if (!rows.length) return '<p class="empty">No rows</p>';
  return `<table><tbody>${rows.map((row) => `<tr><td>${formatDate(row.run_datetime)}</td><td>${escapeHtml(row.query)}</td><td>#${escapeHtml(row.current_rank || "-")}</td><td>${badge(row.status)}</td></tr>`).join("")}</tbody></table>`;
}

function sentimentRows(rows) {
  if (!rows.length) return '<p class="empty">No rows</p>';
  return `<table><tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.date)}</td><td>${badge(row.sentiment)}</td><td>${badge(row.risk_level)}</td></tr>`).join("")}</tbody></table>`;
}

function openScreenshot(index) {
  const shots = state.payload.screenshots || [];
  if (!shots.length) return;
  state.screenshotIndex = (index + shots.length) % shots.length;
  state.screenshotZoom = 1;
  updateScreenshotModal();
  document.querySelector("#screenshotModal").hidden = false;
}

function updateScreenshotModal() {
  const shot = (state.payload.screenshots || [])[state.screenshotIndex];
  if (!shot) return;
  const image = document.querySelector("#modalImage");
  image.src = shot.path;
  image.style.transform = `scale(${state.screenshotZoom})`;
  document.querySelector("#zoomLevel").textContent = `${Math.round(state.screenshotZoom * 100)}%`;
  document.querySelector("#openOriginal").href = shot.path;
  document.querySelector("#modalCaption").textContent = `${shot.date} - ${shot.query}`;
}

function renderCharts() {
  const charts = state.payload.charts || {};
  const labels = charts.labels || [];
  drawLineChart("visibilityChart", labels, charts.visibility || [], "Visibility");
  drawLineChart("riskyChart", labels, charts.risky_mentions || [], "Risky");
  drawLineChart("newUrlsChart", labels, charts.new_urls || [], "New URLs");
  drawLineChart("averageRankChart", labels, charts.average_rank || [], "Average rank", true);
  drawMultiLineChart("domainTrendChart", labels, charts.domain_trends || {});
}

function drawLineChart(id, labels, values, label, invert = false) {
  const canvas = document.querySelector(`#${id}`);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  prepareCanvas(canvas, ctx);
  if (!values.length) return drawEmptyChart(ctx, canvas);
  const points = toPoints(canvas, values, invert);
  drawAxes(ctx, canvas);
  drawPath(ctx, points, chartColors[0]);
  drawChartLabel(ctx, label, labels);
}

function drawMultiLineChart(id, labels, series) {
  const canvas = document.querySelector(`#${id}`);
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  prepareCanvas(canvas, ctx);
  const entries = Object.entries(series);
  if (!entries.length) return drawEmptyChart(ctx, canvas);
  const maxValue = Math.max(1, ...entries.flatMap(([, values]) => values));
  drawAxes(ctx, canvas);
  entries.forEach(([name, values], index) => {
    const points = values.map((value, i) => {
      const x = 36 + (i * (canvas.width - 60)) / Math.max(1, values.length - 1);
      const y = canvas.height - 28 - ((value / maxValue) * (canvas.height - 56));
      return { x, y };
    });
    drawPath(ctx, points, chartColors[index % chartColors.length]);
    ctx.fillStyle = chartColors[index % chartColors.length];
    ctx.fillText(name, 42, 18 + index * 14);
  });
  drawChartLabel(ctx, "Domain trends", labels);
}

function prepareCanvas(canvas, ctx) {
  const ratio = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(320, Math.floor(rect.width * ratio));
  canvas.height = Math.max(150, Math.floor(Number(canvas.getAttribute("height") || 150) * ratio));
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.font = "12px sans-serif";
}

function toPoints(canvas, values, invert) {
  const maxValue = Math.max(1, ...values.filter((value) => value !== null && value !== undefined));
  return values.map((value, index) => {
    const x = 36 + (index * (canvas.width - 60)) / Math.max(1, values.length - 1);
    const normalized = invert ? 1 - (Number(value || 0) / maxValue) : Number(value || 0) / maxValue;
    const y = canvas.height - 28 - (normalized * (canvas.height - 56));
    return { x, y };
  });
}

function drawAxes(ctx, canvas) {
  ctx.strokeStyle = "#263542";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(34, 12);
  ctx.lineTo(34, canvas.height - 28);
  ctx.lineTo(canvas.width - 18, canvas.height - 28);
  ctx.stroke();
}

function drawPath(ctx, points, color) {
  if (!points.length) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => index ? ctx.lineTo(point.x, point.y) : ctx.moveTo(point.x, point.y));
  ctx.stroke();
  ctx.fillStyle = color;
  points.forEach((point) => ctx.fillRect(point.x - 2, point.y - 2, 4, 4));
}

function drawChartLabel(ctx, label, labels) {
  ctx.fillStyle = "#93a3ad";
  ctx.fillText(label, 42, 18);
  if (labels.length) ctx.fillText(`${labels[0]} -> ${labels[labels.length - 1]}`, 42, 34);
}

function drawEmptyChart(ctx, canvas) {
  ctx.fillStyle = "#93a3ad";
  ctx.fillText("No chart data yet", 40, canvas.height / 2);
}

function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function downloadCsv() {
  const rows = state.payload.mentions || [];
  const headers = ["query", "rank", "previous_rank", "rank_delta", "status", "title", "url", "domain", "domain_entity", "tags", "sentiment", "risk_level", "first_seen", "last_seen", "date_published"];
  const csv = [headers.join(","), ...rows.map((row) => headers.map((key) => `"${String(key === "tags" ? (row.domain_tags || []).join("|") : row[key] ?? "").replaceAll('"', '""')}"`).join(","))].join("\n");
  downloadFile("serp-dashboard.csv", csv, "text/csv");
}

function bindEvents() {
  document.querySelectorAll(".view").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(".query-tab").forEach((item) => item.classList.remove("active"));
      state.view = button.dataset.view;
      state.queryTab = "";
      resetAndRender();
    });
  });
  ["query", "status", "sentiment", "domain"].forEach((key) => {
    document.querySelector(`#${key}Filter`).addEventListener("change", (event) => {
      state.filters[key] = event.target.value;
      resetAndRender();
    });
  });
  document.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (state.sortKey === key) state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      else {
        state.sortKey = key;
        state.sortDirection = key === "current_rank" || key === "sentiment" || key === "risk_level" ? "asc" : "desc";
      }
      resetAndRender();
    });
  });
  document.querySelector("#prevPage").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); renderTable(); });
  document.querySelector("#nextPage").addEventListener("click", () => { state.page += 1; renderTable(); });
  document.addEventListener("click", (event) => {
    const historyButton = event.target.closest(".url-history, .text-link");
    if (historyButton?.dataset.url) openHistory(historyButton.dataset.url);
    const capture = event.target.closest(".capture");
    if (capture?.dataset.shotIndex) openScreenshot(Number(capture.dataset.shotIndex));
  });
  document.querySelector("#closeHistory").addEventListener("click", () => { document.querySelector("#historyPanel").hidden = true; });
  document.querySelector("#closeModal").addEventListener("click", () => { document.querySelector("#screenshotModal").hidden = true; });
  document.querySelector("#prevShot").addEventListener("click", () => openScreenshot(state.screenshotIndex - 1));
  document.querySelector("#nextShot").addEventListener("click", () => openScreenshot(state.screenshotIndex + 1));
  document.querySelector("#zoomIn").addEventListener("click", () => { state.screenshotZoom = Math.min(3, state.screenshotZoom + 0.25); updateScreenshotModal(); });
  document.querySelector("#zoomOut").addEventListener("click", () => { state.screenshotZoom = Math.max(0.5, state.screenshotZoom - 0.25); updateScreenshotModal(); });
  document.addEventListener("keydown", (event) => {
    if (document.querySelector("#screenshotModal").hidden) return;
    if (event.key === "Escape") document.querySelector("#screenshotModal").hidden = true;
    if (event.key === "ArrowLeft") openScreenshot(state.screenshotIndex - 1);
    if (event.key === "ArrowRight") openScreenshot(state.screenshotIndex + 1);
    if (event.key === "+" || event.key === "=") { state.screenshotZoom = Math.min(3, state.screenshotZoom + 0.25); updateScreenshotModal(); }
    if (event.key === "-") { state.screenshotZoom = Math.max(0.5, state.screenshotZoom - 0.25); updateScreenshotModal(); }
  });
  document.querySelector("#downloadCsv").addEventListener("click", downloadCsv);
  document.querySelector("#downloadJson").addEventListener("click", () => downloadFile("serp-dashboard.json", JSON.stringify(state.payload, null, 2), "application/json"));
  document.querySelector("#downloadSummary").addEventListener("click", () => downloadFile("serp-executive-summary.md", state.payload.executive_summary_markdown || "", "text/markdown"));
}

async function loadDashboard() {
  try {
    const response = await fetch("data/results.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    document.querySelector("#projectName").textContent = state.payload.project || "SERP Snapshot Dashboard";
    document.querySelector("#generatedAt").textContent = formatDate(state.payload.generated_at);
    renderSummary(state.payload.summary || {});
    renderVolatility();
    renderQueryHealth();
    populateFilters(state.payload.mentions || state.payload.latest_top10 || []);
    renderQueryTabs(state.payload.queries || []);
    renderTable();
    renderDomains();
    renderGallery();
    renderCharts();
  } catch (error) {
    body.innerHTML = `<tr><td colspan="17" class="empty">Dashboard data is not available yet: ${escapeHtml(error.message)}</td></tr>`;
  }
}

bindAuth();
