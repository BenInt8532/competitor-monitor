"""HTML карточек — те же блоки, что на сайте (флаги, резюме, отзывы)."""

from html import escape as escape_html


DOC_CSS = """
body { color: #f3f4ef; background: #0c0c0b; font-family: 'Segoe UI', sans-serif;
       font-size: 14px; line-height: 1.55; margin: 0; }
h3 { font-size: 18px; font-weight: 500; margin: 0 0 8px; }
a { color: #f3f4ef; }
.hint { color: #a6aaa4; font-size: 12px; }
.summary { margin: 8px 0; }
.meta { margin: 6px 0 10px; }
.chip { display: inline-block; background: #141413;
        border: 1px solid rgba(243,244,239,0.18); padding: 2px 8px; margin: 2px 4px 2px 0; }
.chip-good { color: #a9d89f; }
.chip-mid { color: #f3f4ef; }
.chip-bad { color: #cf9b8c; }
.badge { display: inline-block; padding: 2px 8px; margin: 2px 4px 2px 0; color: #000; }
.badge-green { background: #a9d89f; }
.badge-red { background: #cf9b8c; }
.label { color: #a6aaa4; font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; }
ul { margin: 4px 0 10px 18px; padding: 0; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid rgba(243,244,239,0.18); padding: 6px 8px; text-align: left; }
"""


def wrap_document(inner: str) -> str:
    return f"<html><head><meta charset='utf-8'><style>{DOC_CSS}</style></head><body>{inner}</body></html>"


def short_when(iso) -> str:
    if not iso:
        return ""
    return str(iso).replace("T", " ")[:16]


def flags_html(red_flags=None, green_flags=None) -> str:
    parts = []
    for flag in green_flags or []:
        parts.append(f'<span class="badge badge-green">{escape_html(str(flag))}</span>')
    for flag in red_flags or []:
        parts.append(f'<span class="badge badge-red">{escape_html(str(flag))}</span>')
    return "".join(parts) or '<span class="hint">Флагов не найдено</span>'


def list_html(items=None) -> str:
    if not items:
        return '<span class="hint">—</span>'
    rows = "".join(f"<li>{escape_html(str(item))}</li>" for item in items)
    return f"<ul>{rows}</ul>"


def score_chip(label: str, value) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    tone = "chip-good" if number >= 7 else "chip-mid" if number >= 4 else "chip-bad"
    shown = int(number) if number == int(number) else number
    return f'<span class="chip {tone}">{escape_html(label)} {shown}/10</span>'


def analysis_html(analysis: dict | None) -> str:
    if not analysis:
        return '<p class="hint">Анализ не получен</p>'

    payouts = ", ".join(analysis.get("payout_methods") or [])
    rate = analysis.get("exchanger_rate")
    rate_source = analysis.get("rate_source") or ""
    rate_chip = ""
    if rate is not None:
        extra = f" · {escape_html(str(rate_source))}" if rate_source else ""
        rate_chip = f'<span class="chip">{escape_html(str(rate))} ₽/USDT{extra}</span>'

    meta = "".join([
        score_chip("Доверие", analysis.get("trust_score")),
        score_chip("Дизайн", analysis.get("design_score")),
        rate_chip,
        f'<span class="chip">{escape_html(payouts)}</span>' if payouts else "",
    ])
    note = analysis.get("enrichment_note") or ""
    note_html = f'<p class="hint">{escape_html(str(note))}</p>' if note else ""

    return f"""
    <div class="meta">{meta}</div>
    <p class="summary">{escape_html(str(analysis.get("summary") or ""))}</p>
    {note_html}
    <div class="flags">{flags_html(analysis.get("red_flags"), analysis.get("green_flags"))}</div>
    <p class="label">Плюсы</p>{list_html(analysis.get("strengths"))}
    <p class="label">Минусы</p>{list_html(analysis.get("weaknesses"))}
    <p class="label">Уникальное</p>{list_html(analysis.get("unique_offers"))}
    <p class="label">Рекомендации</p>{list_html(analysis.get("recommendations"))}
    """


def reviews_html(reviews: dict | None) -> str:
    if not reviews:
        return ""

    sources = []
    for source in reviews.get("sources") or []:
        mark = "✓" if source.get("status") == "ok" else "–" if source.get("status") == "пусто" else "!"
        note = f" — {escape_html(str(source.get('note')))}" if source.get("note") else ""
        url = escape_html(str(source.get("url") or ""))
        name = escape_html(str(source.get("name") or url))
        sources.append(f'<li>{mark} <a href="{url}">{name}</a>{note}</li>')
    sources_block = f"<ul>{''.join(sources) or '<li>—</li>'}</ul>"

    if not reviews.get("reviews_found"):
        return (
            f'<p class="hint">{escape_html(str(reviews.get("summary") or "Отзывы не найдены"))}</p>'
            f'<p class="label">Какие площадки проверены</p>{sources_block}'
        )

    return f"""
    <div class="meta">
      <span class="chip">Отзывы</span>
      {score_chip("Доверие", reviews.get("reviews_trust_score"))}
    </div>
    <p class="summary">{escape_html(str(reviews.get("summary") or ""))}</p>
    <div class="flags">{flags_html(reviews.get("red_flags"), reviews.get("green_flags"))}</div>
    <p class="label">Жалобы</p>{list_html(reviews.get("complaint_quotes"))}
    <p class="label">Позитив</p>{list_html(reviews.get("positive_quotes"))}
    <p class="label">Отброшено как голословное</p>{list_html(reviews.get("ignored_claims"))}
    <p class="label">Какие площадки проверены</p>{sources_block}
    """


