const DATA_URL = "./data/companies.json";
const LAST_SCRAPED = "2025-10-08 06:56 UTC";

const searchInput = document.querySelector("#searchInput");
const batchSelect = document.querySelector("#batchSelect");
const tagSelect = document.querySelector("#tagSelect");
const resultsContainer = document.querySelector("#resultsContainer");
const resultCount = document.querySelector("#resultCount");
const sortSelect = document.querySelector("#sortSelect");
const sortDirectionBtn = document.querySelector("#sortDirection");
const clearFiltersBtn = document.querySelector("#clearFilters");
const downloadBtn = document.querySelector("#downloadJson");
const template = document.querySelector("#companyTemplate");

let companies = [];
let filtered = [];

const sortState = {
  field: sortSelect?.value ?? "batch",
  direction: sortDirectionBtn?.dataset.direction ?? "desc",
};

const RENDER_CHUNK = 40;
let renderedCount = 0;
const sentinel = document.createElement("div");
sentinel.className = "scroll-sentinel";
sentinel.setAttribute("aria-hidden", "true");

const lazyObserver = new IntersectionObserver((entries) => {
  if (entries.some((entry) => entry.isIntersecting)) {
    renderMore(true);
  }
}, {
  rootMargin: "600px 0px",
});

function lower(value) {
  return (value ?? "").toString().toLowerCase();
}

function loadData() {
  console.log("[YC Explorer] Starting data load from", DATA_URL);
  return fetch(DATA_URL)
    .then((response) => {
      if (!response.ok) {
        throw new Error(
          `Failed to load data (${response.status}). Expected static file at ${DATA_URL}.`
        );
      }
      console.log("[YC Explorer] Data response received, status", response.status);
      return response.json();
    })
    .then((data) => {
      console.log("[YC Explorer] Parsed", data.length, "records");
      companies = data;
      populateFilters(data);
      applyFilters();
    })
    .catch((error) => {
      console.error("[YC Explorer] Data load failed:", error);
      resultsContainer.innerHTML = `<div class="empty-state">Error loading dataset: ${error.message}. Ensure <code>${DATA_URL}</code> is deployed and accessible.</div>`;
      resultCount.textContent = "0 companies";
      if (downloadBtn) {
        downloadBtn.disabled = true;
      }
    });
}

function updateSortDirectionButton() {
  if (!sortDirectionBtn) return;
  const isAscending = sortState.direction === "asc";
  sortDirectionBtn.textContent = isAscending ? "Ascending ↑" : "Descending ↓";
  sortDirectionBtn.setAttribute(
    "aria-label",
    `Toggle sort direction (currently ${isAscending ? "ascending" : "descending"})`
  );
  sortDirectionBtn.title = `Sort ${isAscending ? "ascending" : "descending"}`;
  sortDirectionBtn.dataset.direction = sortState.direction;
}

function uniqueValues(getter) {
  const values = new Set();
  for (const record of companies) {
    const result = getter(record);
    if (Array.isArray(result)) {
      result.forEach((value) => {
        if (value) values.add(value);
      });
    } else if (result) {
      values.add(result);
    }
  }
  return Array.from(values);
}

function populateFilters(data) {
  const batches = uniqueValues((record) => record.batch).sort((a, b) =>
    compareBatchLabels(a, b)
  );
  console.log("[YC Explorer] Unique batches discovered:", batches.length);
  batchSelect.innerHTML = batches
    .map((batch) => `<option value="${batch}">${batch}</option>`)
    .join("");

  const tags = uniqueValues((record) => record.tags)
    .map((tag) => ({ tag, count: countTag(data, tag) }))
    .sort((a, b) => a.tag.localeCompare(b.tag));

  console.log("[YC Explorer] Unique tags discovered:", tags.length);
  tagSelect.innerHTML = tags
    .map(
      ({ tag, count }) =>
        `<option value="${tag}">${tag} (${count.toLocaleString()})</option>`
    )
    .join("");
}

function compareBatchLabels(a = "", b = "") {
  const seasonOrder = {
    autumn: 1,
    fall: 1,
    winter: 2,
    spring: 3,
    summer: 4,
  };

  const parseBatch = (value) => {
    const [seasonRaw = "", yearRaw = ""] = value.trim().split(/\s+/);
    const normalizedSeason = seasonRaw.toLowerCase();
    const numericYear = parseInt(yearRaw, 10);
    return {
      normalizedSeason,
      rank: seasonOrder[normalizedSeason] ?? 0,
      year: Number.isNaN(numericYear) ? Number.MIN_SAFE_INTEGER : numericYear,
      original: value,
    };
  };

  const batchA = parseBatch(a);
  const batchB = parseBatch(b);

  if (batchA.year !== batchB.year) {
    return batchB.year - batchA.year;
  }

  if (batchA.rank !== batchB.rank) {
    return batchB.rank - batchA.rank;
  }

  if (batchA.normalizedSeason !== batchB.normalizedSeason) {
    return batchA.normalizedSeason.localeCompare(batchB.normalizedSeason);
  }

  return batchA.original.localeCompare(batchB.original);
}

