"""A dark stylesheet, kept in one place so the whole window reads as one thing."""

from __future__ import annotations

BG = "#111318"
BG_RAISED = "#181b22"
BG_RAISED_TOP = "#1b1f27"  # gradient top-stop for Card - a hair lighter, for a subtle raised feel
BG_SUNKEN = "#0c0e12"
BG_HOVER = "#1e222b"  # button hover fill - a visible lift, not just a border-color swap
BG_PRESSED = "#0a0c10"
BORDER = "#262b35"
BORDER_LIGHT = "#333a48"  # card border, hairline-brighter than BORDER for definition on a solid bg
TEXT = "#e6e9ef"
TEXT_DIM = "#9aa3b2"
ACCENT = "#0a84ff"        # iOS dark-mode system blue
ACCENT_DIM = "#2d5cad"
ACCENT_TRACK = "#1a2740"  # meter/progress track - a lighter step of the accent ramp, not flat black

OK = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"
INFO = "#58a6ff"

SEVERITY_COLOUR = {"high": BAD, "medium": WARN, "low": TEXT_DIM, "info": INFO}

# Per-resource colours for the settings table's Impact column - deliberately
# distinct from OK/WARN/BAD/ACCENT/INFO above (those carry a "good/caution/
# bad" meaning elsewhere; these three just distinguish which hardware
# resource a setting trades against, with no judgement attached).
GPU_COLOUR = "#3fd0c9"   # teal
CPU_COLOUR = "#ff6ec7"   # pink/magenta
VRAM_COLOUR = "#b388ff"  # purple
RESOURCE_COLOUR = {"gpu": GPU_COLOUR, "cpu": CPU_COLOUR, "vram": VRAM_COLOUR}

MONO = "Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', monospace"

# Windows 11's own "Segoe UI Variable" is as close as this platform gets to
# SF Pro's rounded, friendly weight - tried first, with the classic Segoe UI
# (present on every Windows version this app supports) as the real fallback.
FONT_STACK = "'Segoe UI Variable Display', 'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif"

STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: {FONT_STACK};
    font-size: 13px;
}}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}

QFrame#Card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BG_RAISED_TOP}, stop:1 {BG_RAISED});
    border: 1px solid {BORDER_LIGHT};
    border-radius: 18px;
}}
QFrame#HeaderBar {{ border: none; border-bottom: 1px solid {BORDER}; }}
QLabel#CardTitle {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.2px;
}}
QLabel#Hero {{ font-size: 38px; font-weight: 700; letter-spacing: -0.5px; }}
QLabel#HeroUnit {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Mono {{ font-family: {MONO}; color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Title {{ font-size: 20px; font-weight: 700; letter-spacing: 0.2px; }}

QPushButton {{
    background-color: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 9px 18px;
    color: {TEXT};
}}
QPushButton:hover {{ background-color: {BG_HOVER}; border-color: {ACCENT_DIM}; }}
QPushButton:pressed {{ background-color: {BG_PRESSED}; }}
QPushButton:disabled {{ color: #5b6373; border-color: #1d222b; }}
QPushButton#Primary {{
    background-color: {ACCENT}; border-color: {ACCENT}; color: #ffffff; font-weight: 600;
}}
QPushButton#Primary:hover {{ background-color: #3d9bff; }}
QPushButton#Primary:pressed {{ background-color: {ACCENT_DIM}; }}
QPushButton#Primary:disabled {{ background-color: {ACCENT_DIM}; color: #9fb4d8; }}

QPushButton#TableButton {{
    padding: 2px 10px;
    border-radius: 9px;
    font-size: 12px;
}}

QPushButton#Preset {{
    background-color: {BG_SUNKEN}; padding: 10px 8px; font-weight: 600; border-radius: 14px;
}}
QPushButton#Preset:hover:!checked {{ background-color: {BG_HOVER}; }}
QPushButton#Preset:checked {{
    background-color: {ACCENT}; border-color: {ACCENT}; color: #ffffff;
}}
QPushButton#Preset:checked:hover {{ background-color: #3d9bff; }}

QComboBox, QSpinBox, QLineEdit {{
    background-color: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: 11px;
    padding: 7px 11px;
    selection-background-color: {ACCENT_DIM};
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {BORDER_LIGHT}; }}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_RAISED}; border: 1px solid {BORDER}; border-radius: 10px;
    selection-background-color: {ACCENT_DIM};
}}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 5px;
    border: 1px solid {BORDER}; background: {BG_SUNKEN};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_DIM}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 16px; top: 4px; }}
QTabBar {{ margin-bottom: 2px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 8px 18px; margin: 2px 3px 6px;
    border: 1px solid transparent;
    border-radius: 12px;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: #ffffff; background: {ACCENT};
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; background: {BG_HOVER}; }}

QTableWidget {{
    background-color: {BG_RAISED}; border: none; border-radius: 12px;
    gridline-color: {BORDER}; alternate-background-color: #14171d;
}}
QHeaderView::section {{
    background-color: {BG_SUNKEN}; color: {TEXT_DIM};
    border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 8px; font-weight: 600;
}}
QTableWidget::item {{ padding: 4px 8px; }}
QTableWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}

QPlainTextEdit {{
    background-color: {BG_SUNKEN}; border: none;
    font-family: {MONO}; font-size: 12px;
    selection-background-color: {ACCENT_DIM};
}}
QTextEdit#SettingDetail {{
    background-color: {BG_RAISED};
    border: 1px solid {BORDER};
    border-radius: 14px;
    padding: 12px 16px;
    font-family: {FONT_STACK};
    font-size: 13px;
    selection-background-color: {ACCENT_DIM};
}}
QSplitter::handle:vertical {{
    height: 6px;
    background: {BORDER};
    margin: 2px 0;
    border-radius: 3px;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2f3542; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3c4454; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2f3542; border-radius: 5px; min-width: 30px; }}

QProgressBar {{
    background-color: {ACCENT_TRACK}; border: none; border-radius: 7px;
    max-height: 14px; min-height: 14px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 7px; }}

QStatusBar {{ background: {BG_SUNKEN}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QToolTip {{
    background-color: {BG_RAISED}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 7px 10px; border-radius: 10px;
}}
"""
