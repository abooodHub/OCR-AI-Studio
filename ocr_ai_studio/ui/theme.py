APP_STYLE = r"""
* {
    font-family: "Segoe UI", "Noto Sans Arabic";
    font-size: 10pt;
}
QMainWindow, QDialog, QWidget#content {
    background: #090d15;
    color: #e9edf5;
}
QScrollArea { background: #090d15; border: none; }
QLabel { color: #dfe5ef; background: transparent; }
QLabel#appTitle {
    color: #ffffff;
    font-size: 22pt;
    font-weight: 800;
}
QLabel#sectionTitle {
    color: #ffffff;
    font-size: 13pt;
    font-weight: 750;
}
QLabel#muted {
    color: #8290a6;
    font-size: 9pt;
}
QFrame#card {
    background: #111722;
    border: 1px solid #252f40;
    border-radius: 14px;
}
QFrame#dropZone {
    background: #0c121d;
    border: 1px dashed #4a5972;
    border-radius: 12px;
}
QFrame#dropZone:hover {
    background: #101827;
    border-color: #7869ee;
}
QLabel#dropTitle {
    color: #f3f5fa;
    font-size: 11pt;
    font-weight: 700;
}
QLabel#connectionStatus, QLabel#jobStatus {
    background: #151d2a;
    color: #9ca8bb;
    border: 1px solid #303b4f;
    border-radius: 10px;
    padding: 9px 14px;
    font-weight: 700;
}
QLabel#connectionStatus[tone="success"], QLabel#jobStatus[tone="success"] {
    background: #10231e;
    color: #50d9a7;
    border-color: #245140;
}
QLabel#connectionStatus[tone="working"], QLabel#jobStatus[tone="working"] {
    background: #211e40;
    color: #b7afff;
    border-color: #4e468d;
}
QLabel#connectionStatus[tone="warning"], QLabel#jobStatus[tone="warning"] {
    background: #272012;
    color: #efbd68;
    border-color: #5d4928;
}
QLabel#connectionStatus[tone="error"], QLabel#jobStatus[tone="error"] {
    background: #2d1720;
    color: #ff8299;
    border-color: #603044;
}
QLabel#progressMeta {
    color: #a8b2c3;
    font-size: 9.5pt;
    font-weight: 650;
    padding: 2px;
}
QPushButton {
    background: #1b2433;
    color: #e0e5ee;
    border: 1px solid #344055;
    border-radius: 9px;
    padding: 9px 15px;
    min-height: 20px;
    font-weight: 650;
}
QPushButton:hover { background: #253147; border-color: #53617b; }
QPushButton:pressed { background: #151c29; }
QPushButton:disabled { background: #141a24; color: #566176; border-color: #252d3c; }
QPushButton#primary {
    background: #6e5dea;
    color: #ffffff;
    border-color: #8172f1;
    padding: 10px 24px;
    font-weight: 750;
}
QPushButton#primary:hover { background: #7d6df1; }
QPushButton#secondary { background: #192231; }
QPushButton#danger { background: #301822; color: #ff8299; border-color: #5a293d; }
QPushButton#linkButton {
    background: transparent;
    color: #9f98ff;
    border: none;
    padding: 7px 8px;
}
QPushButton#linkButton:hover { color: #cac6ff; background: #17162b; }
QLineEdit, QComboBox, QSpinBox {
    background: #0a1019;
    color: #e7ebf2;
    border: 1px solid #2d394e;
    border-radius: 9px;
    padding: 9px 11px;
    min-height: 20px;
    selection-background-color: #6e5dea;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover { border-color: #46546d; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #7b6bf0; }
QLineEdit:read-only { color: #aab4c4; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox QAbstractItemView {
    background: #151c28;
    color: #edf0f6;
    border: 1px solid #364258;
    selection-background-color: #29254f;
    padding: 5px;
}
QProgressBar {
    background: #080d14;
    color: #ffffff;
    border: 1px solid #273145;
    border-radius: 7px;
    min-height: 22px;
    max-height: 22px;
    text-align: center;
    font-weight: 700;
}
QProgressBar::chunk { background: #7463ec; border-radius: 6px; }
QTableWidget {
    background: #0a1019;
    color: #dce2ec;
    border: 1px solid #293448;
    border-radius: 10px;
    gridline-color: #20293a;
    selection-background-color: #29254f;
}
QTableWidget::item { padding: 8px; border-bottom: 1px solid #20293a; }
QHeaderView::section {
    background: #17202d;
    color: #a9b4c5;
    border: none;
    border-bottom: 1px solid #303b50;
    padding: 9px;
    font-weight: 700;
}
QPlainTextEdit {
    background: #070b11;
    color: #aeb9ca;
    border: 1px solid #252f41;
    border-radius: 10px;
    padding: 8px;
    font-family: "Cascadia Mono", "Consolas";
    font-size: 9pt;
}
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #354158; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #4a5873; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #1a2230; color: #ffffff; border: 1px solid #3c4860; padding: 6px; }
"""