def files_caption(files, fallback="") -> str:
    files = [name for name in (files or []) if name]
    if not files:
        return fallback or ""
    if len(files) == 1:
        return fallback or files[0]
    return f"из {len(files)} файлов: {', '.join(files)}"


def site_card_html(data: dict, include_reviews: bool, files=None) -> str:
    name = data.get("competitor_name") or data.get("url") or ""
    url = data.get("url") or ""
    extra = ""
    if files and len(files) > 1:
        extra = f'<p class="hint">{escape_html(files_caption(files))}</p>'
    reviews_block = ""
    if include_reviews and data.get("reviews"):
        reviews_block = "<hr>" + reviews_html(data.get("reviews"))
    return f"""
    <h3>{escape_html(str(name))}
      <a href="{escape_html(str(url))}">{escape_html(str(url))}</a>
    </h3>
    {extra}
    {analysis_html(data.get("text_analysis"))}
    {reviews_block}
    """


KIND_LABELS = {
    "image": "скриншот",
    "pdf": "PDF",
    "html": "страница сайта",
    "text": "заметка",
    "docx": "Word",
    "xlsx": "таблица",
    "csv": "таблица",
    "table": "таблица конкурентов",
}


def file_card_html(data: dict, files=None) -> str:
    file_list = [name for name in (files or []) if name] or [""]
    filename = file_list[-1] or ""
    kind = data.get("kind") or ""
    kind_label = KIND_LABELS.get(kind, kind)
    card_name = data.get("competitor_name") or filename
    source_line = files_caption(file_list, f"{kind_label} · {filename}")
    head = f"<h3>{escape_html(str(card_name))}<br><span class='hint'>{escape_html(source_line)}</span></h3>"

    if kind == "table":
        rows = []
        for row in data.get("table_rows") or []:
            link = row.get("url") or "—"
            rate = row.get("exchanger_rate")
            rate_text = "—" if rate is None else str(rate)
            rows.append(
                "<tr>"
                f"<td>{escape_html(str(row.get('competitor_name') or ''))}</td>"
                f"<td>{escape_html(str(link))}</td>"
                f"<td>{escape_html(rate_text)}</td>"
                f"<td>{escape_html(str(row.get('notes') or '—'))}</td>"
                "</tr>"
            )
        queued = len(data.get("queued_urls") or [])
        queued_note = (
            f"Ссылки ({queued}) добавлены в очередь «Анализ» — нажми «Проанализировать»."
            if queued
            else "Ссылок в таблице не было, остались только данные из ячеек."
        )
        return f"""
        {head}
        <p class="summary">В файле список конкурентов. Данные из ячеек уже записаны в профили. {queued_note}</p>
        <table><thead><tr><th>Конкурент</th><th>Сайт</th><th>Курс</th><th>Из таблицы</th></tr></thead>
        <tbody>{''.join(rows)}</tbody></table>
        """

    source_names = ", ".join(file_list) or ", ".join(
        (item.get("label") or "") for item in ((data.get("profile") or {}).get("sources") or [])
    )
    merged = ""
    profile = data.get("profile") or {}
    if len(file_list) > 1 or (profile.get("sources_count") or 0) > 1:
        merged = f'<p class="hint">Один обменник, материалы сложились. Источники: {escape_html(source_names)}</p>'

    image = data.get("image_analysis")
    if image:
        return f"""
        {head}{merged}
        <div class="meta">{score_chip("Дизайн", image.get("design_score"))}</div>
        <p class="summary">{escape_html(str(image.get("description") or ""))}</p>
        <div class="flags">{flags_html([], image.get("safety_claims"))}</div>
        <p class="label">Маркетинг</p>{list_html(image.get("marketing_insights"))}
        <p class="label">Рекомендации</p>{list_html(image.get("recommendations"))}
        """

    return f"{head}{merged}{analysis_html(data.get('text_analysis'))}"


def card_identity(data: dict | None, filename: str = "") -> str:
    data = data or {}
    profile = data.get("profile") or {}
    if profile.get("key"):
        return str(profile["key"])
    url = data.get("url") or ""
    if url.startswith("http"):
        host = url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
        return host.split(".")[0].lower()
    raw = (data.get("competitor_name") or filename or "").lower()
    cleaned = []
    for char in raw.replace("http://", "").replace("https://", ""):
        if char.isalnum() or char in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя":
            cleaned.append(char)
    return "".join(cleaned)


def same_competitor(left: str, right: str) -> bool:
    def norm(value: str) -> str:
        return "".join(ch for ch in (value or "").lower() if ch.isalnum() or ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя")

    a, b = norm(left), norm(right)
    return bool(a and b and a == b)
