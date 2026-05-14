const state = {
  mentions: [],
  domains: [],
  filter: "all",
  search: "",
  sortKey: "last_seen",
  sortDirection: "desc",
};

const sentimentOrder = { negative: 0, risky: 1, neutral: 2, positive: 3 };
const riskOrder = { high: 0, medium: 1, low: 2, none: 3 };

const body = document.querySelector("#mentionsBody");
const screenshots = document.querySelector("#screenshots");
const domains = document.querySelector("#domains");
const searchInput = document.querySelector("#searchInput");

function formatDate(value) {
  if (!value) return "-";
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function badge(value) {
  const safe = escapeHtml(value || "none");
  return `<span class="badge ${safe}">${safe}</span>`;
}

function matchesFilter(mention) {
  if (state.filter === "all") return true;
  if (state.filter === "new") return mention.status === "new";
  return mention.sentiment === state.filter;
}

function matchesSearch(mention) {
  if (!state.search) return true;
  const haystack = [mention.title, mention.url, mention.domain, mention.query].join(" ").toLowerCase();
  return haystack.includes(state.search);
}

function filteredMentions() {
  return state.mentions.filter((mention) => matchesFilter(mention) && matchesSearch(mention));
}

function sortMentions(mentions) {
  return [...mentions].sort((left, right) => {
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
    } else if (state.sortKey === "rank") {
      leftValue = Number(leftValue || 0);
      rightValue = Number(rightValue || 0);
    }

    if (leftValue < rightValue) return state.sortDirection === "asc" ? -1 : 1;
    if (leftValue > rightValue) return state.sortDirection === "asc" ? 1 : -1;
    return 0;
  });
}

function renderTable() {
  const mentions = sortMentions(filteredMentions());
  if (!mentions.length) {
    body.innerHTML = '<tr><td colspan="12" class="empty">No mentions match the current view</td></tr>';
    return;
  }

  body.innerHTML = mentions.map((mention) => `
    <tr>
      <td>${formatDate(mention.first_seen)}</td>
      <td>${formatDate(mention.last_seen)}</td>
      <td class="query-cell">${escapeHtml(mention.query)}</td>
      <td>${escapeHtml(mention.rank)}</td>
      <td class="title-cell">${escapeHtml(mention.title)}</td>
      <td class="url-cell"><a href="${escapeHtml(mention.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(mention.url)}</a></td>
      <td>${escapeHtml(mention.domain)}</td>
      <td>${badge(mention.sentiment || "neutral")}</td>
      <td>${badge(mention.risk_level || "none")}</td>
      <td>${escapeHtml(mention.risk_keywords || "-")}</td>
      <td>${badge(mention.source_type || "organic")}</td>
      <td>${badge(mention.status || "existing")}</td>
    </tr>
  `).join("");
}

function renderSummary(summary) {
  document.querySelector("#totalMentions").textContent = summary.total_mentions ?? state.mentions.length;
  document.querySelector("#newMentions").textContent = summary.new_mentions ?? 0;
  document.querySelector("#riskyMentions").textContent = summary.risky_mentions ?? 0;
  document.querySelector("#negativeMentions").textContent = summary.negative_mentions ?? 0;
  document.querySelector("#positiveMentions").textContent = summary.positive_mentions ?? 0;
  document.querySelector("#neutralMentions").textContent = summary.neutral_mentions ?? 0;
}

function renderDomains() {
  document.querySelector("#domainCount").textContent = `${state.domains.length} domains`;
  if (!state.domains.length) {
    domains.innerHTML = '<p class="empty">Domain summary will appear after the first export.</p>';
    return;
  }

  domains.innerHTML = state.domains.map((domain) => `
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
  const captures = state.mentions.filter((mention) => mention.screenshot);
  document.querySelector("#screenshotCount").textContent = `${captures.length} captures`;
  if (!captures.length) {
    screenshots.innerHTML = '<p class="empty">No screenshots have been captured yet.</p>';
    return;
  }

  screenshots.innerHTML = captures.map((mention) => `
    <figure class="capture">
      <a href="${escapeHtml(mention.screenshot)}" target="_blank" rel="noopener noreferrer">
        <img src="${escapeHtml(mention.screenshot)}" alt="Screenshot for ${escapeHtml(mention.title)}" loading="lazy" />
      </a>
      <figcaption>
        <strong>${escapeHtml(mention.sentiment)}</strong> | #${escapeHtml(mention.rank)} | ${escapeHtml(mention.domain)}<br />
        ${escapeHtml(mention.title)}
      </figcaption>
    </figure>
  `).join("");
}

function render(payload) {
  document.querySelector("#projectName").textContent = payload.project || "SERP Monitoring Dashboard";
  document.querySelector("#generatedAt").textContent = formatDate(payload.generated_at);
  state.mentions = payload.mentions || [];
  state.domains = payload.domains || [];
  renderSummary(payload.summary || {});
  renderTable();
  renderDomains();
  renderGallery();
}

function bindEvents() {
  document.querySelectorAll(".filter").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".filter").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.filter = button.dataset.filter;
      renderTable();
    });
  });

  document.querySelectorAll("[data-sort]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.sort;
      if (state.sortKey === key) {
        state.sortDirection = state.sortDirection === "asc" ? "desc" : "asc";
      } else {
        state.sortKey = key;
        state.sortDirection = key === "rank" || key === "sentiment" || key === "risk_level" ? "asc" : "desc";
      }
      renderTable();
    });
  });

  searchInput.addEventListener("input", () => {
    state.search = searchInput.value.trim().toLowerCase();
    renderTable();
  });
}

async function loadDashboard() {
  try {
    const response = await fetch("data/results.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    body.innerHTML = `<tr><td colspan="12" class="empty">Dashboard data is not available yet: ${escapeHtml(error.message)}</td></tr>`;
    domains.innerHTML = '<p class="empty">Domain summary will appear after the first export.</p>';
    screenshots.innerHTML = '<p class="empty">Screenshots will appear after the first monitor run.</p>';
  }
}

bindEvents();
loadDashboard();
