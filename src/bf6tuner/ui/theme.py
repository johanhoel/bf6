"""A dark stylesheet, kept in one place so the whole window reads as one thing."""

from __future__ import annotations

BG = "#111318"
BG_RAISED = "#181b22"
BG_SUNKEN = "#0c0e12"
BORDER = "#262b35"
TEXT = "#e6e9ef"
TEXT_DIM = "#9aa3b2"
ACCENT = "#4c8dff"
ACCENT_DIM = "#2d5cad"

OK = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"
INFO = "#58a6ff"

SEVERITY_COLOUR = {"high": BAD, "medium": WARN, "low": TEXT_DIM, "info": INFO}

MONO = "Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', monospace"

STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

QFrame#Card {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QFrame#HeaderBar {{ border: none; border-bottom: 1px solid {BORDER}; }}
QLabel#CardTitle {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.2px;
}}
QLabel#Hero {{ font-size: 34px; font-weight: 700; }}
QLabel#HeroUnit {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Mono {{ font-family: {MONO}; color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Title {{ font-size: 19px; font-weight: 700; letter-spacing: 0.2px; }}

QPushButton {{
    background-color: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 8px 15px;
    color: {TEXT};
}}
QPushButton:hover {{ border-color: {ACCENT_DIM}; }}
QPushButton:disabled {{ color: #5b6373; border-color: #1d222b; }}
QPushButton#Primary {{
    background-color: {ACCENT}; border-color: {ACCENT}; color: #06080c; font-weight: 600;
}}
QPushButton#Primary:hover {{ background-color: #5f9bff; }}
QPushButton#Primary:disabled {{ background-color: {ACCENT_DIM}; color: #9fb4d8; }}

QPushButton#Preset {{
    background-color: {BG_SUNKEN}; padding: 9px 6px; font-weight: 600; border-radius: 6px;
}}
QPushButton#Preset:checked {{
    background-color: {ACCENT}; border-color: {ACCENT}; color: #06080c;
}}

QComboBox, QSpinBox, QLineEdit {{
    background-color: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: {ACCENT_DIM};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_RAISED}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DIM};
}}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {BORDER}; background: {BG_SUNKEN};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 8px 16px; margin-right: 2px;
    border: 1px solid transparent; border-top-left-radius: 8px; border-top-right-radius: 8px;
}}
QTabBar::tab:selected {{ color: {TEXT}; background: {BG_RAISED}; border-color: {BORDER}; border-bottom-color: {BG_RAISED}; }}
QTabBar::tab:hover:!selected {{ color: {TEXT}; }}

QTableWidget {{
    background-color: {BG_RAISED}; border: none;
    gridline-color: {BORDER}; alternate-background-color: #14171d;
}}
QHeaderView::section {{
    background-color: {BG_SUNKEN}; color: {TEXT_DIM};
    border: none; border-bottom: 1px solid {BORDER};
    padding: 7px 8px; font-weight: 600;
}}
QTableWidget::item {{ padding: 7px 8px; }}
QTableWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}

QPlainTextEdit {{
    background-color: {BG_SUNKEN}; border: none;
    font-family: {MONO}; font-size: 12px;
    selection-background-color: {ACCENT_DIM};
}}
QTextEdit#SettingDetail {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 10px 14px;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
    selection-background-color: {ACCENT_DIM};
}}
QSplitter::handle:vertical {{
    height: 6px;
    background: {BORDER};
    margin: 2px 0;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2f3542; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3c4454; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2f3542; border-radius: 5px; min-width: 30px; }}

QProgressBar {{
    background-color: {BG_SUNKEN}; border: 1px solid {BORDER}; border-radius: 3px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 3px; }}

QStatusBar {{ background: {BG_SUNKEN}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QToolTip {{
    background-color: {BG_RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 6px; border-radius: 6px;
}}
"""
