const API_BASE = "";

let MAX_URLS = 10;
let queue = [];          // [{ url, name, status, error }]
let isRunning = false;
// карточки анализа: один ключ = один обменник, даже если файлов несколько
const analysisCards = {};

// --- вкладки ---
document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "summary") loadSummary();
    if (btn.dataset.tab === "history") loadHistory();
  });
});

// --- общие помощники ---
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

function shortWhen(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").slice(0, 16);
}

function flagsHtml(redFlags = [], greenFlags = []) {
  let html = "";
  greenFlags.forEach((f) => (html += `<span class="badge badge-green">${escapeHtml(f)}</span>`));
  redFlags.forEach((f) => (html += `<span class="badge badge-red">${escapeHtml(f)}</span>`));
  return html || '<span class="hint">Флагов не найдено</span>';
}

function listHtml(items = []) {
  if (!items.length) return '<span class="hint">—</span>';
  return `<ul>${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
}

function scoreChip(label, value) {
  if (value === null || value === undefined) return "";
  const tone = value >= 7 ? "chip-good" : value >= 4 ? "chip-mid" : "chip-bad";
  return `<span class="chip ${tone}">${label} ${value}/10</span>`;
}

function analysisHtml(analysis) {
  if (!analysis) return '<p class="hint">Анализ не получен</p>';

  const payouts = (analysis.payout_methods || []).join(", ");
  const meta = [
    scoreChip("Доверие", analysis.trust_score),
    scoreChip("Дизайн", analysis.design_score),
    analysis.exchanger_rate != null
      ? `<span class="chip">${analysis.exchanger_rate} ₽/USDT${
          analysis.rate_source ? ` · ${escapeHtml(analysis.rate_source)}` : ""
        }</span>`
      : "",
    payouts ? `<span class="chip">${escapeHtml(payouts)}</span>` : "",
  ].join("");

  return `
    <div class="meta">${meta}</div>
    <p class="summary">${escapeHtml(analysis.summary || "")}</p>
    ${analysis.enrichment_note
      ? `<p class="hint">${escapeHtml(analysis.enrichment_note)}</p>`
      : ""}
    <div class="flags">${flagsHtml(analysis.red_flags, analysis.green_flags)}</div>
    <details><summary>Сильные / слабые стороны, рекомендации</summary>
      <div class="cols">
        <div><span class="label">Плюсы</span>${listHtml(analysis.strengths)}</div>
        <div><span class="label">Минусы</span>${listHtml(analysis.weaknesses)}</div>
        <div><span class="label">Уникальное</span>${listHtml(analysis.unique_offers)}</div>
        <div><span class="label">Рекомендации</span>${listHtml(analysis.recommendations)}</div>
      </div>
    </details>
  `;
}

function reviewsHtml(reviews) {
  if (!reviews) return "";

  const sources = (reviews.sources || [])
    .map((s) => {
      const mark = s.status === "ok" ? "✓" : s.status === "пусто" ? "–" : "!";
      const note = s.note ? ` — ${escapeHtml(s.note)}` : "";
      return `<li>${mark} <a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a>${note}</li>`;
    })
    .join("");

  if (!reviews.reviews_found) {
    return `
      <p class="hint">${escapeHtml(reviews.summary || "Отзывы не найдены")}</p>
      <details><summary>Какие площадки проверены</summary><ul class="sources">${sources || "<li>—</li>"}</ul></details>
    `;
  }

  return `
    <div class="meta">
      <span class="chip chip-label">Отзывы</span>
      ${scoreChip("Доверие", reviews.reviews_trust_score)}
    </div>
    <p class="summary">${escapeHtml(reviews.summary || "")}</p>
    <div class="flags">${flagsHtml(reviews.red_flags, reviews.green_flags)}</div>
    <details><summary>Цитаты из отзывов</summary>
      <div class="cols">
        <div><span class="label">Жалобы</span>${listHtml(reviews.complaint_quotes)}</div>
        <div><span class="label">Позитив</span>${listHtml(reviews.positive_quotes)}</div>
        <div><span class="label">Отброшено как голословное</span>${listHtml(reviews.ignored_claims)}</div>
      </div>
    </details>
    <details><summary>Какие площадки проверены</summary><ul class="sources">${sources || "<li>—</li>"}</ul></details>
  `;
}

function addResultCard(html) {
  document.getElementById("analyze-results").insertAdjacentHTML("afterbegin", html);
}

function cardIdentity(data, filename) {
  if (data && data.profile && data.profile.key) return data.profile.key;
  if (data && data.url) {
    try {
      return new URL(data.url).hostname.replace(/^www\./, "").split(".")[0];
    } catch (e) {
      /* не ссылка */
    }
  }
  const raw = (data && data.competitor_name) || filename || "";
  return raw.toLowerCase().replace(/https?:\/\//, "").replace(/[^a-z0-9а-яё]/gi, "");
}

function upsertResultCard(key, html) {
  const wrap = document.getElementById("analyze-results");
  const marked = html.replace(/class="result"/, `class="result" data-card-key="${escapeHtml(key)}"`);
  const existing = wrap.querySelector(`[data-card-key="${key}"]`);
  if (existing) existing.outerHTML = marked;
  else wrap.insertAdjacentHTML("afterbegin", marked);
}

function sameCompetitor(a, b) {
  const norm = (value) => (value || "").toLowerCase().replace(/[^a-z0-9а-яё]/gi, "");
  const left = norm(a);
  const right = norm(b);
  return Boolean(left && right && left === right);
}

function removeBtn(name) {
  if (!name) return "";
  return `<button type="button" class="link-btn remove-competitor" data-remove-competitor="${escapeHtml(name)}">удалить</button>`;
}

async function removeCompetitor(name) {
  if (!name) return;
  if (!confirm(`Удалить «${name}» из сравнения, истории и анализа?`)) return;

  const res = await fetch(`${API_BASE}/competitors/remove`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ competitor_name: name }),
  });
  if (!res.ok) {
    alert("Не нашёл этого конкурента в сохранённых данных");
    return;
  }

  queue = queue.filter((item) => !sameCompetitor(item.name, name));
  renderQueue();

  document.querySelectorAll("[data-competitor]").forEach((card) => {
    if (sameCompetitor(card.dataset.competitor, name)) card.remove();
  });
  document.querySelectorAll("[data-card-key]").forEach((card) => {
    if (sameCompetitor(card.dataset.competitor, name) || sameCompetitor(card.dataset.cardKey, name)) {
      card.remove();
    }
  });
  Object.keys(analysisCards).forEach((key) => {
    const stored = analysisCards[key];
    if (sameCompetitor(key, name) || sameCompetitor(stored.data && stored.data.competitor_name, name)) {
      delete analysisCards[key];
    }
  });

  if (document.getElementById("tab-summary").classList.contains("active")) loadSummary();
  if (document.getElementById("tab-history").classList.contains("active")) loadHistory();
}

document.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-remove-competitor]");
  if (!btn) return;
  removeCompetitor(btn.dataset.removeCompetitor);
});

// === Очередь ссылок ===

async function loadConfig() {
  try {
    const res = await fetch(`${API_BASE}/config`);
    const data = await res.json();
    MAX_URLS = data.max_urls_per_batch || 10;
    document.getElementById("sites-limit").textContent = MAX_URLS;
    if (data.supported_formats) {
      document.getElementById("formats-hint").textContent = `Поддерживаются: ${data.supported_formats}`;
    }
  } catch (e) {
    /* оставляем значения по умолчанию */
  }
}

async function addUrls(raw) {
  if (!raw || !raw.trim()) return;

  const res = await fetch(`${API_BASE}/urls/parse`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw }),
  });
  const data = await res.json();

  if (!data.items.length) {
    alert("Не нашёл ни одной ссылки в тексте");
    return;
  }

  let added = 0;
  let overflow = 0;
  data.items.forEach((item) => {
    const key = item.url.replace(/\/+$/, "").toLowerCase();
    if (queue.some((q) => q.url.replace(/\/+$/, "").toLowerCase() === key)) return;
    if (queue.length >= MAX_URLS) {
      overflow += 1;
      return;
    }
    queue.push({ url: item.url, name: item.competitor_name, status: "ожидает", error: null });
    added += 1;
  });

  renderQueue();
  if (overflow || data.skipped) {
    alert(`Добавлено ${added}. Лимит — ${MAX_URLS} ссылок, лишние не добавлены.`);
  }
}

function renderQueue() {
  const wrap = document.getElementById("site-queue");
  const runBtn = document.getElementById("site-run-btn");

  if (!queue.length) {
    wrap.innerHTML = '<p class="hint">Очередь пуста</p>';
    runBtn.disabled = true;
    runBtn.textContent = "Проанализировать";
    return;
  }

  const rows = queue
    .map((item, index) => {
      const statusClass =
        item.status === "готово" ? "status-ok" : item.status === "ошибка" ? "status-err" : "status-wait";
      const note = item.error ? `<br><span class="hint">${escapeHtml(item.error)}</span>` : "";
      return `<tr>
        <td>${index + 1}</td>
        <td>${escapeHtml(item.name)}</td>
        <td class="url-cell">${escapeHtml(item.url)}</td>
        <td class="${statusClass}">${escapeHtml(item.status)}${note}</td>
        <td><button class="link-btn" data-remove="${index}" ${isRunning ? "disabled" : ""}>убрать</button></td>
      </tr>`;
    })
    .join("");

  wrap.innerHTML = `<table><thead><tr>
      <th>#</th><th>Конкурент</th><th>Ссылка</th><th>Статус</th><th></th>
    </tr></thead><tbody>${rows}</tbody></table>`;

  wrap.querySelectorAll("[data-remove]").forEach((btn) => {
    btn.addEventListener("click", () => {
      queue.splice(Number(btn.dataset.remove), 1);
      renderQueue();
    });
  });

  const pending = queue.filter((i) => i.status === "ожидает").length;
  runBtn.disabled = isRunning || pending === 0;
  runBtn.textContent = pending ? `Проанализировать (${pending})` : "Всё проанализировано";
}

document.getElementById("site-add-btn").addEventListener("click", async () => {
  const input = document.getElementById("site-single-input");
  await addUrls(input.value);
  input.value = "";
});

document.getElementById("site-single-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("site-add-btn").click();
});

document.getElementById("site-add-list-btn").addEventListener("click", async () => {
  const input = document.getElementById("site-list-input");
  await addUrls(input.value);
  input.value = "";
});

document.getElementById("site-clear-btn").addEventListener("click", () => {
  if (isRunning) return;
  queue = [];
  renderQueue();
});

document.getElementById("site-run-btn").addEventListener("click", async () => {
  if (isRunning) return;
  const includeReviews = document.getElementById("site-include-reviews").checked;

  isRunning = true;
  renderQueue();

  for (const item of queue) {
    if (item.status !== "ожидает") continue;

    item.status = "анализирую…";
    item.error = null;
    renderQueue();

    try {
      const res = await fetch(`${API_BASE}/analyze/url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: item.url, include_reviews: includeReviews }),
      });
      const data = await res.json();

      if (data.success) {
        item.status = "готово";
        upsertAnalysisCard(data.data, item.url || item.name, "site", includeReviews);
      } else {
        item.status = "ошибка";
        item.error = data.error || "неизвестная ошибка";
      }
    } catch (e) {
      item.status = "ошибка";
      item.error = String(e);
    }
    renderQueue();
  }

  isRunning = false;
  renderQueue();
});

