const profileForm = document.querySelector("#profile-form");
const tenderForm = document.querySelector("#tender-form");
const fileInput = document.querySelector("#tender-file");
const dropZone = document.querySelector("#drop-zone");
const fileLabel = document.querySelector("#file-label");
const fileDetail = document.querySelector("#file-detail");
const profileStatus = document.querySelector("#profile-status");
const uploadError = document.querySelector("#upload-error");
const analyzeButton = document.querySelector("#analyze-button");
const maxFileBytes = 12 * 1024 * 1024;
const maxBundleBytes = 40 * 1024 * 1024;
const supportedExtensions = new Set([
  ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".txt", ".csv", ".html", ".htm",
  ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp",
]);

function splitList(value) {
  return value.split(/[,\n]/).map((part) => part.trim()).filter(Boolean);
}

function setMessage(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error-message", isError);
  element.hidden = !message;
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => `${item.loc.slice(1).join(".") || "Input"}: ${item.msg}`).join("; ")
      : body.detail;
    throw new Error(detail || "Something went wrong. Please try again.");
  }
  return body;
}

function readProfileForm() {
  const values = Object.fromEntries(new FormData(profileForm).entries());
  return {
    business_name: values.business_name,
    country: values.country,
    sector: values.sector,
    years_operating: Number(values.years_operating),
    annual_revenue: Number(values.annual_revenue),
    currency: values.currency.toUpperCase(),
    registration_number: values.registration_number,
    certifications: splitList(values.certifications || ""),
    capabilities: splitList(values.capabilities || ""),
    geographic_regions: splitList(values.geographic_regions || ""),
  };
}

profileForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setMessage(profileStatus, "Saving…");
  try {
    await requestJson("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readProfileForm()),
    });
    setMessage(profileStatus, "Profile saved on this device.");
    refreshHistory();
  } catch (error) {
    setMessage(profileStatus, error.message, true);
  }
});

function updateSelectedFiles(files) {
  if (!files.length) {
    fileLabel.textContent = "Drop tender documents here";
    fileDetail.textContent = "or browse files · PDF, Office, text, CSV, HTML and images";
    return;
  }
  fileLabel.textContent = `${files.length} document${files.length === 1 ? "" : "s"} selected`;
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  fileDetail.textContent = `${files.map((file) => file.name).join(" · ")} · ${(totalBytes / 1024 / 1024).toFixed(2)} MB total`;
  setMessage(uploadError, "");
}

fileInput.addEventListener("change", () => updateSelectedFiles(Array.from(fileInput.files)));
for (const eventName of ["dragenter", "dragover"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
}
dropZone.addEventListener("drop", (event) => {
  const files = Array.from(event.dataTransfer.files);
  if (files.length) {
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    fileInput.files = transfer.files;
    updateSelectedFiles(files);
  }
});

tenderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = Array.from(fileInput.files);
  if (!files.length) return setMessage(uploadError, "Choose at least one tender document.", true);
  if (files.length > 20) return setMessage(uploadError, "Choose no more than 20 files.", true);
  const totalBytes = files.reduce((total, file) => total + file.size, 0);
  if (totalBytes > maxBundleBytes) return setMessage(uploadError, "The combined upload must be 40 MB or smaller.", true);
  const names = new Set();
  for (const file of files) {
    const extension = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!supportedExtensions.has(extension)) {
      return setMessage(uploadError, `Unsupported file type: ${file.name}. Choose PDF, Office, text, CSV, HTML or image files.`, true);
    }
    if (file.size > maxFileBytes) return setMessage(uploadError, `${file.name} is larger than 12 MB.`, true);
    const normalizedName = file.name.toLowerCase();
    if (names.has(normalizedName)) return setMessage(uploadError, `Remove duplicate filenames before uploading: ${file.name}.`, true);
    names.add(normalizedName);
  }
  const data = new FormData();
  files.forEach((file) => data.append("files", file));
  analyzeButton.disabled = true;
  analyzeButton.querySelector("span:first-child").textContent = "Reading and analyzing tender…";
  setMessage(uploadError, "");
  try {
    const result = await requestJson("/api/analyses", { method: "POST", body: data });
    renderAnalysis(result);
    await refreshHistory();
  } catch (error) {
    setMessage(uploadError, error.message, true);
  } finally {
    analyzeButton.disabled = false;
    analyzeButton.querySelector("span:first-child").textContent = "Analyze this tender";
  }
});

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