function countTag(data, tag) {
  return data.reduce(
    (total, record) => total + (record.tags?.includes(tag) ? 1 : 0),
    0
  );
}

function getSelectedValues(select) {
  return Array.from(select.selectedOptions).map((option) => option.value);
}

function recordMatches(record, searchQuery, batches, tags) {
  if (batches.length && !batches.includes(record.batch ?? "")) {
    return false;
  }

  if (tags.length) {
    const recordTags = record.tags ?? [];
    const hasAllTags = tags.every((tag) => recordTags.includes(tag));
    if (!hasAllTags) return false;
  }

  if (!searchQuery) return true;
  const keyword = lower(searchQuery);
  const candidates = [
    record.company_name,
    record.short_description,
    record.long_description,
    record.location,
    record.country,
    record.status,
    record.website,
    record.cb_url,
    record.linkedin_url,
    ...(record.tags ?? []),
    ...(record.founders_names ?? []),
  ];
  return candidates.some((value) => lower(value).includes(keyword));
}

function sortFilteredRecords() {
  const { field, direction } = sortState;
  const directionFactor = direction === "asc" ? 1 : -1;

  filtered.sort((a, b) => {
    if (field === "name") {
      const aName = (a.company_name ?? "").trim();
      const bName = (b.company_name ?? "").trim();
      if (!aName && !bName) return 0;
      if (!aName) return 1;
      if (!bName) return -1;
      const result = aName.localeCompare(bName, undefined, {
        sensitivity: "base",
        ignorePunctuation: true,
      });
      return result * directionFactor;
    }

    if (field === "batch") {
      const batchA = a.batch ?? "";
      const batchB = b.batch ?? "";
      if (!batchA && !batchB) return 0;
      if (!batchA) return 1;
      if (!batchB) return -1;
      const result = compareBatchLabels(batchA, batchB);
      return direction === "asc" ? -result : result;
    }

    return 0;
  });
}

function buildCompanyCard(record) {
  const fragment = template.content.cloneNode(true);
  fragment.querySelector(".company-name").textContent = record.company_name;
  fragment.querySelector(".company-batch").textContent = record.batch ?? "—";
  fragment.querySelector(".company-description").textContent =
    record.short_description || record.long_description || "No description yet.";
  const longElement = fragment.querySelector(".company-long");
  if (record.long_description) {
    longElement.textContent = record.long_description;
  } else {
    longElement.remove();
  }
  fragment.querySelector(".company-status").textContent = record.status ?? "—";
  fragment.querySelector(".company-location").textContent =
    record.location || record.country || "—";
  fragment.querySelector(".company-team").textContent =
    record.team_size ?? "Unknown";
  fragment.querySelector(".company-year").textContent =
    record.year_founded ?? "—";
  populateFounders(fragment, record);

  setLink(fragment.querySelector(".company-website"), record.website, "—", true);
  setLink(fragment.querySelector(".company-cb"), record.cb_url, "—", true);
  setLink(fragment.querySelector(".company-linkedin"), record.linkedin_url, "—");

  const tagsContainer = fragment.querySelector(".tags");
  if (record.tags?.length) {
    record.tags.forEach((tag) => {
      const pill = document.createElement("span");
      pill.textContent = tag;
      tagsContainer.appendChild(pill);
    });
  } else {
    tagsContainer.remove();
  }

  return fragment;
}

function applyFilters() {
  const searchQuery = searchInput.value.trim();
  const batches = getSelectedValues(batchSelect);
  const tags = getSelectedValues(tagSelect);

  console.log(
    "[YC Explorer] Applying filters",
    JSON.stringify({ searchQuery, batches, tags })
  );
  filtered = companies.filter((record) =>
    recordMatches(record, searchQuery, batches, tags)
  );
  sortFilteredRecords();

  console.log("[YC Explorer] Filtered down to", filtered.length, "records");
  renderResults();
}

function renderResults() {
  resultCount.textContent = `${filtered.length.toLocaleString()} companies`;
  if (downloadBtn) {
    downloadBtn.disabled = filtered.length === 0;
  }
  lazyObserver.disconnect();
  renderedCount = 0;

  if (!filtered.length) {
    resultsContainer.innerHTML =
      '<div class="empty-state">No companies match your filters yet.</div>';
    return;
  }

  resultsContainer.innerHTML = "";
  resultsContainer.appendChild(sentinel);
  renderMore();

  if (renderedCount < filtered.length) {
    lazyObserver.observe(sentinel);
  } else {
    sentinel.classList.remove("is-active");
    sentinel.remove();
  }
}