function upsertAnalysisCard(data, filename, kind, includeReviews) {
  if (data && data.kind === "table") {
    addResultCard(fileCardHtml(data, [filename]));
    return;
  }

  const key = cardIdentity(data, filename);
  const prev = analysisCards[key] || { files: [], data: {}, includeReviews: false };
  if (filename && !prev.files.includes(filename)) prev.files.push(filename);
  prev.data = Object.assign({}, prev.data, data);
  if (includeReviews !== undefined) prev.includeReviews = includeReviews;
  if (data.profile) prev.data.profile = data.profile;
  analysisCards[key] = prev;

  const html = kind === "site"
    ? siteCardHtml(prev.data, prev.includeReviews, prev.files)
    : fileCardHtml(prev.data, prev.files);
  upsertResultCard(key, html);
}

function filesCaption(files, fallback) {
  if (!files || !files.length) return fallback || "";
  if (files.length === 1) return fallback || files[0];
  return `из ${files.length} файлов: ${files.join(", ")}`;
}

function siteCardHtml(data, includeReviews, files) {
  const shot = data.screenshot_base64
    ? `<details><summary>Скриншот страницы</summary>
         <img class="shot" src="data:image/png;base64,${data.screenshot_base64}" alt="скриншот">
       </details>`
    : "";

  const reviewsBlock = includeReviews && data.reviews ? `<hr>${reviewsHtml(data.reviews)}` : "";
  const name = data.competitor_name || data.url || "";
  const extra = files && files.length > 1
    ? `<p class="hint">${escapeHtml(filesCaption(files))}</p>`
    : "";

  return `<div class="result" data-competitor="${escapeHtml(name)}">
    <h3>${escapeHtml(name)}
      <a href="${escapeHtml(data.url || "")}" target="_blank" rel="noopener">${escapeHtml(data.url || "")}</a>
      ${removeBtn(name)}
    </h3>
    ${extra}
    ${analysisHtml(data.text_analysis)}
    ${reviewsBlock}
    ${shot}
  </div>`;
}

