const state = {
  mentions: [],
  filter: "all",
  sortKey: "last_seen",
  sortDirection: "desc",
};

const sentimentOrder = {
  negative: 0,
  risky: 1,
  neutral: 2,
  positive: 3,
};

const body = document.querySelector("#mentionsBody");
const screenshots = document.querySelector("#screenshots");

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

function filteredMentions() {
  return state.mentions.filter((mention) => {
    if (state.filter === "all") return true;
    if (state.filter === "new") return mention.status === "new";
    return mention.sentiment === state.filter;
  });
}

function sortMentions(mentions) {
  return [...mentions].sort((left, right) => {
    let leftValue = left[state.sortKey];
    let rightValue = right[state.sortKey];

    if (state.sortKey === "first_seen" || state.sortKey === "last_seen") {
      leftValue = new Date(leftValue || 0).getTime();
      rightValue = new Date(rightValue || 0).getTime();
    }

    if (state.sortKey === "sentiment") {
      leftValue = sentimentOrder[leftValue] ?? 99;
      rightValue = sentimentOrder[rightValue] ?? 99;
    }

    if (state.sortKey === "rank") {
      leftValue = Number(leftValue || 0);
      rightValue = Number(rightValue || 0);
    }

    if (leftValue < rightValue) return state.sortDirection === "asc" ? -1 : 1;
    if (leftValue > rightValue) return state.sortDirection === "asc" ? 1 : -1;
    return 0;
  });
}

function badge(value) {
  return `<span class="badge ${escapeHtml(value)}">${escapeHtml(value)}</span>`;
}

function renderTable() {
  const mentions = sortMentions(filteredMentions());
  if (!mentions.length) {
    body.innerHTML = '<tr><td colspan="11" class="empty">No mentions match this filter</td></tr>';
    return;
  }

  body.innerHTML = mentions
    .map(
      (mention) => `
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
          <td>${badge(mention.status || "existing")}</td>
        </tr>
      `
    )
    .join("");
}

function renderGallery() {
  const captures = state.mentions.filter((mention) => mention.screenshot);
  document.querySelector("#screenshotCount").textContent = `${captures.length} captures`;

  if (!captures.length) {
    screenshots.innerHTML = '<p class="empty">No screenshots have been captured yet.</p>';
    return;
  }

  screenshots.innerHTML = captures
    .map(
      (mention) => `
        <figure class="capture">
          <a href="${escapeHtml(mention.screenshot)}" target="_blank" rel="noopener noreferrer">
            <img src="${escapeHtml(mention.screenshot)}" alt="Screenshot for ${escapeHtml(mention.title)}" loading="lazy" />
          </a>
          <figcaption>
            <strong>${escapeHtml(mention.sentiment)}</strong> · #${escapeHtml(mention.rank)} · ${escapeHtml(mention.domain)}<br />
            ${escapeHtml(mention.title)}
          </figcaption>
        </figure>
      `
    )
    .join("");
}

function renderSummary(summary) {
  document.querySelector("#totalMentions").textContent = summary.total_mentions ?? state.mentions.length;
  document.querySelector("#newMentions").textContent = summary.new_mentions ?? 0;
  document.querySelector("#riskyMentions").textContent = summary.risky_mentions ?? 0;
  document.querySelector("#negativeMentions").textContent = summary.negative_mentions ?? 0;
  document.querySelector("#positiveNeutralMentions").textContent = summary.positive_neutral_mentions ?? 0;
}

function render(payload) {
  document.querySelector("#projectName").textContent = payload.project || "SERP Monitoring Dashboard";
  document.querySelector("#generatedAt").textContent = formatDate(payload.generated_at);
  state.mentions = payload.mentions || [];
  renderSummary(payload.summary || {});
  renderTable();
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
        state.sortDirection = key === "rank" || key === "sentiment" ? "asc" : "desc";
      }
      renderTable();
    });
  });
}

async function loadDashboard() {
  try {
    const response = await fetch("data/results.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
  } catch (error) {
    body.innerHTML = `<tr><td colspan="11" class="empty">Dashboard data is not available yet: ${escapeHtml(error.message)}</td></tr>`;
    screenshots.innerHTML = '<p class="empty">Screenshots will appear after the first monitor run.</p>';
  }
}

bindEvents();
loadDashboard();