function sourceMarkup(sources = [], fallbackDocument = "Tender.pdf") {
  if (!sources.length) return "";
  return `<span class="source-list">${sources.map((source) => {
    const location = source.location || (source.page ? `Page ${source.page}` : "Source");
    const document = source.location ? source.document : fallbackDocument;
    return `<details class="source-chip"><summary>${escapeHtml(document || fallbackDocument)} · ${escapeHtml(location)}</summary><div class="source-quote">“${escapeHtml(source.quote)}”</div></details>`;
  }).join("")}</span>`;
}

function listMarkup(items, title, detail, sources, fallbackDocument) {
  if (!items.length) return '<p class="empty-list">No items identified in the tender.</p>';
  return `<ul class="result-list">${items.map((item) =>
    `<li><span class="list-title">${escapeHtml(title(item))}</span><span class="list-detail">${escapeHtml(detail(item))}</span>${sourceMarkup(item[sources], fallbackDocument)}</li>`
  ).join("")}</ul>`;
}

function renderAnalysis(record) {
  const data = record.analysis;
  const results = document.querySelector("#analysis-results");
  const resultsSection = document.querySelector("#results-section");
  document.querySelector("#result-title").textContent = record.file_name;
  const pageSummary = record.page_count ? ` · ${record.page_count} pages` : "";
  document.querySelector("#analysis-meta").textContent = `${record.document_count} document${record.document_count === 1 ? "" : "s"}${pageSummary} · ${new Date(record.created_at + "Z").toLocaleString()}`;
  results.innerHTML = `
    <div class="result-grid">
      <article class="result-card eligibility-card">
        <div><h3>Eligibility assessment</h3><span class="eligibility-label ${escapeHtml(data.eligibility)}">${escapeHtml(data.eligibility)}</span></div>
        <p>${escapeHtml(data.rationale.text)}${sourceMarkup(data.rationale.sources, record.file_name)}</p>
      </article>
      <article class="result-card"><h3>Mandatory documents</h3>${listMarkup(data.mandatory_documents, (item) => item.name, (item) => item.reason, "sources", record.file_name)}</article>
      <article class="result-card"><h3>Key deadlines</h3>${listMarkup(data.key_deadlines, (item) => item.name, (item) => item.date, "sources", record.file_name)}</article>
      <article class="result-card"><h3>Missing requirements</h3>${listMarkup(data.missing_requirements, (item) => item.requirement, (item) => item.profile_gap, "sources", record.file_name)}</article>
      <article class="result-card field-wide"><h3>Submission plan</h3>${listMarkup(data.submission_plan, (item) => item.step, (item) => item.due_date || "Suggested next step", "sources", record.file_name)}</article>
    </div>`;
  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function refreshHistory() {
  try {
    const records = await requestJson("/api/analyses");
    const section = document.querySelector("#history-section");
    const list = document.querySelector("#history-list");
    const historyError = document.querySelector("#history-error");
    historyError.hidden = true;
    section.hidden = !records.length;
    list.innerHTML = records.map((record) =>
      `<button class="history-item" type="button" data-analysis-id="${record.id}">${escapeHtml(record.file_name)}<small>${record.document_count} document${record.document_count === 1 ? "" : "s"} · ${new Date(record.created_at + "Z").toLocaleDateString()}</small></button>`
    ).join("");
    list.querySelectorAll("[data-analysis-id]").forEach((button) => {
      button.addEventListener("click", async () => {
        try {
          renderAnalysis(await requestJson(`/api/analyses/${button.dataset.analysisId}`));
        } catch (error) {
          setMessage(uploadError, error.message, true);
        }
      });
    });
  } catch (error) {
    const section = document.querySelector("#history-section");
    const historyError = document.querySelector("#history-error");
    section.hidden = false;
    historyError.textContent = `Could not load saved analyses: ${error.message}`;
    historyError.hidden = false;
  }
}

refreshHistory();