// === Файлы: скрин, PDF, сохранённая страница, заметка ===

document.getElementById("file-analyze-btn").addEventListener("click", async () => {
  const btn = document.getElementById("file-analyze-btn");
  const fileInput = document.getElementById("file-input");
  const nameEl = document.getElementById("file-competitor-name");

  if (!fileInput.files.length) {
    alert("Выбери хотя бы один файл");
    return;
  }

  btn.disabled = true;
  const total = fileInput.files.length;
  let done = 0;

  for (const file of fileInput.files) {
    done += 1;
    btn.textContent = `Анализирую ${done} из ${total}… если не хватает — зайду на сайт`;
    nameEl.value = "";

    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await fetch(`${API_BASE}/analyze/file`, { method: "POST", body: formData });
      const data = await res.json();

      if (!data.success) {
        addResultCard(`<div class="result">
          <h3>${escapeHtml(file.name)}</h3>
          <p class="hint">Ошибка: ${escapeHtml(data.error || "неизвестная")}</p>
        </div>`);
        continue;
      }

      nameEl.value = data.competitor_name || "";
      if (data.kind === "table" && data.queued_urls && data.queued_urls.length) {
        await addUrls(data.queued_urls.join("\n"));
      }
      upsertAnalysisCard(data, file.name, "file");
    } catch (e) {
      addResultCard(`<div class="result">
        <h3>${escapeHtml(file.name)}</h3>
        <p class="hint">Ошибка запроса: ${escapeHtml(String(e))}</p>
      </div>`);
    }
  }

  btn.textContent = "Проанализировать файлы";
  btn.disabled = false;
  fileInput.value = "";
});