function renderMore(isObserverTrigger = false) {
  if (isObserverTrigger) {
    sentinel.classList.add("is-active");
  } else {
    sentinel.classList.remove("is-active");
  }

  if (!filtered.length || renderedCount >= filtered.length) {
    lazyObserver.disconnect();
    sentinel.classList.remove("is-active");
    sentinel.remove();
    return;
  }

  const end = Math.min(renderedCount + RENDER_CHUNK, filtered.length);
  const fragment = document.createDocumentFragment();

  for (let index = renderedCount; index < end; index += 1) {
    fragment.appendChild(buildCompanyCard(filtered[index]));
  }

  resultsContainer.insertBefore(fragment, sentinel);
  renderedCount = end;
  sentinel.classList.remove("is-active");

  if (renderedCount >= filtered.length) {
    lazyObserver.disconnect();
    sentinel.remove();
  }
}

function clearFilters() {
  searchInput.value = "";
  batchSelect.selectedIndex = -1;
  tagSelect.selectedIndex = -1;
  applyFilters();
}

function downloadFilteredCompanies() {
  if (!filtered.length) return;

  const timestamp = new Date().toISOString().split("T")[0];
  const blob = new Blob([JSON.stringify(filtered, null, 2)], {
    type: "application/json",
  });
  const tempUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = tempUrl;
  link.download = `yc-companies-filtered-${timestamp}.json`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(tempUrl);
}

function extractHost(url) {
  try {
    return new URL(url).host;
  } catch (error) {
    return url.replace(/^https?:\/\//i, "");
  }
}

function setLink(element, url, emptyText = "—", useHost = false) {
  if (!element) return;
  element.classList.remove("is-empty");
  if (url) {
    element.href = url;
    element.textContent = useHost ? extractHost(url) : url;
  } else {
    element.textContent = emptyText;
    element.classList.add("is-empty");
    element.removeAttribute("href");
  }
}

function populateFounders(fragment, record) {
  const container = fragment.querySelector(".company-founders");
  if (!container) return;

  const details = record.founder_details?.filter(
    (founder) => founder?.name || founder?.bio
  );

  if (details?.length) {
    container.innerHTML = "";
    details.forEach((founder) => {
      const profile = document.createElement("div");
      profile.className = "founder-profile";

      const header = document.createElement("div");
      header.className = "founder-header";

      const name = document.createElement(
        founder.linkedin_url ? "a" : "span"
      );
      name.className = "founder-name";
      name.textContent = founder.name ?? "Unknown founder";
      if (founder.linkedin_url) {
        name.href = founder.linkedin_url;
        name.target = "_blank";
        name.rel = "noopener";
      }
      header.appendChild(name);

      if (founder.title) {
        const title = document.createElement("span");
        title.className = "founder-title";
        title.textContent = founder.title;
        header.appendChild(title);
      }

      profile.appendChild(header);

      if (founder.bio) {
        const bio = document.createElement("p");
        bio.className = "founder-bio";
        bio.textContent = founder.bio;
        profile.appendChild(bio);
      }

      container.appendChild(profile);
    });
    return;
  }

  const names = record.founders_names?.filter(Boolean) ?? [];
  container.textContent = names.length ? names.join(", ") : "—";
}

searchInput.addEventListener("input", debounce(applyFilters, 200));
batchSelect.addEventListener("change", applyFilters);
tagSelect.addEventListener("change", applyFilters);
clearFiltersBtn.addEventListener("click", clearFilters);
if (downloadBtn) {
  downloadBtn.addEventListener("click", downloadFilteredCompanies);
  downloadBtn.disabled = true;
}

if (sortSelect) {
  sortSelect.addEventListener("change", () => {
    sortState.field = sortSelect.value;
    applyFilters();
  });
}

if (sortDirectionBtn) {
  sortDirectionBtn.addEventListener("click", () => {
    sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
    updateSortDirectionButton();
    applyFilters();
  });
  updateSortDirectionButton();
}

function debounce(callback, delay = 200) {
  let timeoutId;
  return (...args) => {
    window.clearTimeout(timeoutId);
    timeoutId = window.setTimeout(() => callback(...args), delay);
  };
}

loadData();

const lastScrapedElement = document.querySelector("#lastScraped");
if (lastScrapedElement) {
  lastScrapedElement.textContent = `Last scraped: ${LAST_SCRAPED}`;
}
document.title = `YC Directory Explorer · Last scraped ${LAST_SCRAPED}`;
