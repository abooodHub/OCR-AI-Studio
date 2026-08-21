# ruff: noqa: E501 - QSS declarations are intentionally kept readable.

APP_STYLE = """
QWidget {
    background: #090c13;
    color: #e8ecf5;
    font-family: "Segoe UI", "Tahoma";
    font-size: 10.5pt;
}

QMainWindow, QStackedWidget { background: #090c13; }

QFrame#sidebar {
    background: #101521;
    border-left: 1px solid #252c3b;
}
QLabel#brand {
    color: #ffffff;
    font-size: 19pt;
    font-weight: 800;
    background: transparent;
}
QLabel#brandSubtitle, QLabel#version {
    color: #747f95;
    font-size: 8.5pt;
    background: transparent;
}
QLabel#brandIcon {
    min-width: 48px;
    max-width: 48px;
    min-height: 48px;
    max-height: 48px;
    border-radius: 14px;
    background: #6c5ce7;
    color: #ffffff;
    font-size: 13pt;
    font-weight: 800;
}
QLabel#navCaption {
    color: #69758b;
    background: transparent;
    font-size: 8.5pt;
    font-weight: 700;
    padding: 0 9px 5px 0;
}
QFrame#localCard {
    background: #121c24;
    border: 1px solid #1e3a3b;
    border-radius: 12px;
}
QLabel#localTitle {
    color: #56d7af;
    background: transparent;
    font-weight: 700;
}

QLabel#pageTitle {
    color: #ffffff;
    font-size: 23pt;
    font-weight: 800;
    background: transparent;
}
QLabel#pageDescription {
    color: #8d97aa;
    font-size: 10pt;
    background: transparent;
}
QLabel#privacyBadge {
    color: #75e0bd;
    background: #10251f;
    border: 1px solid #1f4c40;
    border-radius: 11px;
    padding: 7px 13px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#muted, QLabel#sectionDescription {
    color: #8590a5;
    background: transparent;
    font-size: 9pt;
}
QLabel#sectionTitle {
    color: #f5f7fb;
    background: transparent;
    font-size: 12.5pt;
    font-weight: 750;
}

QFrame#card {
    background: #121722;
    border: 1px solid #252d3d;
    border-radius: 15px;
}
QFrame#providerCard {
    background: #121824;
    border: 1px solid #283247;
    border-radius: 13px;
}
QFrame#providerCard:hover { border-color: #4c4a80; background: #151b29; }
QLabel#providerName, QLabel#diagnosticName {
    color: #f3f5fa;
    background: transparent;
    font-size: 11.5pt;
    font-weight: 700;
}
QLabel#portBadge {
    color: #aaa4ff;
    background: #211f43;
    border: 1px solid #39356b;
    border-radius: 8px;
    padding: 4px 9px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 8pt;
    font-weight: 700;
}
QFrame#modelStatusCard {
    background: #0d121c;
    border: 1px solid #252e40;
    border-radius: 10px;
}
QLabel#modelStatus { background: transparent; color: #9aa5b9; font-weight: 600; }
QLabel#modelStatus[tone="working"] { color: #9ea8ff; }
QLabel#modelStatus[tone="success"] { color: #55d9a9; }
QLabel#modelStatus[tone="error"] { color: #ff7d96; }

QLabel#step, QLabel#stepActive {
    border-radius: 10px;
    padding: 8px 13px;
    font-size: 9pt;
    font-weight: 650;
}
QLabel#step { background: #10151f; border: 1px solid #222a39; color: #747f92; }
QLabel#stepActive { background: #211e49; border: 1px solid #403a86; color: #c1bcff; }

QPushButton {
    background: #20283a;
    color: #dce2ee;
    border: 1px solid #303a50;
    border-radius: 9px;
    padding: 9px 15px;
    min-height: 20px;
    font-weight: 650;
}
QPushButton:hover { background: #29334a; border-color: #4b5874; color: #ffffff; }
QPushButton:pressed { background: #181e2c; }
QPushButton:disabled { background: #151a25; color: #4f596c; border-color: #222938; }
QPushButton#primary {
    background: #6c5ce7;
    color: #ffffff;
    border: 1px solid #7869ee;
    padding: 10px 18px;
    font-weight: 750;
}
QPushButton#primary:hover { background: #7a6bef; border-color: #9185f4; }
QPushButton#secondary { background: #1b2231; color: #c9d0de; }
QPushButton#danger { background: #301923; color: #ff829a; border-color: #57283a; }
QPushButton#danger:hover { background: #43202e; border-color: #77364c; }
QPushButton#nav {
    background: transparent;
    border: none;
    text-align: right;
    color: #9aa5b9;
    padding: 12px 14px;
    border-radius: 10px;
    font-weight: 650;
}
QPushButton#nav:hover { background: #171e2b; color: #ffffff; }
QPushButton#nav:checked {
    background: #211e49;
    color: #c5c0ff;
    border: 1px solid #37316f;
}

QLineEdit, QComboBox, QSpinBox {
    background: #0b1019;
    color: #e3e8f1;
    border: 1px solid #2b3549;
    border-radius: 9px;
    padding: 9px 11px;
    min-height: 20px;
    selection-background-color: #6c5ce7;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #3d4962; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 1px solid #786cf0; background: #0d121d; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView {
    background: #151b27;
    color: #e5e9f1;
    border: 1px solid #333d53;
    selection-background-color: #292450;
    padding: 5px;
}

QTableWidget {
    background: #0b1018;
    alternate-background-color: #0e141e;
    color: #dce1eb;
    border: 1px solid #283247;
    border-radius: 11px;
    gridline-color: #202839;
    selection-background-color: #292450;
    selection-color: #ffffff;
    outline: none;
}
QTableWidget::item { padding: 9px; border-bottom: 1px solid #1c2433; }
QTableWidget::item:selected { border-left: 3px solid #7b6cef; }
QHeaderView::section {
    background: #171e2b;
    color: #9da8bb;
    border: none;
    border-bottom: 1px solid #303a4f;
    padding: 10px;
    font-size: 9pt;
    font-weight: 700;
}

QLabel#statusReady {
    background: transparent;
    color: #8d98aa;
    font-size: 9.5pt;
    font-weight: 650;
}
QLabel#statusReady[tone="working"] { color: #a7a1ff; }
QLabel#statusReady[tone="success"] { color: #55d9a9; }
QLabel#statusReady[tone="warning"] { color: #f2bd68; }
QLabel#statusReady[tone="error"] { color: #ff7e96; }
QProgressBar {
    background: #090e16;
    border: 1px solid #20293a;
    border-radius: 5px;
    min-height: 9px;
    max-height: 9px;
}
QProgressBar::chunk { background: #7465ee; border-radius: 4px; }

QPlainTextEdit {
    background: #080c12;
    border: 1px solid #222b3c;
    border-radius: 9px;
    color: #aab5c7;
    padding: 7px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 9pt;
    selection-background-color: #3d357c;
}

QFrame#diagnosticRow {
    background: #0d121c;
    border: 1px solid #242d3e;
    border-radius: 10px;
}
QLabel#technicalText {
    background: transparent;
    color: #758197;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 8.5pt;
}
QLabel#readyBadge, QLabel#errorBadge {
    border-radius: 9px;
    padding: 6px 12px;
    font-size: 9pt;
    font-weight: 700;
}
QLabel#readyBadge { background: #102820; color: #57dcae; border: 1px solid #21503f; }
QLabel#errorBadge { background: #321923; color: #ff8299; border: 1px solid #5b293b; }

QStatusBar {
    background: #0d111a;
    color: #7f8a9d;
    border-top: 1px solid #1d2432;
    padding: 4px 12px;
}
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #303a4e; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #43506a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #1a2230; color: #ffffff; border: 1px solid #3b465d; padding: 7px; }
"""