function fileCardHtml(data, files) {
  const list = files && files.length ? files : [""];
  const filename = list[list.length - 1] || "";
  const kindLabel = {
    image: "скриншот",
    pdf: "PDF",
    html: "страница сайта",
    text: "заметка",
    docx: "Word",
    xlsx: "таблица",
    csv: "таблица",
    table: "таблица конкурентов",
  }[data.kind] || data.kind;
  const cardName = data.competitor_name || filename;
  const sourceLine = filesCaption(list, `${kindLabel} · ${filename}`);
  const head = `<h3>${escapeHtml(cardName)}
      <a>${escapeHtml(sourceLine)}</a>
      ${data.kind === "table" ? "" : removeBtn(cardName)}
    </h3>`;

  if (data.kind === "table") {
    const rows = (data.table_rows || [])
      .map((row) => {
        const link = row.url
          ? `<a href="${escapeHtml(row.url)}" target="_blank" rel="noopener">${escapeHtml(row.url)}</a>`
          : "—";
        const rate = row.exchanger_rate != null ? row.exchanger_rate : "—";
        return `<tr data-competitor="${escapeHtml(row.competitor_name)}">
          <td>${escapeHtml(row.competitor_name)}</td>
          <td>${link}</td>
          <td>${rate}</td>
          <td>${escapeHtml(row.notes || "—")}</td>
          <td>${removeBtn(row.competitor_name)}</td>
        </tr>`;
      })
      .join("");
    const queued = (data.queued_urls || []).length;
    return `<div class="result">
      ${head}
      <p class="summary">В файле список конкурентов, не один документ.
        Данные из ячеек уже записаны в профили.
        ${queued ? `Ссылки (${queued}) добавлены в очередь выше — нажми «Проанализировать», чтобы открыть сайты.` : "Ссылок в таблице не было, остались только данные из ячеек."}
      </p>
      <table><thead><tr><th>Конкурент</th><th>Сайт</th><th>Курс</th><th>Из таблицы</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>
    </div>`;
  }

  const sourceNames = list.filter(Boolean).join(", ")
    || ((data.profile && data.profile.sources) || []).map((s) => s.label).join(", ");
  const merged = list.length > 1 || (data.profile && data.profile.sources_count > 1)
    ? `<p class="hint">Один обменник, материалы сложились. Источники: ${escapeHtml(sourceNames)}</p>`
    : "";

  if (data.image_analysis) {
    const a = data.image_analysis;
    return `<div class="result" data-competitor="${escapeHtml(cardName)}">
      ${head}
      ${merged}
      <div class="meta">${scoreChip("Дизайн", a.design_score)}</div>
      <p class="summary">${escapeHtml(a.description)}</p>
      <div class="flags">${flagsHtml([], a.safety_claims)}</div>
      <details><summary>Инсайты и рекомендации</summary>
        <div class="cols">
          <div><span class="label">Маркетинг</span>${listHtml(a.marketing_insights)}</div>
          <div><span class="label">Рекомендации</span>${listHtml(a.recommendations)}</div>
        </div>
      </details>
    </div>`;
  }

  return `<div class="result" data-competitor="${escapeHtml(cardName)}">${head}${merged}${analysisHtml(data.text_analysis)}</div>`;
}

