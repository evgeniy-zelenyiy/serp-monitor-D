const state = {
  payload: {},
  view: "all",
  queryTab: "",
  filters: { query: "", date: "", status: "", sentiment: "", risk: "", domain: "" },
  sortKey: "current_rank",
  sortDirection: "asc",
};

const sentimentOrder = { negative: 0, risky: 1, neutral: 2, positive: 3 };
const riskOrder = { high: 0, medium: 1, low: 2, none: 3 };

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

function formatDate(value) {
  if (!value) return "unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatShortDate(value) {
  return value ? String(value).slice(0, 10) : "unknown";
}

function badge(value) {
  const safe = escapeHtml(value || "none");
  return `<span class="badge ${safe}">${safe}</span>`;
}

function sourceRows() {
  if (state.queryTab) {
    return (state.payload.latest_top10 || []).filter((item) => item.query === state.queryTab);
  }
  if (state.view === "all") return state.payload.mentions || [];
  return (state.payload.views && state.payload.views[state.view]) || [];
}

function filteredRows() {
  return sourceRows().filter((item) => {
    const date = formatShortDate(item.run_datetime || item.last_seen);
    return (!state.filters.query || item.query === state.filters.query)
      && (!state.filters.date || date === state.filters.date)
      && (!state.filters.status || item.status === state.filters.status)
      && (!state.filters.sentiment || item.sentiment === state.filters.sentiment)
      && (!state.filters.risk || item.risk_level === state.filters.risk)
      && (!state.filters.domain || item.domain === state.filters.domain);
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

function renderTable() {
  const rows = sortedRows(filteredRows());
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="15" class="empty">No rows match this view</td></tr>';
    return;
  }
  body.innerHTML = rows.map((item) => `
    <tr>
      <td class="query-cell">${escapeHtml(item.query)}</td>
      <td>${escapeHtml(item.current_rank || "-")}</td>
      <td>${escapeHtml(item.previous_rank || "-")}</td>
      <td>${item.rank_delta === null || item.rank_delta === undefined ? "-" : escapeHtml(item.rank_delta)}</td>
      <td>${badge(item.status)}</td>
      <td class="title-cell">${escapeHtml(item.title)}</td>
      <td class="url-cell"><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url)}</a></td>
      <td>${escapeHtml(item.domain)}</td>
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
      renderTable();
    });
  });
}

function populateFilters(rows) {
  fillSelect("#queryFilter", [...new Set(rows.map((item) => item.query))].sort(), "All queries");
  fillSelect("#domainFilter", [...new Set(rows.map((item) => item.domain).filter(Boolean))].sort(), "All domains");
}

function fillSelect(selector, values, label) {
  const select = document.querySelector(selector);
  select.innerHTML = `<option value="">${label}</option>`
    + values.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
}

function renderDomains() {
  const domainRows = state.payload.domains || [];
  document.querySelector("#domainCount").textContent = `${domainRows.length} domains`;
  if (!domainRows.length) {
    domains.innerHTML = '<p class="empty">Domain summary will appear after the first export.</p>';
    return;
  }
  domains.innerHTML = domainRows.map((domain) => `
    <article class="domain-card">
      <h3>${escapeHtml(domain.domain)}</h3>
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
  screenshots.innerHTML = shots.map((shot) => `
    <figure class="capture">
      <a href="${escapeHtml(shot.path)}" target="_blank" rel="noopener noreferrer"><img src="${escapeHtml(shot.path)}" alt="SERP screenshot for ${escapeHtml(shot.query)}" loading="lazy" /></a>
      <figcaption><strong>${escapeHtml(shot.date)}</strong><br />${escapeHtml(shot.query)}</figcaption>
    </figure>
  `).join("");
}

function bindEvents() {
  document.querySelectorAll(".view").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".view").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      document.querySelectorAll(".query-tab").forEach((item) => item.classList.remove("active"));
      state.view = button.dataset.view;
      state.queryTab = "";
      renderTable();
    });
  });
  ["query", "date", "status", "sentiment", "risk", "domain"].forEach((key) => {
    document.querySelector(`#${key}Filter`).addEventListener("change", (event) => {
      state.filters[key] = event.target.value;
      renderTable();
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
      renderTable();
    });
  });
}

async function loadDashboard() {
  try {
    const response = await fetch("data/results.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.payload = await response.json();
    document.querySelector("#projectName").textContent = state.payload.project || "SERP Snapshot Dashboard";
    document.querySelector("#generatedAt").textContent = formatDate(state.payload.generated_at);
    renderSummary(state.payload.summary || {});
    populateFilters(state.payload.mentions || []);
    renderQueryTabs(state.payload.queries || []);
    renderTable();
    renderDomains();
    renderGallery();
  } catch (error) {
    body.innerHTML = `<tr><td colspan="15" class="empty">Dashboard data is not available yet: ${escapeHtml(error.message)}</td></tr>`;
  }
}

bindEvents();
loadDashboard();
