"""A tactical-HUD stylesheet, kept in one place so the whole window reads as
one thing.

Angular, near-black, cyan/magenta - the "Tactical HUD" direction picked from
a round of mockups (2026-09-17). Qt's stylesheet engine has no box-shadow,
text-shadow, CSS grid backgrounds, clip-path or keyframe animation, so the
glow/scanline/spinning-reticle flourishes from the HTML mockup are not all
reproduced here - see app.py's `card()` and the Prediction card for the one
piece (a QGraphicsDropShadowEffect glow) that *is* worth doing in Python.
"""

from __future__ import annotations

BG = "#07080a"
BG_RAISED = "#0e1013"
BG_RAISED_TOP = "#13171a"  # gradient top-stop for Card - a hair lighter, for a subtle raised feel
BG_SUNKEN = "#040506"
BG_HOVER = "#151a1d"   # button hover fill - a visible lift, not just a border-color swap
BG_PRESSED = "#020304"
BORDER = "#1c2b2e"
BORDER_LIGHT = "#2a3f44"  # card border, hairline-brighter than BORDER for definition on a solid bg
TEXT = "#d8fbff"
TEXT_DIM = "#7b98a0"
ACCENT = "#00e5ff"        # tactical-HUD cyan
ACCENT_DIM = "#0a5f6e"
ACCENT_TRACK = "#0a2a30"  # meter/progress track - a lighter step of the accent ramp, not flat black

OK = "#39ff88"
WARN = "#ffd60a"
BAD = "#ff2b6d"
INFO = "#00e5ff"

SEVERITY_COLOUR = {"high": BAD, "medium": WARN, "low": TEXT_DIM, "info": INFO}

# Per-resource colours for the settings table's Impact column - deliberately
# distinct from OK/WARN/BAD/ACCENT/INFO above (those carry a "good/caution/
# bad" meaning elsewhere; these three just distinguish which hardware
# resource a setting trades against, with no judgement attached). GPU shares
# the accent cyan on purpose - GPU is the resource this app spends most of
# its attention on, so the two reinforce each other everywhere they appear.
GPU_COLOUR = ACCENT     # cyan
CPU_COLOUR = "#ff2b6d"  # magenta
VRAM_COLOUR = "#ffd60a"  # gold
RESOURCE_COLOUR = {"gpu": GPU_COLOUR, "cpu": CPU_COLOUR, "vram": VRAM_COLOUR}

MONO = "'Cascadia Mono', 'Cascadia Code', Consolas, 'DejaVu Sans Mono', monospace"

# Monospace first, all the way through - the HUD look this app moved to
# reads as a terminal/targeting readout, not a soft consumer app, so body
# text uses the same family as the numbers instead of a humanist sans.
FONT_STACK = f"{MONO}, 'Segoe UI', sans-serif"

# Small and sharp, not rounded - the defining shape change from the previous
# iOS-inspired pass. A couple of values below intentionally stay a hair
# larger than this (dropdown popups, tooltips) purely so text doesn't touch
# a corner at the tightest spots; the visual language is still "angular."
RADIUS = 3

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
    border-left: 3px solid {ACCENT};
    border-radius: {RADIUS}px;
}}
QFrame#HeaderBar {{ border: none; border-bottom: 1px solid {BORDER}; }}
QLabel#CardTitle {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#Hero {{ font-size: 38px; font-weight: 700; letter-spacing: -0.5px; color: {ACCENT}; }}
QLabel#HeroUnit {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Mono {{ font-family: {MONO}; color: {TEXT_DIM}; font-size: 12px; }}
QLabel#Title {{ font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }}

QPushButton {{
    background-color: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 9px 18px;
    color: {TEXT};
}}
QPushButton:hover {{ background-color: {BG_HOVER}; border-color: {ACCENT_DIM}; }}
QPushButton:pressed {{ background-color: {BG_PRESSED}; }}
QPushButton:disabled {{ color: #465559; border-color: #14191b; }}
QPushButton#Primary {{
    background-color: {ACCENT}; border-color: {ACCENT}; color: #001318; font-weight: 700;
}}
QPushButton#Primary:hover {{ background-color: #33ecff; }}
QPushButton#Primary:pressed {{ background-color: #00b8cc; }}
QPushButton#Primary:disabled {{ background-color: {ACCENT_DIM}; color: #6b98a0; }}

QPushButton#TableButton {{
    padding: 2px 10px;
    border-radius: {RADIUS}px;
    font-size: 12px;
}}

QPushButton#Preset {{
    background-color: {BG_SUNKEN}; padding: 10px 8px; font-weight: 700; border-radius: {RADIUS}px;
    letter-spacing: 1px;
}}
QPushButton#Preset:hover:!checked {{ background-color: {BG_HOVER}; }}
QPushButton#Preset:checked {{
    background-color: {ACCENT}; border-color: {ACCENT}; color: #001318;
}}
QPushButton#Preset:checked:hover {{ background-color: #33ecff; }}

QComboBox, QSpinBox, QLineEdit {{
    background-color: {BG_SUNKEN};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 7px 11px;
    selection-background-color: {ACCENT_DIM};
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {BORDER_LIGHT}; }}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background-color: {BG_RAISED}; border: 1px solid {BORDER}; border-radius: {RADIUS + 4}px;
    selection-background-color: {ACCENT_DIM};
}}

QCheckBox {{ spacing: 8px; padding: 3px 0; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 3px;
    border: 1px solid {BORDER}; background: {BG_SUNKEN};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT_DIM}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: {RADIUS}px; top: 4px; }}
QTabBar {{ margin-bottom: 2px; }}
QTabBar::tab {{
    background: transparent; color: {TEXT_DIM};
    padding: 8px 18px; margin: 2px 3px 6px;
    border: 1px solid transparent;
    border-radius: {RADIUS}px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QTabBar::tab:selected {{
    color: #001318; background: {ACCENT};
}}
QTabBar::tab:hover:!selected {{ color: {TEXT}; background: {BG_HOVER}; }}

QTableWidget {{
    background-color: {BG_RAISED}; border: none; border-radius: {RADIUS}px;
    gridline-color: {BORDER}; alternate-background-color: #0a0c0e;
}}
QHeaderView::section {{
    background-color: {BG_SUNKEN}; color: {TEXT_DIM};
    border: none; border-bottom: 1px solid {BORDER};
    padding: 8px 8px; font-weight: 700; letter-spacing: 1px;
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
    border-radius: {RADIUS}px;
    padding: 12px 16px;
    font-family: {FONT_STACK};
    font-size: 13px;
    selection-background-color: {ACCENT_DIM};
}}
QSplitter::handle:vertical {{
    height: 6px;
    background: {BORDER};
    margin: 2px 0;
    border-radius: 2px;
}}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #1c3238; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #274449; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #1c3238; border-radius: 4px; min-width: 30px; }}

QProgressBar {{
    background-color: {ACCENT_TRACK}; border: none; border-radius: 2px;
    max-height: 14px; min-height: 14px;
    text-align: center;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 2px; }}

QStatusBar {{ background: {BG_SUNKEN}; color: {TEXT_DIM}; border-top: 1px solid {BORDER}; }}
QToolTip {{
    background-color: {BG_RAISED}; color: {TEXT};
    border: 1px solid {ACCENT_DIM}; padding: 7px 10px; border-radius: {RADIUS}px;
}}
"""