// === Текст ===

document.getElementById("text-analyze-btn").addEventListener("click", async () => {
  const btn = document.getElementById("text-analyze-btn");
  const text = document.getElementById("text-input").value;
  const competitor_name = document.getElementById("text-competitor-name").value || null;
  const rateVal = document.getElementById("text-exchanger-rate").value;

  if (text.trim().length < 10) {
    alert("Текст слишком короткий");
    return;
  }

  btn.disabled = true;
  btn.textContent = "Анализирую… если не хватает данных — зайду на сайт";

  try {
    const res = await fetch(`${API_BASE}/analyze/text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        competitor_name,
        exchanger_rate: rateVal ? parseFloat(rateVal) : null,
      }),
    });
    const data = await res.json();

    if (data.success) {
      const title = competitor_name || data.analysis.detected_name || "Текст";
      upsertAnalysisCard(
        { competitor_name: title, kind: "text", text_analysis: data.analysis },
        "вставленный текст",
        "file",
      );
    } else {
      alert(`Ошибка: ${data.error}`);
    }
  } catch (e) {
    alert(`Ошибка запроса: ${e}`);
  } finally {
    btn.textContent = "Проанализировать текст";
    btn.disabled = false;
  }
});

// === Папки data/ ===

document.getElementById("batch-analyze-btn").addEventListener("click", async () => {
  const btn = document.getElementById("batch-analyze-btn");
  const resultEl = document.getElementById("batch-result");

  btn.disabled = true;
  resultEl.textContent = "Анализирую папки data/. Скупые notes.md дозаполняются с сайта — это может занять несколько минут.";

  try {
    const res = await fetch(`${API_BASE}/analyze/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    let html = `<p>Обработано: ${data.results.length}, ошибок: ${data.errors.length}</p>`;
    data.results.forEach((r) => {
      html += `<h3>${escapeHtml(r.competitor_name || r.url)}</h3>${analysisHtml(r.text_analysis)}`;
    });
    if (data.errors.length) {
      html += `<p class="hint">${escapeHtml(data.errors.join("; "))}</p>`;
    }
    resultEl.innerHTML = html;
  } catch (e) {
    resultEl.textContent = `Ошибка запроса: ${e}`;
  } finally {
    btn.disabled = false;
  }
});

// === Отзывы ===

document.getElementById("reviews-run-btn").addEventListener("click", async () => {
  const btn = document.getElementById("reviews-run-btn");
  const url = document.getElementById("reviews-url-input").value;
  const extraRaw = document.getElementById("reviews-extra-input").value;
  const resultEl = document.getElementById("reviews-result");

  if (!url.trim()) {
    resultEl.textContent = "Вставь ссылку на обменник";
    return;
  }

  const extra_urls = extraRaw
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);

  btn.disabled = true;
  resultEl.textContent = "Ищу отзывы на площадках (до минуты)...";

  try {
    const res = await fetch(`${API_BASE}/analyze/reviews`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, extra_urls }),
    });
    const data = await res.json();
    if (data.success) {
      resultEl.innerHTML = `<h3>${escapeHtml(data.competitor_name || "")} ${removeBtn(data.competitor_name)}</h3>${reviewsHtml(data.reviews)}`;
      if (data.competitor_name) resultEl.dataset.competitor = data.competitor_name;
    } else {
      resultEl.textContent = `Ошибка: ${data.error}`;
    }
  } catch (e) {
    resultEl.textContent = `Ошибка запроса: ${e}`;
  } finally {
    btn.disabled = false;
  }
});

