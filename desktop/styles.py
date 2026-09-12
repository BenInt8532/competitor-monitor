"""Тёмная тема окна: как у сайта — чёрный фон, тёплый текст, цвет только у флагов."""

THEME = """
QMainWindow, QWidget {
    background-color: #000000;
    color: #f3f4ef;
    font-family: "Segoe UI", Montserrat, sans-serif;
    font-size: 14px;
}
QLabel#title {
    font-size: 26px;
    font-weight: 500;
    color: #f3f4ef;
}
QLabel#subtitle {
    color: #a6aaa4;
    font-size: 12px;
    letter-spacing: 2px;
    text-transform: uppercase;
}
QLabel#hint {
    color: #a6aaa4;
    font-size: 13px;
}
QLabel#cardTitle {
    font-size: 18px;
    font-weight: 500;
    color: #f3f4ef;
}
QTabWidget::pane {
    border: none;
    background: #000000;
    top: -1px;
}
QTabBar::tab {
    background: #0c0c0b;
    color: #a6aaa4;
    padding: 10px 18px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected {
    color: #f3f4ef;
    border-bottom: 2px solid #f3f4ef;
}
QTabBar::tab:hover:!selected { color: #f3f4ef; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {
    background: #141413;
    color: #f3f4ef;
    border: 1px solid rgba(243, 244, 239, 0.18);
    padding: 8px;
    selection-background-color: #3a3a38;
}
QPushButton#primaryButton {
    background: #f3f4ef;
    color: #000000;
    border: none;
    padding: 10px 16px;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: #dfe0db; }
QPushButton#primaryButton:disabled { background: #3a3a38; color: #6e716c; }
QPushButton#secondaryButton {
    background: transparent;
    color: #f3f4ef;
    border: 1px solid rgba(243, 244, 239, 0.18);
    padding: 10px 16px;
}
QPushButton#secondaryButton:hover { background: #141413; }
QPushButton#dangerButton {
    background: transparent;
    color: #cf9b8c;
    border: 1px solid #cf9b8c;
    padding: 10px 16px;
}
QPushButton#linkButton {
    background: transparent;
    color: #a6aaa4;
    border: none;
    text-decoration: underline;
    padding: 4px 8px;
}
QCheckBox { color: #f3f4ef; spacing: 8px; }
QTableWidget {
    background: #0c0c0b;
    color: #f3f4ef;
    gridline-color: rgba(243, 244, 239, 0.12);
    border: 1px solid rgba(243, 244, 239, 0.18);
    alternate-background-color: #141413;
}
QHeaderView::section {
    background: #141413;
    color: #a6aaa4;
    padding: 6px;
    border: none;
    border-right: 1px solid rgba(243, 244, 239, 0.09);
}
QTextBrowser {
    background: #0c0c0b;
    color: #f3f4ef;
    border: 1px solid rgba(243, 244, 239, 0.18);
    padding: 12px;
}
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    background: #0c0c0b;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: #3a3a38;
    min-height: 24px;
}
QStatusBar {
    background: #0c0c0b;
    color: #a6aaa4;
}
QProgressBar {
    border: 1px solid rgba(243, 244, 239, 0.18);
    background: #0c0c0b;
    text-align: center;
    color: #f3f4ef;
    min-width: 180px;
}
QProgressBar::chunk { background: #a9d89f; }
QGroupBox {
    border: 1px solid rgba(243, 244, 239, 0.18);
    margin-top: 12px;
    padding: 12px 8px 8px;
    color: #a6aaa4;
}
QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
QToolTip {
    background: #141413;
    color: #f3f4ef;
    border: 1px solid rgba(243, 244, 239, 0.18);
    padding: 6px;
}
"""
