"""
Окно PyQt6 — те же вкладки, что на сайте.
Сервер FastAPI поднимается сам в фоне. Если на порту уже наш актуальный API —
подключаемся; если занято чужим — берём соседний порт.
"""

from __future__ import annotations

import base64
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

HERE = Path(__file__).resolve().parent
ROOT = Path(getattr(sys, "_MEIPASS")) if getattr(sys, "frozen", False) else HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_client import api  # noqa: E402
from html_view import (  # noqa: E402
    analysis_html,
    card_identity,
    file_card_html,
    flags_html,
    reviews_html,
    same_competitor,
    short_when,
    site_card_html,
    wrap_document,
)
from styles import THEME  # noqa: E402
import server  # noqa: E402

ERROR_LOG = (
    Path(sys.executable).resolve().parent / "desktop-error.log"
    if getattr(sys, "frozen", False)
    else HERE / "desktop-error.log"
)


class Worker(QThread):
    """Фоновый поток: долгий запрос не должен морозить окно."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            self.done.emit(self.func(*self.args, **self.kwargs))
        except Exception as exc:
            self.failed.emit(str(exc))


class HtmlPane(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenExternalLinks(True)
        self.setMinimumHeight(120)

    def set_body(self, inner: str):
        self.setHtml(wrap_document(inner))
        self.document().adjustSize()
        height = int(self.document().size().height()) + 28
        self.setMinimumHeight(min(max(height, 80), 1200))
        self.setMaximumHeight(min(max(height, 80), 1200))


def hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def primary(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("primaryButton")
    return button


def secondary(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("secondaryButton")
    return button


def danger(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("dangerButton")
    return button


def link_btn(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName("linkButton")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class ResultCard(QWidget):
    removed = pyqtSignal(str)

    def __init__(self, html: str, screenshot_b64: str | None = None, competitor_name: str = ""):
        super().__init__()
        self.competitor_name = competitor_name
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)

        header = QHBoxLayout()
        if competitor_name:
            remove = link_btn("удалить")
            remove.clicked.connect(lambda: self.removed.emit(self.competitor_name))
            header.addStretch()
            header.addWidget(remove)
            layout.addLayout(header)

        self.pane = HtmlPane()
        self.pane.set_body(html)
        layout.addWidget(self.pane)

        self.shot = QLabel()
        self.shot.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.shot.setVisible(False)
        layout.addWidget(self.shot)
        self.set_screenshot(screenshot_b64)

    def update_content(self, html: str, screenshot_b64: str | None = None, competitor_name: str = ""):
        self.competitor_name = competitor_name or self.competitor_name
        self.pane.set_body(html)
        self.set_screenshot(screenshot_b64)

    def set_screenshot(self, screenshot_b64: str | None):
        if not screenshot_b64:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(base64.b64decode(screenshot_b64))
        if pixmap.isNull():
            return
        self.shot.setPixmap(
            pixmap.scaledToWidth(720, Qt.TransformationMode.SmoothTransformation)
        )
        self.shot.setVisible(True)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Мониторинг конкурентов")
        self.setMinimumSize(1100, 760)
        self.resize(1280, 880)
        self.setStyleSheet(THEME)

        self.max_urls = 10
        self.queue: list[dict] = []
        self.running_queue = False
        self.include_reviews_default = True
        self.analysis_store: dict[str, dict] = {}
        self.card_widgets: dict[str, ResultCard] = {}
        self.workers: list[Worker] = []
        self.selected_files: list[str] = []

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 12)

        title = QLabel("Мониторинг конкурентов")
        title.setObjectName("title")
        subtitle = QLabel("Крипто-обменники · анализ безопасности и маркетинга")
        subtitle.setObjectName("subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_analyze(), "Анализ")
        self.tabs.addTab(self._tab_batch(), "Папки data/")
        self.tabs.addTab(self._tab_reviews(), "Отзывы")
        self.tabs.addTab(self._tab_summary(), "Сравнение")
        self.tabs.addTab(self._tab_history(), "История")
        self.tabs.currentChanged.connect(self._on_tab)
        layout.addWidget(self.tabs)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = self.statusBar()
        self.status.showMessage("Запускаю сервер…")

    def _keep(self, worker: Worker) -> Worker:
        self.workers.append(worker)
        worker.finished.connect(lambda w=worker: self._drop_worker(w))
        return worker

    def _drop_worker(self, worker: Worker):
        if worker in self.workers:
            self.workers.remove(worker)

    def _busy(self, on: bool, message: str = ""):
        self.progress.setVisible(on)
        if message:
            self.status.showMessage(message)

    def _error(self, text: str):
        self._busy(False)
        self.running_queue = False
        self.run_files_btn.setEnabled(True)
        self.run_files_btn.setText("Проанализировать файлы")
        self.run_text_btn.setEnabled(True)
        self.batch_btn.setEnabled(True)
        self.reviews_btn.setEnabled(True)
        self.refresh_rates_btn.setEnabled(True)
        self.refresh_rates_btn.setText("Обновить курсы")
        self.render_queue()
        QMessageBox.warning(self, "Ошибка", text)

    def _confirm(self, text: str) -> bool:
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    # --- вкладка Анализ ---

    def _tab_analyze(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        col = QVBoxLayout(inner)

        sites_title = QLabel("Ссылки на сайты")
        sites_title.setObjectName("cardTitle")
        col.addWidget(sites_title)
        self.sites_limit_hint = hint(
            "До 10 ссылок: вставь списком или добавляй по одной. "
            "Название обменника определяется по домену автоматически."
        )
        col.addWidget(self.sites_limit_hint)

        row = QHBoxLayout()
        self.site_single = QLineEdit()
        self.site_single.setPlaceholderText("https://coindrop.trade")
        self.site_single.returnPressed.connect(self.add_single_url)
        add_one = secondary("Добавить")
        add_one.clicked.connect(self.add_single_url)
        row.addWidget(self.site_single)
        row.addWidget(add_one)
        col.addLayout(row)

        self.site_list = QPlainTextEdit()
        self.site_list.setPlaceholderText(
            "Или списком, по одной ссылке в строке:\ncoindrop.trade\nbuhtaobmena.me"
        )
        self.site_list.setFixedHeight(90)
        col.addWidget(self.site_list)

        list_row = QHBoxLayout()
        add_list = secondary("Добавить список")
        add_list.clicked.connect(self.add_list_urls)
        clear_q = secondary("Очистить очередь")
        clear_q.clicked.connect(self.clear_queue)
        list_row.addWidget(add_list)
        list_row.addWidget(clear_q)
        list_row.addStretch()
        col.addLayout(list_row)

        self.reviews_check = QCheckBox("Искать отзывы на BestChange и сторонних площадках")
        self.reviews_check.setChecked(True)
        col.addWidget(self.reviews_check)

        self.queue_table = QTableWidget(0, 5)
        self.queue_table.setHorizontalHeaderLabels(["#", "Конкурент", "Ссылка", "Статус", ""])
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.queue_table.setColumnWidth(0, 40)
        self.queue_table.setMaximumHeight(220)
        col.addWidget(self.queue_table)

        self.run_sites_btn = primary("Проанализировать")
        self.run_sites_btn.setEnabled(False)
        self.run_sites_btn.clicked.connect(self.run_queue)
        col.addWidget(self.run_sites_btn)
        col.addWidget(hint("Каждый сайт открывается в фоновом браузере — около минуты на ссылку."))

        col.addWidget(QLabel("Файлы"))
        col.addWidget(
            hint(
                "Один файл или пачка. Если про одного обменника пришли и скрин, и Word — "
                "данные сложатся в один профиль. Таблица со ссылками разбирается как список "
                "конкурентов: ячейки пишутся в профили, ссылки попадают в очередь выше."
            )
        )
        self.formats_hint = hint("Поддерживаются: PNG, JPG, WEBP, GIF · PDF · HTML, MHTML · TXT, MD, CSV, JSON")
        col.addWidget(self.formats_hint)

        file_row = QHBoxLayout()
        pick_files = secondary("Выбрать файлы")
        pick_files.clicked.connect(self.pick_files)
        self.files_label = hint("Файлы не выбраны")
        file_row.addWidget(pick_files)
        file_row.addWidget(self.files_label, 1)
        col.addLayout(file_row)

        self.file_name_preview = QLineEdit()
        self.file_name_preview.setPlaceholderText("Название определится автоматически")
        self.file_name_preview.setReadOnly(True)
        col.addWidget(self.file_name_preview)

        self.run_files_btn = primary("Проанализировать файлы")
        self.run_files_btn.clicked.connect(self.run_files)
        col.addWidget(self.run_files_btn)

        col.addWidget(QLabel("Текст"))
        col.addWidget(
            hint(
                "Единственное место, где название вводится руками. "
                "Если в тексте есть ссылка, а выплаты или курса нет — зайдём на сайт и дозаполним."
            )
        )
        self.text_name = QLineEdit()
        self.text_name.setPlaceholderText("Название конкурента (например, CoinDrop)")
        self.text_rate = QLineEdit()
        self.text_rate.setPlaceholderText("Курс ₽/USDT (необязательно)")
        col.addWidget(self.text_name)
        col.addWidget(self.text_rate)
        self.text_input = QPlainTextEdit()
        self.text_input.setPlaceholderText("Описание сервиса, условия обмена, отзывы клиентов...")
        self.text_input.setFixedHeight(120)
        col.addWidget(self.text_input)
        self.run_text_btn = primary("Проанализировать текст")
        self.run_text_btn.clicked.connect(self.run_text)
        col.addWidget(self.run_text_btn)

        col.addWidget(QLabel("Результаты"))
        self.results_host = QVBoxLayout()
        results_wrap = QWidget()
        results_wrap.setLayout(self.results_host)
        col.addWidget(results_wrap)
        self.results_host.addStretch()

        scroll.setWidget(inner)
        layout.addWidget(scroll)
        return page

    # --- остальные вкладки ---

    def _tab_batch(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Папки data/"))
        layout.addWidget(
            hint(
                "Проходит по подпапкам data/ и анализирует notes.md в каждой. "
                "Скупые заметки со ссылкой дозаполняются с сайта — это может занять минуту на папку."
            )
        )
        self.batch_btn = primary("Проанализировать всех")
        self.batch_btn.clicked.connect(self.run_batch)
        layout.addWidget(self.batch_btn)
        self.batch_pane = HtmlPane()
        self.batch_pane.set_body("<p class='hint'>Результат появится здесь</p>")
        layout.addWidget(self.batch_pane, 1)
        return page

    def _tab_reviews(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Отзывы о конкуренте"))
        layout.addWidget(
            hint(
                "По ссылке находим страницу обменника на BestChange (отзывы и финансовые претензии) "
                "и сторонние площадки, затем ищем в отзывах красные и зелёные маркеры. "
                "Сайты «помогу вернуть деньги» отбрасываются."
            )
        )
        self.reviews_url = QLineEdit()
        self.reviews_url.setPlaceholderText("https://grumbot.net")
        layout.addWidget(self.reviews_url)
        self.reviews_extra = QPlainTextEdit()
        self.reviews_extra.setPlaceholderText(
            "Необязательно: свои ссылки на страницы отзывов, по одной в строке"
        )
        self.reviews_extra.setFixedHeight(70)
        layout.addWidget(self.reviews_extra)
        self.reviews_btn = primary("Найти и разобрать отзывы")
        self.reviews_btn.clicked.connect(self.run_reviews)
        layout.addWidget(self.reviews_btn)
        self.reviews_pane = HtmlPane()
        layout.addWidget(self.reviews_pane, 1)
        return page

    def _tab_summary(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Сравнение конкурентов"))
        row = QHBoxLayout()
        self.refresh_rates_btn = secondary("Обновить курсы")
        self.refresh_rates_btn.clicked.connect(self.refresh_rates)
        export_btn = secondary("Скачать CSV")
        export_btn.clicked.connect(self.export_csv)
        row.addWidget(self.refresh_rates_btn)
        row.addWidget(export_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(
            hint(
                "Таблица собирается из всего, что уже накоплено по каждому обменнику. "
                "«Обновить курсы» тянет USDT/RUB с BestChange. CSV — чтобы открыть в Excel."
            )
        )
        self.cbr_label = hint("")
        layout.addWidget(self.cbr_label)

        legend = QGroupBox("Что значат колонки")
        legend.setCheckable(True)
        legend.setChecked(False)
        legend_layout = QVBoxLayout(legend)
        legend_layout.addWidget(
            hint(
                "Доверие — не рейтинг биржи. Сначала балл модели 0–10 по чеклисту безопасности. "
                "Тяжёлые красные флаги ставят потолок (блокировка карты — не выше 3). "
                "Дизайн — первый сигнал «похоже на контору», не про безопасность выплаты. "
                "Курс — BestChange на 1000 USDT (счёт / перевод / наличные), не с лендинга."
            )
        )
        layout.addWidget(legend)

        self.summary_table = QTableWidget(0, 9)
        self.summary_table.setHorizontalHeaderLabels([
            "Конкурент", "Материалы", "Доверие", "Дизайн", "Курс ₽/USDT",
            "К курсу ЦБ", "Способы выплаты", "Флаги", "",
        ])
        header = self.summary_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.summary_table, 1)
        return page

    def _tab_history(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("История анализов"))
        row = QHBoxLayout()
        refresh = secondary("Обновить")
        refresh.clicked.connect(self.load_history)
        clear = danger("Очистить историю")
        clear.clicked.connect(self.clear_history)
        row.addWidget(refresh)
        row.addWidget(clear)
        row.addStretch()
        layout.addLayout(row)
        self.history_pane = HtmlPane()
        layout.addWidget(self.history_pane, 1)
        return page

    def _on_tab(self, index: int):
        if index == 3:
            self.load_summary()
        if index == 4:
            self.load_history()

    # --- очередь URL ---

    def render_queue(self):
        self.queue_table.setRowCount(len(self.queue))
        pending = 0
        for index, item in enumerate(self.queue):
            if item["status"] == "ожидает":
                pending += 1
            values = [str(index + 1), item["name"], item["url"], item["status"]]
            if item.get("error"):
                values[3] = f"{item['status']}: {item['error']}"
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() ^ Qt.ItemFlag.ItemIsEditable)
                self.queue_table.setItem(index, col, cell)
            remove = link_btn("убрать")
            remove.setEnabled(not self.running_queue)
            remove.clicked.connect(lambda _, i=index: self.remove_queue_row(i))
            self.queue_table.setCellWidget(index, 4, remove)

        self.run_sites_btn.setEnabled((not self.running_queue) and pending > 0)
        if not self.queue:
            self.run_sites_btn.setText("Проанализировать")
        elif pending:
            self.run_sites_btn.setText(f"Проанализировать ({pending})")
        else:
            self.run_sites_btn.setText("Всё проанализировано")

    def remove_queue_row(self, index: int):
        if self.running_queue or index >= len(self.queue):
            return
        self.queue.pop(index)
        self.render_queue()

    def clear_queue(self):
        if self.running_queue:
            return
        self.queue = []
        self.render_queue()

    def add_single_url(self):
        raw = self.site_single.text()
        self._add_urls(raw)
        self.site_single.clear()

    def add_list_urls(self):
        raw = self.site_list.toPlainText()
        self._add_urls(raw)
        self.site_list.clear()

    def _add_urls(self, raw: str):
        if not raw.strip():
            return
        worker = self._keep(Worker(api.parse_urls, raw))
        worker.done.connect(self._on_urls_parsed)
        worker.failed.connect(self._error)
        worker.start()

    def _on_urls_parsed(self, data: dict):
        if data.get("success") is False and data.get("error"):
            self._error(data["error"])
            return
        items = data.get("items") or []
        if not items:
            self._error("Не нашёл ни одной ссылки в тексте")
            return
        added = 0
        overflow = 0
        for item in items:
            key = item["url"].rstrip("/").lower()
            if any(row["url"].rstrip("/").lower() == key for row in self.queue):
                continue
            if len(self.queue) >= self.max_urls:
                overflow += 1
                continue
            self.queue.append({
                "url": item["url"],
                "name": item.get("competitor_name") or item["url"],
                "status": "ожидает",
                "error": None,
            })
            added += 1
        self.render_queue()
        if overflow or data.get("skipped"):
            QMessageBox.information(
                self,
                "Очередь",
                f"Добавлено {added}. Лимит — {self.max_urls} ссылок, лишние не добавлены.",
            )

    def run_queue(self):
        if self.running_queue:
            return
        pending = [item for item in self.queue if item["status"] == "ожидает"]
        if not pending:
            return
        self.running_queue = True
        self.include_reviews_default = self.reviews_check.isChecked()
        self.render_queue()
        self._busy(True, "Разбираю сайты… каждый около минуты")
        self._run_next_queued()

    def _run_next_queued(self):
        nxt = next((item for item in self.queue if item["status"] == "ожидает"), None)
        if nxt is None:
            self.running_queue = False
            self._busy(False, "Очередь сайтов готова")
            self.render_queue()
            return
        nxt["status"] = "анализирую…"
        nxt["error"] = None
        self.render_queue()
        worker = self._keep(
            Worker(api.analyze_url, nxt["url"], self.include_reviews_default)
        )
        worker.done.connect(lambda data, item=nxt: self._on_url_done(item, data))
        worker.failed.connect(lambda err, item=nxt: self._on_url_fail(item, err))
        worker.start()

    def _on_url_done(self, item: dict, data: dict):
        if data.get("success"):
            item["status"] = "готово"
            payload = data.get("data") or data
            self.upsert_analysis(payload, item.get("url") or item.get("name"), "site", self.include_reviews_default)
        else:
            item["status"] = "ошибка"
            item["error"] = data.get("error") or "неизвестная ошибка"
        self.render_queue()
        self._run_next_queued()

    def _on_url_fail(self, item: dict, err: str):
        item["status"] = "ошибка"
        item["error"] = err
        self.render_queue()
        self._run_next_queued()

    # --- файлы и текст ---

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Файлы конкурентов",
            "",
            "Все поддерживаемые (*.png *.jpg *.jpeg *.webp *.gif *.heic *.heif "
            "*.pdf *.docx *.xlsx *.xlsm *.html *.htm *.mhtml *.mht *.txt *.md *.csv *.json);;"
            "Все файлы (*.*)",
        )
        if paths:
            self.selected_files = paths
            names = [Path(path).name for path in paths]
            shown = ", ".join(names[:4])
            extra = f" и ещё {len(names) - 4}" if len(names) > 4 else ""
            self.files_label.setText(f"{len(names)} шт.: {shown}{extra}")

    def run_files(self):
        if not self.selected_files:
            self._error("Выбери хотя бы один файл")
            return
        self.run_files_btn.setEnabled(False)
        self._busy(True, "Анализирую файлы…")
        self._file_queue = list(self.selected_files)
        self._file_total = len(self._file_queue)
        self._run_next_file()

    def _run_next_file(self):
        if not getattr(self, "_file_queue", None):
            self.run_files_btn.setEnabled(True)
            self.run_files_btn.setText("Проанализировать файлы")
            self.selected_files = []
            self.files_label.setText("Файлы не выбраны")
            self._busy(False, "Файлы разобраны")
            return
        path = self._file_queue.pop(0)
        done = self._file_total - len(self._file_queue)
        self.run_files_btn.setText(f"Анализирую {done} из {self._file_total}…")
        self.status.showMessage(f"Файл {Path(path).name} ({done}/{self._file_total})")
        worker = self._keep(Worker(api.analyze_file, path))
        worker.done.connect(lambda data, p=path: self._on_file_done(p, data))
        worker.failed.connect(lambda err, p=path: self._on_file_fail(p, err))
        worker.start()

    def _on_file_done(self, path: str, data: dict):
        name = Path(path).name
        if not data.get("success"):
            self._prepend_html(
                f"<h3>{name}</h3><p class='hint'>Ошибка: {data.get('error') or 'неизвестная'}</p>"
            )
        else:
            self.file_name_preview.setText(data.get("competitor_name") or "")
            queued = data.get("queued_urls") or []
            if data.get("kind") == "table" and queued:
                self._add_urls("\n".join(queued))
            self.upsert_analysis(data, name, "file")
        self._run_next_file()

    def _on_file_fail(self, path: str, err: str):
        self._prepend_html(f"<h3>{Path(path).name}</h3><p class='hint'>Ошибка запроса: {err}</p>")
        self._run_next_file()

    def run_text(self):
        text = self.text_input.toPlainText()
        if len(text.strip()) < 10:
            self._error("Текст слишком короткий")
            return
        name = self.text_name.text().strip() or None
        rate_raw = self.text_rate.text().strip().replace(",", ".")
        rate = None
        if rate_raw:
            try:
                rate = float(rate_raw)
            except ValueError:
                self._error("Курс должен быть числом, например 82.5")
                return
        self.run_text_btn.setEnabled(False)
        self._busy(True, "Анализирую текст… если не хватает данных — зайду на сайт")
        worker = self._keep(Worker(api.analyze_text, text, name, rate))
        worker.done.connect(lambda data, n=name: self._on_text_done(data, n))
        worker.failed.connect(self._error)
        worker.start()

    def _on_text_done(self, data: dict, name: str | None):
        self.run_text_btn.setEnabled(True)
        self._busy(False)
        if not data.get("success"):
            self._error(data.get("error") or "Ошибка анализа текста")
            return
        analysis = data.get("analysis") or {}
        title = name or analysis.get("detected_name") or "Текст"
        self.upsert_analysis(
            {"competitor_name": title, "kind": "text", "text_analysis": analysis},
            "вставленный текст",
            "file",
        )
        self.status.showMessage("Текст разобран")

    def run_batch(self):
        self.batch_btn.setEnabled(False)
        self.batch_pane.set_body(
            "<p>Анализирую папки data/. Скупые notes.md дозаполняются с сайта — "
            "это может занять несколько минут.</p>"
        )
        self._busy(True, "Папки data/…")
        worker = self._keep(Worker(api.analyze_batch))
        worker.done.connect(self._on_batch)
        worker.failed.connect(self._error)
        worker.start()

    def _on_batch(self, data: dict):
        self.batch_btn.setEnabled(True)
        self._busy(False)
        if data.get("success") is False and data.get("error") and not data.get("results"):
            self.batch_pane.set_body(f"<p>{data.get('error')}</p>")
            return
        results = data.get("results") or []
        errors = data.get("errors") or []
        parts = [f"<p>Обработано: {len(results)}, ошибок: {len(errors)}</p>"]
        for row in results:
            title = row.get("competitor_name") or row.get("url") or ""
            parts.append(f"<h3>{title}</h3>{analysis_html(row.get('text_analysis'))}")
        if errors:
            parts.append(f"<p class='hint'>{'; '.join(str(e) for e in errors)}</p>")
        self.batch_pane.set_body("".join(parts))
        self.status.showMessage("Папки data/ готовы")

    def run_reviews(self):
        url = self.reviews_url.text().strip()
        if not url:
            self.reviews_pane.set_body("<p>Вставь ссылку на обменник</p>")
            return
        extra = [
            line.strip()
            for line in self.reviews_extra.toPlainText().splitlines()
            if line.strip()
        ]
        self.reviews_btn.setEnabled(False)
        self.reviews_pane.set_body("<p>Ищу отзывы на площадках (до минуты)...</p>")
        self._busy(True, "Отзывы…")
        worker = self._keep(Worker(api.analyze_reviews, url, extra))
        worker.done.connect(self._on_reviews)
        worker.failed.connect(self._error)
        worker.start()

    def _on_reviews(self, data: dict):
        self.reviews_btn.setEnabled(True)
        self._busy(False)
        if not data.get("success"):
            self.reviews_pane.set_body(f"<p>Ошибка: {data.get('error')}</p>")
            return
        name = data.get("competitor_name") or ""
        self.reviews_pane.set_body(f"<h3>{name}</h3>{reviews_html(data.get('reviews'))}")
        self.status.showMessage("Отзывы готовы")

    # --- сравнение и история ---

    def load_summary(self):
        self.cbr_label.setText("Загружаю таблицу…")
        worker = self._keep(Worker(api.summary))
        worker.done.connect(self._fill_summary)
        worker.failed.connect(self._error)
        worker.start()

    def _fill_summary(self, data: dict):
        if data.get("error") and not data.get("rows"):
            self.cbr_label.setText(str(data["error"]))
            return
        cbr = data.get("cbr_rate")
        if cbr:
            self.cbr_label.setText(
                f"Курс ЦБ РФ на {cbr.get('date')}: {cbr.get('usd_rub')} ₽/USD"
            )
        else:
            self.cbr_label.setText("Курс ЦБ недоступен")

        rows = data.get("rows") or []
        self.summary_table.setRowCount(len(rows))
        if not rows:
            return
        for index, row in enumerate(rows):
            aliases = ", ".join(row.get("aliases") or [])
            name = row.get("competitor_name") or ""
            name_text = name + (f" ({aliases})" if aliases else "")
            materials = row.get("materials") or []
            materials_text = (
                f"{row.get('sources_count') or len(materials)}\n" + " · ".join(materials)
                if materials
                else str(row.get("sources_count") or "—")
            )
            trust = "—" if row.get("trust_score") is None else str(row.get("trust_score"))
            design = "—" if row.get("design_score") is None else str(row.get("design_score"))
            rate = "—" if row.get("exchanger_rate") is None else str(row.get("exchanger_rate"))
            spread = row.get("rate_spread")
            if spread is None:
                spread_text = "—"
            else:
                spread_text = f"+{spread}" if spread > 0 else str(spread)
            payouts = ", ".join(row.get("payout_methods") or [])
            values = [
                name_text,
                materials_text,
                trust,
                design,
                rate,
                spread_text,
                payouts,
            ]
            tooltips = [
                row.get("source_url") or "",
                " · ".join(materials),
                row.get("trust_note") or "",
                row.get("design_note") or "",
                " · ".join(
                    part for part in [row.get("rate_source"), short_when(row.get("rate_fetched_at"))] if part
                ),
                "",
                payouts,
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setFlags(cell.flags() ^ Qt.ItemFlag.ItemIsEditable)
                if col < len(tooltips) and tooltips[col]:
                    cell.setToolTip(tooltips[col])
                self.summary_table.setItem(index, col, cell)
            flags = QLabel(flags_html(row.get("red_flags"), row.get("green_flags")))
            flags.setTextFormat(Qt.TextFormat.RichText)
            flags.setWordWrap(True)
            self.summary_table.setCellWidget(index, 7, flags)
            remove = link_btn("удалить")
            remove.clicked.connect(lambda _, n=name: self.remove_competitor(n))
            self.summary_table.setCellWidget(index, 8, remove)
            self.summary_table.setRowHeight(index, 64)

    def refresh_rates(self):
        self.refresh_rates_btn.setEnabled(False)
        self.refresh_rates_btn.setText("Тяну курсы с BestChange…")
        self._busy(True, "Курсы BestChange…")
        worker = self._keep(Worker(api.refresh_rates))
        worker.done.connect(self._on_rates)
        worker.failed.connect(self._error)
        worker.start()

    def _on_rates(self, data: dict):
        self.refresh_rates_btn.setEnabled(True)
        self.refresh_rates_btn.setText("Обновить курсы")
        self._busy(False)
        if data.get("error") and data.get("updated") is None:
            self._error(data["error"])
            return
        missed = data.get("not_found") or []
        extra = f" Не нашлись: {', '.join(missed)}." if missed else ""
        self.cbr_label.setText(
            f"Обновлено курсов: {data.get('updated')} из {data.get('checked')}.{extra}"
        )
        self.load_summary()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить CSV", "sravnenie-konkurentov.csv", "CSV (*.csv)"
        )
        if not path:
            return
        worker = self._keep(Worker(api.export_csv))
        worker.done.connect(lambda pair, p=path: self._save_csv(p, pair))
        worker.failed.connect(self._error)
        worker.start()

    def _save_csv(self, path: str, pair):
        content, err = pair
        if err:
            self._error(err)
            return
        Path(path).write_bytes(content)
        self.status.showMessage(f"CSV сохранён: {path}")

    def load_history(self):
        self.history_pane.set_body("<p>Загружаю...</p>")
        worker = self._keep(Worker(api.history))
        worker.done.connect(self._fill_history)
        worker.failed.connect(self._error)
        worker.start()

    def _fill_history(self, data: dict):
        items = data.get("items") or []
        if not items:
            self.history_pane.set_body("<p class='hint'>История пуста</p>")
            return
        blocks = []
        for item in reversed(items):
            when = short_when(item.get("timestamp"))
            name = item.get("competitor_name") or "—"
            blocks.append(
                f"<p><span class='hint'>{item.get('request_type')} · {when}</span><br>"
                f"<b>{name}</b><br>{item.get('response_summary') or ''}</p>"
                f"{flags_html(item.get('red_flags'), item.get('green_flags'))}<hr>"
            )
        self.history_pane.set_body("".join(blocks))

    def clear_history(self):
        if not self._confirm("Точно очистить всю историю?"):
            return
        worker = self._keep(Worker(api.clear_history))
        worker.done.connect(lambda _: self.load_history())
        worker.failed.connect(self._error)
        worker.start()

    # --- карточки анализа ---

    def _prepend_html(self, inner: str, key: str | None = None, screenshot=None, name: str = ""):
        card = ResultCard(inner, screenshot, name)
        card.removed.connect(self.remove_competitor)
        if key:
            old = self.card_widgets.get(key)
            if old is not None:
                self.results_host.removeWidget(old)
                old.deleteLater()
            self.card_widgets[key] = card
        self.results_host.insertWidget(0, card)

    def upsert_analysis(self, data: dict, filename: str, kind: str, include_reviews: bool | None = None):
        if data.get("kind") == "table":
            self._prepend_html(file_card_html(data, [filename]), name="")
            return
        key = card_identity(data, filename)
        prev = self.analysis_store.get(key) or {"files": [], "data": {}, "includeReviews": False}
        if filename and filename not in prev["files"]:
            prev["files"].append(filename)
        prev["data"] = {**prev["data"], **data}
        if include_reviews is not None:
            prev["includeReviews"] = include_reviews
        if data.get("profile"):
            prev["data"]["profile"] = data["profile"]
        self.analysis_store[key] = prev

        html = (
            site_card_html(prev["data"], prev["includeReviews"], prev["files"])
            if kind == "site"
            else file_card_html(prev["data"], prev["files"])
        )
        name = prev["data"].get("competitor_name") or ""
        shot = prev["data"].get("screenshot_base64")
        self._prepend_html(html, key=key, screenshot=shot, name=name)

    def remove_competitor(self, name: str):
        if not name:
            return
        if not self._confirm(f"Удалить «{name}» из сравнения, истории и анализа?"):
            return
        worker = self._keep(Worker(api.remove_competitor, name))
        worker.done.connect(lambda data, n=name: self._after_remove(n, data))
        worker.failed.connect(self._error)
        worker.start()

    def _after_remove(self, name: str, data: dict):
        if data.get("success") is False and data.get("error"):
            self._error(data.get("error") or "Не нашёл этого конкурента")
            return
        self.queue = [item for item in self.queue if not same_competitor(item.get("name"), name)]
        self.render_queue()
        drop_keys = [
            key for key, stored in self.analysis_store.items()
            if same_competitor(key, name)
            or same_competitor((stored.get("data") or {}).get("competitor_name"), name)
        ]
        for key in drop_keys:
            self.analysis_store.pop(key, None)
            widget = self.card_widgets.pop(key, None)
            if widget is not None:
                self.results_host.removeWidget(widget)
                widget.deleteLater()
        leftover = [
            widget for widget in self.card_widgets.values()
            if same_competitor(widget.competitor_name, name)
        ]
        for widget in leftover:
            self.results_host.removeWidget(widget)
            widget.deleteLater()
        if self.tabs.currentIndex() == 3:
            self.load_summary()
        if self.tabs.currentIndex() == 4:
            self.load_history()
        self.status.showMessage(f"Удален: {name}")

    def bootstrap(self):
        try:
            server.start()
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Сервер",
                f"Не удалось запустить API на порту {server.PORT}:\n{exc}",
            )
            self.status.showMessage("Сервер недоступен")
            return
        if api.health():
            self.status.showMessage(f"Сервер на http://{server.HOST}:{server.PORT}")
        else:
            self.status.showMessage("Сервер не ответил на /health")
        worker = self._keep(Worker(api.config))
        worker.done.connect(self._apply_config)
        worker.start()
        self.render_queue()

    def _apply_config(self, data: dict):
        if data.get("max_urls_per_batch"):
            self.max_urls = int(data["max_urls_per_batch"])
            self.sites_limit_hint.setText(
                f"До {self.max_urls} ссылок: вставь списком или добавляй по одной. "
                "Название обменника определяется по домену автоматически."
            )
        if data.get("supported_formats"):
            self.formats_hint.setText(f"Поддерживаются: {data['supported_formats']}")


def _write_crash(text: str):
    try:
        ERROR_LOG.write_text(text, encoding="utf-8")
    except Exception:
        pass


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    window.bootstrap()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _write_crash(traceback.format_exc())
        raise