// === Сравнение ===

document.getElementById("summary-refresh-btn").addEventListener("click", refreshRates);
document.getElementById("summary-export-btn").addEventListener("click", () => {
  window.location.href = `${API_BASE}/summary/export`;
});

async function loadSummary() {
  const rateEl = document.getElementById("summary-cbr-rate");
  const wrapEl = document.getElementById("summary-table-wrap");
  wrapEl.textContent = "Загружаю...";

  try {
    const res = await fetch(`${API_BASE}/summary`);
    const data = await res.json();

    rateEl.textContent = data.cbr_rate
      ? `Курс ЦБ РФ на ${data.cbr_rate.date}: ${data.cbr_rate.usd_rub} ₽/USD`
      : "Курс ЦБ недоступен";

    if (!data.rows.length) {
      wrapEl.innerHTML = '<p class="hint">Пока нет проанализированных конкурентов</p>';
      return;
    }

    let html = `<table><thead><tr>
      <th>Конкурент</th><th>Материалы</th><th>Доверие</th><th>Дизайн</th><th>Курс ₽/USDT</th><th>К курсу ЦБ</th>
      <th>Способы выплаты</th><th>Флаги</th><th></th>
    </tr></thead><tbody>`;
    data.rows.forEach((r) => {
      const extraNames = (r.aliases || []).length ? ` <span class="hint">${escapeHtml(r.aliases.join(", "))}</span>` : "";
      const nameCell = r.source_url
        ? `<a href="${escapeHtml(r.source_url)}" target="_blank" rel="noopener">${escapeHtml(r.competitor_name)}</a>${extraNames}`
        : `${escapeHtml(r.competitor_name)}${extraNames}`;
      const spread = r.rate_spread === null || r.rate_spread === undefined
        ? "—"
        : `${r.rate_spread > 0 ? "+" : ""}${r.rate_spread}`;
      const materials = (r.materials || []).length
        ? `<div>${r.sources_count || r.materials.length}</div><div class="hint">${escapeHtml(r.materials.join(" · "))}</div>`
        : `${r.sources_count || "—"}`;
      const trust = r.trust_score != null
        ? `<div>${r.trust_score}</div>${r.trust_note ? `<div class="hint">${escapeHtml(r.trust_note)}</div>` : ""}`
        : `<div>—</div>${r.trust_note ? `<div class="hint">${escapeHtml(r.trust_note)}</div>` : ""}`;
      const design = r.design_score != null
        ? `<div>${r.design_score}</div>${r.design_note ? `<div class="hint">${escapeHtml(r.design_note)}</div>` : ""}`
        : `<div>—</div>${r.design_note ? `<div class="hint">${escapeHtml(r.design_note)}</div>` : ""}`;
      const rateHint = [r.rate_source, shortWhen(r.rate_fetched_at)].filter(Boolean).join(" · ");
      const rateCell = r.exchanger_rate != null
        ? `<div>${r.exchanger_rate}</div>${rateHint ? `<div class="hint">${escapeHtml(rateHint)}</div>` : ""}`
        : "—";
      html += `<tr>
        <td>${nameCell}</td>
        <td>${materials}</td>
        <td>${trust}</td>
        <td>${design}</td>
        <td>${rateCell}</td>
        <td>${spread}</td>
        <td>${escapeHtml((r.payout_methods || []).join(", "))}</td>
        <td>${flagsHtml(r.red_flags, r.green_flags)}</td>
        <td>${removeBtn(r.competitor_name)}</td>
      </tr>`;
    });
    html += "</tbody></table>";
    wrapEl.innerHTML = html;
  } catch (e) {
    wrapEl.textContent = `Ошибка запроса: ${e}`;
  }
}

// === История ===

document.getElementById("history-refresh-btn").addEventListener("click", loadHistory);
document.getElementById("history-clear-btn").addEventListener("click", async () => {
  if (!confirm("Точно очистить всю историю?")) return;
  await fetch(`${API_BASE}/history`, { method: "DELETE" });
  loadHistory();
});

async function loadHistory() {
  const listEl = document.getElementById("history-list");
  listEl.textContent = "Загружаю...";
  try {
    const res = await fetch(`${API_BASE}/history`);
    const data = await res.json();
    if (!data.items.length) {
      listEl.innerHTML = '<p class="hint">История пуста</p>';
      return;
    }
    listEl.innerHTML = data.items
      .slice()
      .reverse()
      .map(
        (item) => `
        <div class="history-item" data-competitor="${escapeHtml(item.competitor_name || "")}">
          <span class="label">${escapeHtml(item.request_type)} · ${new Date(item.timestamp).toLocaleString("ru-RU")}</span>
          <strong>${escapeHtml(item.competitor_name || "—")}</strong>
          ${item.competitor_name ? removeBtn(item.competitor_name) : ""}
          <p>${escapeHtml(item.response_summary || "")}</p>
          <div class="flags">${flagsHtml(item.red_flags, item.green_flags)}</div>
        </div>`
      )
      .join("");
  } catch (e) {
    listEl.textContent = `Ошибка запроса: ${e}`;
  }
}

// начальная загрузка
async function refreshRates() {
  const btn = document.getElementById("summary-refresh-btn");
  btn.disabled = true;
  btn.textContent = "Тяну курсы с BestChange…";
  try {
    const res = await fetch(`${API_BASE}/summary/rates/refresh`, { method: "POST" });
    const data = await res.json();
    const missed = (data.not_found || []).length
      ? ` Не нашлись: ${data.not_found.join(", ")}.`
      : "";
    document.getElementById("summary-cbr-rate").textContent =
      `Обновлено курсов: ${data.updated} из ${data.checked}.${missed}`;
    await loadSummary();
  } catch (e) {
    document.getElementById("summary-table-wrap").textContent = `Ошибка запроса: ${e}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Обновить курсы";
  }
}

loadConfig();
renderQueue();
