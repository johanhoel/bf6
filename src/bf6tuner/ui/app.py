"""The main window.

Everything recomputes on any change, because the whole value of the tool is
seeing the predicted frame rate move when you change the target. Nothing is
written to disk until an explicit button press.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from .. import compare, database, hardware, paths, prefs, writer
from ..engine import PRESETS, Recommendation, Target, recommend
from . import theme
from .locate import LocateDialog
from .restore import RestoreDialog

RESOLUTIONS = [
    ("1920x1080", 1920, 1080), ("2560x1080", 2560, 1080), ("2560x1440", 2560, 1440),
    ("3440x1440", 3440, 1440), ("3840x1600", 3840, 1600), ("3840x2160", 3840, 2160),
    ("5120x1440", 5120, 1440), ("5120x2160", 5120, 2160),
]
PRESET_BLURB = {
    "esports": "Frames above all else",
    "competitive": "Fast, but still readable",
    "balanced": "Match your monitor",
    "quality": "Best image at 60+",
}


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(9)
    if title:
        label = QLabel(title.upper())
        label.setObjectName("CardTitle")
        layout.addWidget(label)
    return frame, layout


def dim(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Dim")
    label.setWordWrap(True)
    return label


class _NoScrollComboBox(QComboBox):
    """Drop-in QComboBox that ignores wheel events unless it has keyboard focus.

    Without this, scrolling the settings table silently changes option values —
    the user never sees the widget activate and has no idea a setting moved.
    The widget must be explicitly clicked (keyboard focus) before the wheel works.
    """
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class _NoScrollSpinBox(QSpinBox):
    """Same guard for spin-box editors."""
    def wheelEvent(self, event) -> None:  # type: ignore[override]
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# Amber palette for "you changed this from the recommendation".
_OVERRIDE_FG   = QColor(theme.WARN)          # #d29922 amber
_OVERRIDE_BG   = QColor("#2b2208")            # very dark amber tint
_OVERRIDE_EDGE = theme.WARN                   # reused in stylesheet strings
_NORMAL_FG     = QColor(theme.TEXT)


class DetectWorker(QThread):
    """Hardware detection shells out to PowerShell, so keep it off the UI thread."""

    finished_ok = Signal(object, object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.finished_ok.emit(hardware.detect(), paths.discover(prefs.load()))
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self, db: database.Database) -> None:
        super().__init__()
        self.db = db
        self.profile = hardware.HardwareProfile()
        self.game = paths.GamePaths()
        self.rec: Recommendation | None = None
        self.comparison: compare.Comparison | None = None
        self.setting_overrides: dict = prefs.load_setting_overrides()
        self._setting_editors: dict[str, QWidget] = {}
        self._setting_rows: dict[str, int] = {}
        self._loading = True

        self.setWindowTitle(f"{APP_NAME} {__version__} - Battlefield 6 configurator")
        self.resize(1320, 880)
        self.setMinimumSize(1080, 700)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 12)
        outer.setSpacing(14)

        outer.addLayout(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_sidebar(), 0)
        body.addWidget(self._build_main_panel(), 1)
        outer.addLayout(body, 1)

        outer.addLayout(self._build_action_bar())

        self.statusBar().showMessage(f"Database {db.version} loaded from {db.source}")
        self._loading = False
        self.redetect()

    # -- construction ------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = dim("Hardware-aware Battlefield 6 settings and User.cfg")
        stack = QVBoxLayout()
        stack.setSpacing(0)
        stack.addWidget(title)
        stack.addWidget(subtitle)
        row.addLayout(stack)
        row.addStretch(1)

        self.redetect_button = QPushButton("Re-detect hardware")
        self.redetect_button.clicked.connect(self.redetect)
        row.addWidget(self.redetect_button)
        return row

    def _build_sidebar(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(14)

        hw_card, hw_layout = card("Your machine")
        self.hw_labels: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(7)
        for row, key in enumerate(("CPU", "GPU", "Memory", "Display", "Storage")):
            caption = QLabel(key)
            caption.setObjectName("Dim")
            caption.setAlignment(Qt.AlignTop | Qt.AlignLeft)
            value = QLabel("detecting...")
            value.setWordWrap(True)
            grid.addWidget(caption, row, 0)
            grid.addWidget(value, row, 1)
            self.hw_labels[key] = value
        hw_layout.addLayout(grid)
        self.hw_note = dim("")
        self.hw_note.setVisible(False)
        hw_layout.addWidget(self.hw_note)
        layout.addWidget(hw_card)

        preset_card, preset_layout = card("Preset")
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        preset_grid = QGridLayout()
        preset_grid.setSpacing(6)
        for index, name in enumerate(PRESETS):
            button = QPushButton(name.capitalize())
            button.setObjectName("Preset")
            button.setCheckable(True)
            button.setToolTip(PRESET_BLURB[name])
            button.setChecked(name == "competitive")
            self.preset_group.addButton(button, index)
            preset_grid.addWidget(button, index // 2, index % 2)
        preset_layout.addLayout(preset_grid)
        self.preset_blurb = dim(PRESET_BLURB["competitive"])
        preset_layout.addWidget(self.preset_blurb)
        self.preset_group.idClicked.connect(self._on_preset)
        layout.addWidget(preset_card)

        target_card, target_layout = card("Target")
        form = QGridLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setColumnStretch(1, 1)

        form.addWidget(dim("Resolution"), 0, 0)
        self.resolution_box = QComboBox()
        self.resolution_box.setMinimumHeight(30)
        for label, _, _ in RESOLUTIONS:
            self.resolution_box.addItem(label)
        self.resolution_box.currentIndexChanged.connect(self.refresh)
        form.addWidget(self.resolution_box, 0, 1)

        form.addWidget(dim("Refresh rate"), 1, 0)
        self.refresh_box = QSpinBox()
        self.refresh_box.setMinimumHeight(30)
        self.refresh_box.setRange(30, 600)
        self.refresh_box.setSuffix(" Hz")
        self.refresh_box.setValue(144)
        self.refresh_box.valueChanged.connect(self.refresh)
        form.addWidget(self.refresh_box, 1, 1)
        target_layout.addLayout(form)

        self.checkboxes: dict[str, QCheckBox] = {}
        for key, label, tip, default in (
            ("vrr", "G-Sync / FreeSync display", "Caps frames just below refresh so VRR stays engaged.", True),
            ("hdr", "HDR display (real HDR, not HDR400)", "Only tick this for a panel with local dimming or OLED.", False),
            ("background_load", "I stream, record, or keep heavy apps open",
             "Reserves a logical processor or two for background work.", False),
            ("frame_gen", "Allow frame generation", "Raises the FPS counter and the input latency together.", False),
            ("threads", "Allow Thread.* overrides (advanced)",
             "Only ever applied on homogeneous CPUs with 8+ cores. Measure the result.", False),
            ("legacy", "Include legacy Frostbite keys",
             "Older BF3/BF4/BFV console variables that may be silently ignored by BF6.", False),
            ("overlay", "Enable the in-game FPS overlay", "Strongly recommended while testing.", True),
        ):
            box = QCheckBox(label)
            box.setToolTip(tip)
            box.setChecked(default)
            box.toggled.connect(self.refresh)
            self.checkboxes[key] = box
            target_layout.addWidget(box)

        layout.addWidget(target_card)
        layout.addStretch(1)

        # Scroll rather than squash: the window can be shorter than this column.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(390)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(panel)
        return scroll

    def _build_main_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        pred_card, pred_layout = card("Prediction")
        row = QHBoxLayout()
        row.setSpacing(28)
        self.hero: dict[str, QLabel] = {}
        for key, caption in (
            ("predicted", "expected in game"), ("gpu", "GPU limit"),
            ("cpu", "CPU limit"), ("cap", "frame cap"),
        ):
            column = QVBoxLayout()
            column.setSpacing(0)
            value = QLabel("--")
            value.setObjectName("Hero")
            unit = QLabel(caption)
            unit.setObjectName("HeroUnit")
            column.addWidget(value)
            column.addWidget(unit)
            self.hero[key] = value
            row.addLayout(column)
        row.addStretch(1)
        self.bottleneck_label = QLabel("")
        self.bottleneck_label.setAlignment(Qt.AlignRight | Qt.AlignBottom)
        self.bottleneck_label.setWordWrap(True)
        self.bottleneck_label.setFixedWidth(300)
        row.addWidget(self.bottleneck_label)
        pred_layout.addLayout(row)
        self.before_after = QLabel("")
        self.before_after.setWordWrap(True)
        pred_layout.addWidget(self.before_after)
        self.prediction_note = dim("")
        pred_layout.addWidget(self.prediction_note)
        layout.addWidget(pred_card)

        self.tabs = QTabWidget()
        self.comparison_area = self._make_scroll()
        self.tabs.addTab(self.comparison_area, "Current vs recommended")

        self.settings_table = self._make_table(
            ["Setting", "Value", "", "Why"], [250, 200, 155, -1]
        )
        self.settings_table.verticalHeader().setDefaultSectionSize(38)
        self.tabs.addTab(self._build_settings_tab(), "In-game settings")

        self.cfg_view = QPlainTextEdit()
        self.cfg_view.setReadOnly(True)
        self.cfg_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.cfg_view, "User.cfg")

        self.warnings_area = self._make_scroll()
        self.tabs.addTab(self.warnings_area, "Warnings")

        self.checks_area = self._make_scroll()
        self.tabs.addTab(self.checks_area, "System checks")

        layout.addWidget(self.tabs, 1)
        return panel


    def _build_settings_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        self.override_note = dim(
            "Every value here is editable. Change one and the prediction, the frame cap "
            "and the comparison all update to match. Click any row to see a full description."
        )
        row.addWidget(self.override_note, 1)

        self.reset_overrides_button = QPushButton("Reset all to recommended")
        self.reset_overrides_button.clicked.connect(self.reset_all_overrides)
        row.addWidget(self.reset_overrides_button)
        layout.addLayout(row)

        # Splitter: table on top, detail pane below.
        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.settings_table)

        self.setting_detail = QTextEdit()
        self.setting_detail.setReadOnly(True)
        self.setting_detail.setObjectName("SettingDetail")
        self.setting_detail.setMinimumHeight(80)
        self.setting_detail.setMaximumHeight(200)
        self.setting_detail.setPlaceholderText(
            "Click any row to see a full description and trade-offs for that setting."
        )
        splitter.addWidget(self.setting_detail)
        splitter.setSizes([9999, 150])

        layout.addWidget(splitter, 1)
        return container

    def _make_editor(self, choice, setting: dict) -> QWidget | None:
        """An editor matched to the setting's type, or None if it is not ours to change."""
        # "keep" covers the settings the app never writes (mouse and audio). Field
        # of view and brightness are personal but still written, so they stay
        # editable - they are the ones people most want to set themselves.
        if choice.value == "keep" or setting.get("type") == "resolution":
            return None

        kind = setting.get("type")
        if kind in ("enum", "bool", "upscaler") or setting.get("options"):
            box = _NoScrollComboBox()
            options = setting.get("options")
            if not options and kind == "bool":
                options = [{"value": 0, "label": "Off"}, {"value": 1, "label": "On"}]
            for option in options or []:
                box.addItem(str(option["label"]), option["value"])
            box.currentIndexChanged.connect(
                lambda _=0, sid=choice.setting_id: self._on_editor_changed(sid)
            )
            return box

        if kind == "slider":
            spin = _NoScrollSpinBox()
            spin.setRange(int(setting.get("min", 0)), int(setting.get("max", 1000)))
            unit = setting.get("unit", "")
            if unit and len(unit) <= 6:
                spin.setSuffix(f" {unit}")
            spin.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(
                lambda _=0, sid=choice.setting_id: self._on_editor_changed(sid)
            )
            return spin

        return None

    def _sync_settings_table(self) -> None:
        """Fill the table, creating each row's editor once and updating it after.

        Rebuilding the table would destroy the widget whose signal we are inside,
        so rows are created on the first pass and only their values change later.
        """
        rec = self.rec
        assert rec is not None
        table = self.settings_table
        building = table.rowCount() == 0

        if building:
            table.setRowCount(len(rec.settings))
            table.currentCellChanged.connect(self._on_setting_row_changed)

        for row, choice in enumerate(rec.settings):
            setting = self.db.setting(choice.setting_id) or {}
            self._setting_rows[choice.setting_id] = row

            if building:
                name = QTableWidgetItem(choice.label)
                name.setToolTip(choice.menu)
                table.setItem(row, 0, name)

                editor = self._make_editor(choice, setting)
                if editor is not None:
                    self._setting_editors[choice.setting_id] = editor
                    table.setCellWidget(row, 1, editor)
                else:
                    table.setItem(row, 1, QTableWidgetItem(""))

                if editor is not None:
                    reset = QPushButton("Reset")
                    reset.setFlat(True)
                    reset.clicked.connect(
                        lambda _=False, sid=choice.setting_id: self.reset_override(sid)
                    )
                    table.setCellWidget(row, 2, reset)

                table.setItem(row, 3, QTableWidgetItem(""))

            name_item = table.item(row, 0)
            if name_item is not None:
                name_item.setText(("◆ " if choice.overridden else "") + choice.label)
                if choice.overridden:
                    font = QFont()
                    font.setBold(True)
                    name_item.setFont(font)
                    name_item.setForeground(_OVERRIDE_FG)
                    name_item.setBackground(_OVERRIDE_BG)
                else:
                    name_item.setFont(QFont())
                    name_item.setForeground(_NORMAL_FG)
                    name_item.setBackground(QColor())  # transparent / default

            editor = self._setting_editors.get(choice.setting_id)
            if editor is not None:
                editor.blockSignals(True)
                if isinstance(editor, QComboBox):
                    index = editor.findData(choice.value)
                    if index >= 0:
                        editor.setCurrentIndex(index)
                elif isinstance(editor, QSpinBox):
                    try:
                        editor.setValue(int(round(float(choice.value))))
                    except (TypeError, ValueError):
                        pass
                editor.blockSignals(False)
                # Colour the editor itself so the change is obvious even when
                # the row is not selected.
                if choice.overridden:
                    editor.setStyleSheet(
                        f"background-color: {_OVERRIDE_BG.name()};"
                        f" border: 2px solid {_OVERRIDE_EDGE};"
                        f" border-radius: 6px;"
                        f" color: {_OVERRIDE_EDGE};"
                        f" font-weight: 600;"
                    )
                    editor.setToolTip(
                        f"You set this to {choice.display}.\n"
                        f"Engine recommended: {choice.recommended_display}"
                    )
                else:
                    editor.setStyleSheet("")
                    editor.setToolTip("")
            else:
                cell = table.item(row, 1)
                if cell is not None:
                    cell.setText("leave as-is" if choice.value == "keep" else choice.display)
                    cell.setForeground(Qt.gray)

            reset_button = table.cellWidget(row, 2)
            if isinstance(reset_button, QPushButton):
                reset_button.setEnabled(choice.overridden)
                if choice.overridden and choice.recommended_display:
                    reset_button.setText(f"↩ {choice.recommended_display}")
                    reset_button.setToolTip(
                        f"Click to restore the recommended value: {choice.recommended_display}"
                    )
                else:
                    reset_button.setText("Reset")
                    reset_button.setToolTip("Already matches the recommendation")

            why = table.item(row, 3)
            if why is not None:
                if choice.overridden and choice.recommended_display:
                    why_text = f"was: {choice.recommended_display}    {choice.reason}"
                    why.setForeground(_OVERRIDE_FG)
                else:
                    why_text = choice.reason
                    why.setForeground(QColor(theme.TEXT_DIM))
                why.setText(why_text)
                why.setToolTip(why_text)

        count = len(rec.overrides)
        self.reset_overrides_button.setEnabled(bool(count))
        self.tabs.setTabText(1, f"In-game settings ({count} changed)" if count
                             else "In-game settings")

    # -- override handling -------------------------------------------------

    def _on_editor_changed(self, setting_id: str) -> None:
        if self._loading:
            return
        editor = self._setting_editors.get(setting_id)
        if editor is None:
            return
        value = editor.currentData() if isinstance(editor, QComboBox) else editor.value()

        choice = next((c for c in (self.rec.settings if self.rec else [])
                       if c.setting_id == setting_id), None)
        # Selecting the recommended value again is a reset, not an override.
        if choice is not None and not choice.overridden and value == choice.value:
            return
        if choice is not None and choice.overridden and value == choice.recommended_value:
            self.reset_override(setting_id)
            return

        self.setting_overrides[setting_id] = value
        prefs.set_setting_override(setting_id, value)
        self.refresh()

    def reset_override(self, setting_id: str) -> None:
        self.setting_overrides.pop(setting_id, None)
        prefs.set_setting_override(setting_id, None)
        self.refresh()

    def reset_all_overrides(self) -> None:
        if not self.setting_overrides:
            return
        count = len(self.setting_overrides)
        confirm = QMessageBox.question(
            self, "Reset settings",
            f"Drop {count} setting(s) you changed and go back to the recommendation "
            f"for the {self.current_target().preset} preset?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return
        self.setting_overrides.clear()
        prefs.clear_setting_overrides()
        self.refresh()

    # -- setting detail pane -----------------------------------------------

    def _on_setting_row_changed(
        self, row: int, _col: int, _prev_row: int, _prev_col: int
    ) -> None:
        if self.rec is None or row < 0 or row >= len(self.rec.settings):
            self.setting_detail.clear()
            return
        choice = self.rec.settings[row]
        setting = self.db.setting(choice.setting_id)
        self._update_setting_detail(choice, setting)

    def _update_setting_detail(
        self, choice: "SettingChoice", setting: dict | None
    ) -> None:
        """Render the full description + trade-offs for a setting into the detail pane."""
        if setting is None:
            self.setting_detail.setPlainText(choice.reason)
            return

        note = setting.get("note", "")
        menu = setting.get("menu", "").replace(" > ", " › ")
        tradeoff = setting.get("tradeoff", {})
        impact = setting.get("impact", {})
        gpu_cost = impact.get("gpu", 0)
        cpu_cost = impact.get("cpu", 0)
        vram_cost = impact.get("vram", 0)

        def _cost_label(val: int) -> str:
            if val >= 5: return "very high"
            if val >= 3: return "high"
            if val >= 2: return "medium"
            if val >= 1: return "low"
            return "none"

        parts: list[str] = []
        parts.append(
            f"<b style='font-size:14px'>{choice.label}</b>"
            f"&nbsp;&nbsp;<span style='color:{theme.TEXT_DIM}'>{menu}</span>"
        )

        cost_bits = []
        if gpu_cost >= 1:
            cost_bits.append(f"GPU: {_cost_label(gpu_cost)}")
        if cpu_cost >= 1:
            cost_bits.append(f"CPU: {_cost_label(cpu_cost)}")
        if vram_cost >= 2:
            cost_bits.append(f"VRAM: {_cost_label(vram_cost)}")
        if cost_bits:
            parts.append(
                f"<br><span style='color:{theme.TEXT_DIM}; font-size:11px'>"
                f"Performance impact — {' · '.join(cost_bits)}</span>"
            )

        if note:
            parts.append(f"<br><br>{note}")

        def _section(label: str, colour: str, data: dict) -> None:
            pros = data.get("pros", [])
            cons = data.get("cons", [])
            if not pros and not cons:
                return
            parts.append(
                f"<br><br><span style='color:{colour}; font-weight:600'>{label}</span>"
            )
            for p in pros:
                parts.append(f"<br><span style='color:{theme.OK}'>+</span>&nbsp;{p}")
            for c in cons:
                parts.append(f"<br><span style='color:{theme.WARN}'>−</span>&nbsp;{c}")

        _section("Raising it", theme.INFO, tradeoff.get("raise", {}))
        _section("Lowering it", theme.INFO, tradeoff.get("lower", {}))

        if choice.overridden and choice.recommended_display:
            parts.append(
                f"<br><br><span style='color:{theme.WARN}'>You have overridden this. "
                f"The engine recommended {choice.recommended_display}.</span>"
            )

        self.setting_detail.setHtml("".join(parts))

    def _make_table(self, headers: list[str], widths: list[int]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        header = table.horizontalHeader()
        # Every column is interactive (drag to resize). The last one also
        # stretches to fill spare space, but is still draggable.
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(True)
        header.setMinimumSectionSize(40)
        for index, width in enumerate(widths):
            if width > 0:
                table.setColumnWidth(index, width)
        return table

    def _make_scroll(self) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        layout.addStretch(1)
        area.setWidget(inner)
        return area

    @staticmethod
    def _wrap(widget: QWidget) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(widget)
        return container

    def _build_action_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        self.path_label = QLabel("")
        self.path_label.setObjectName("Mono")
        self.path_label.setWordWrap(True)
        row.addWidget(self.path_label, 1)

        self.locate_button = QPushButton("Locate files...")
        self.locate_button.setToolTip(
            "Point the app at PROFSAVE_profile or the game folder by hand. Remembered afterwards."
        )
        self.locate_button.clicked.connect(self.locate_files)
        row.addWidget(self.locate_button)

        self.backup_button = QPushButton("Back up now")
        self.backup_button.setToolTip("Snapshot both config files without changing anything.")
        self.backup_button.clicked.connect(self.backup_now)
        row.addWidget(self.backup_button)

        self.restore_button = QPushButton("Restore...")
        self.restore_button.setToolTip("Put your configuration back to an earlier snapshot.")
        self.restore_button.clicked.connect(self.open_restore)
        row.addWidget(self.restore_button)

        self.export_button = QPushButton("Export report")
        self.export_button.clicked.connect(self.export_report)
        row.addWidget(self.export_button)

        self.save_button = QPushButton("Save User.cfg only")
        self.save_button.clicked.connect(self.save_user_cfg)
        row.addWidget(self.save_button)

        self.apply_button = QPushButton("Apply everything")
        self.apply_button.setObjectName("Primary")
        self.apply_button.setToolTip(
            "Writes User.cfg and the in-game settings, after taking one restore point covering both."
        )
        self.apply_button.clicked.connect(self.apply_everything)
        row.addWidget(self.apply_button)
        return row

    # -- detection ---------------------------------------------------------

    def redetect(self) -> None:
        self.redetect_button.setEnabled(False)
        self.statusBar().showMessage("Detecting hardware...")
        self.worker = DetectWorker()
        self.worker.finished_ok.connect(self._on_detected)
        self.worker.failed.connect(self._on_detect_failed)
        self.worker.start()

    def _on_detect_failed(self, detail: str) -> None:
        self.redetect_button.setEnabled(True)
        self.statusBar().showMessage("Hardware detection failed.")
        QMessageBox.warning(self, "Detection failed", detail[-1500:])

    def _on_detected(self, profile: hardware.HardwareProfile, game: paths.GamePaths) -> None:
        self.profile = profile
        self.game = game
        self.redetect_button.setEnabled(True)

        p = profile
        topology = f"{p.cores}C / {p.threads}T"
        if p.hybrid:
            topology += f" ({p.p_cores}P + {p.e_cores}E)"
        self.hw_labels["CPU"].setText(f"{p.cpu_name}\n{topology}")
        self.hw_labels["GPU"].setText(
            f"{p.gpu_name}\n{p.vram_gb:g} GB VRAM"
            + (f" - driver {p.driver_version}" if p.driver_version else "")
        )
        self.hw_labels["Memory"].setText(
            f"{p.ram_gb:g} GB"
            + (f" @ {p.ram_speed_mts} MT/s" if p.ram_speed_mts else "")
            + (f" - {len(p.ram_sticks)} module(s)" if p.ram_sticks else "")
        )
        self.hw_labels["Display"].setText(
            f"{p.width}x{p.height} @ {p.refresh_hz} Hz"
            + (f" (up to {p.max_refresh_hz} Hz)" if p.max_refresh_hz > p.refresh_hz else "")
        )
        media = p.drive_media.get(game.install_drive, "unknown") if game.install_drive else "-"
        self.hw_labels["Storage"].setText(
            f"{game.install_dir}" if game.install_dir else "Battlefield 6 install not found"
        )
        if game.install_dir:
            self.hw_labels["Storage"].setText(f"{game.install_dir}\nDrive {game.install_drive}: {media}")

        notes = list(p.detection_notes) + list(game.notes)
        self.hw_note.setText("\n\n".join(notes))
        self.hw_note.setVisible(bool(notes))

        self._loading = True
        label = f"{p.width}x{p.height}"
        index = self.resolution_box.findText(label)
        if index < 0:
            self.resolution_box.addItem(label)
            index = self.resolution_box.count() - 1
        self.resolution_box.setCurrentIndex(index)
        self.refresh_box.setValue(max(p.max_refresh_hz, p.refresh_hz, 60))
        self.checkboxes["hdr"].setChecked(p.hdr_display)
        self._loading = False

        self.statusBar().showMessage(
            "Hardware detected." if p.detected else "Using a sample profile - detection unavailable here."
        )
        self.refresh()

    # -- recomputation -----------------------------------------------------

    def _on_preset(self, index: int) -> None:
        self.preset_blurb.setText(PRESET_BLURB[PRESETS[index]])
        self.refresh()

    def current_target(self) -> Target:
        label = self.resolution_box.currentText()
        width, height = (int(part) for part in label.split("x", 1))
        return Target(
            preset=PRESETS[self.preset_group.checkedId()],
            width=width, height=height,
            refresh_hz=self.refresh_box.value(),
            vrr=self.checkboxes["vrr"].isChecked(),
            background_load=self.checkboxes["background_load"].isChecked(),
            hdr_display=self.checkboxes["hdr"].isChecked(),
            allow_frame_generation=self.checkboxes["frame_gen"].isChecked(),
            allow_thread_overrides=self.checkboxes["threads"].isChecked(),
            include_legacy_commands=self.checkboxes["legacy"].isChecked(),
            show_fps_overlay=self.checkboxes["overlay"].isChecked(),
        )

    def refresh(self) -> None:
        if self._loading:
            return
        media = (
            self.profile.drive_media.get(self.game.install_drive)
            if self.game.install_drive else None
        )
        self.rec = recommend(
            self.db, self.profile, self.current_target(), install_drive_media=media,
            overrides=self.setting_overrides,
        )
        self.comparison = compare.from_paths(
            self.db, self.rec, self.game.profsave, self.game.user_cfg
        )
        self._render()

    def _render(self) -> None:
        rec = self.rec
        assert rec is not None

        self.hero["predicted"].setText(str(rec.predicted_fps))
        self.hero["gpu"].setText(str(rec.gpu_fps))
        self.hero["cpu"].setText(str(rec.cpu_fps))
        self.hero["cap"].setText(str(rec.frame_cap))
        colour = {"CPU": theme.WARN, "GPU": theme.INFO, "balanced": theme.OK}[rec.bottleneck]
        self.hero["predicted"].setStyleSheet(f"color: {colour};")
        self.bottleneck_label.setText(
            f"<span style='color:{colour}; font-weight:600'>{rec.bottleneck}-limited</span>"
            f"<br><span style='color:{theme.TEXT_DIM}'>{rec.upscaler_tech}: "
            f"{rec.upscaler_mode.replace('_', ' ')}</span>"
        )
        comparison = self.comparison
        if comparison is not None and comparison.available:
            delta_colour = theme.OK if comparison.fps_delta > 0 else (
                theme.BAD if comparison.fps_delta < 0 else theme.TEXT_DIM)
            self.before_after.setText(
                f"<span style='color:{theme.TEXT_DIM}'>Right now you are running about</span> "
                f"<b>{comparison.current_predicted} FPS</b>"
                f"<span style='color:{theme.TEXT_DIM}'> &rarr; after applying, about </span>"
                f"<b>{comparison.new_predicted} FPS</b>&nbsp;&nbsp;"
                f"<span style='color:{delta_colour}; font-weight:700'>{comparison.fps_delta:+d}</span>"
            )
            self.before_after.setVisible(True)
        else:
            self.before_after.setVisible(False)

        note = (
            "Modelled estimate, not a measurement. Turn the in-game FPS overlay on and compare."
        )
        if rec.headroom_note:
            note = rec.headroom_note + "  " + note
        self.prediction_note.setText(note)

        self._sync_settings_table()

        self.cfg_view.setPlainText(writer.render_user_cfg(rec).replace("\r\n", "\n"))

        self._fill_scroll(self.warnings_area, [
            (w.severity, w.title, w.body, None) for w in rec.warnings
        ] or [("info", "Nothing to flag", "No warnings for this configuration.", None)])

        self._fill_scroll(self.checks_area, [
            (t.get("severity", "low"), t["label"], t.get("why", ""), t.get("how", ""))
            for t in rec.tweaks
        ] or [("info", "Nothing to check", "No system-level issues detected.", None)])

        self._fill_comparison()
        changed = 0 if comparison is None else (
            len(comparison.changes) + len([c for c in comparison.cfg_changes if c.action != "same"])
        )
        self.tabs.setTabText(0, f"Current vs recommended ({changed})" if changed
                             else "Current vs recommended")
        self.tabs.setTabText(3, f"Warnings ({len(rec.warnings)})")
        self.tabs.setTabText(4, f"System checks ({len(rec.tweaks)})")

        target_path = self.game.user_cfg or Path("(install folder not found)")
        cfg_mark = "  [set by hand]" if "install_dir" in self.game.overridden else ""
        profsave_mark = "  [set by hand]" if "profsave" in self.game.overridden else ""
        self.path_label.setText(
            f"User.cfg -> {target_path}{cfg_mark}\n"
            f"In-game  -> {self.game.profsave or '(PROFSAVE_profile not found - use Locate...)'}"
            f"{profsave_mark}"
        )
        self.apply_button.setText(
            "Apply everything" if self.game.profsave else "Apply User.cfg"
        )
        self.apply_button.setEnabled(
            self.game.user_cfg is not None or self.game.profsave is not None
        )


    def _fill_comparison(self) -> None:
        comparison = self.comparison
        container = self.comparison_area.widget()
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if comparison is None:
            layout.addStretch(1)
            return

        summary, summary_layout = card("Summary")
        headline = QLabel(comparison.headline)
        headline.setWordWrap(True)
        headline.setStyleSheet("font-size: 15px; font-weight: 600;")
        summary_layout.addWidget(headline)
        if comparison.available:
            summary_layout.addWidget(dim(
                f"Now: ~{comparison.current_predicted} FPS ({comparison.current_bottleneck}-limited, "
                f"GPU {comparison.current_gpu_fps} / CPU {comparison.current_cpu_fps}). "
                f"After: ~{comparison.new_predicted} FPS. "
                "Per-change figures below are marginal - what each change is worth on its own - so "
                "they will not sum to the total, and a change showing 0 FPS is one the other side of "
                "the bottleneck absorbs."
            ))
            if comparison.profsave_path:
                summary_layout.addWidget(dim(f"Read from {comparison.profsave_path}"))
        else:
            summary_layout.addWidget(dim(comparison.reason_unavailable))
            if self.game.searched:
                summary_layout.addWidget(dim(self.game.search_summary()))
            locate = QPushButton("Locate PROFSAVE_profile...")
            locate.clicked.connect(self.locate_files)
            button_row = QHBoxLayout()
            button_row.addWidget(locate)
            button_row.addStretch(1)
            summary_layout.addLayout(button_row)
        layout.addWidget(summary)

        for change in comparison.changes:
            layout.addWidget(self._change_card(change))

        if comparison.available:
            for title, entries, describe in (
                ("Already correct", comparison.unchanged, lambda c: c.label),
                ("Left alone - yours to set", comparison.personal,
                 lambda c: f"{c.label} (now {c.current_display})"),
                ("Not stored in the profile, so not comparable", comparison.unknown,
                 lambda c: f"{c.label} -> {c.new_display}"),
            ):
                if not entries:
                    continue
                frame, inner = card(f"{title} ({len(entries)})")
                inner.addWidget(dim(", ".join(describe(c) for c in entries)))
                layout.addWidget(frame)

        active = [c for c in comparison.cfg_changes if c.action != "same"]
        frame, inner = card(f"User.cfg - {len(active)} line(s) would change")
        if not active:
            inner.addWidget(dim("Your User.cfg already matches the recommendation."))
        for change in active:
            inner.addWidget(self._cfg_change_widget(change))
        layout.addWidget(frame)
        layout.addStretch(1)

    def _change_card(self, change: compare.SettingChange) -> QWidget:
        frame, inner = card("")
        colour = theme.OK if change.fps_delta > 0 else (
            theme.BAD if change.fps_delta < 0 else theme.TEXT_DIM)
        arrow_colour = theme.WARN if change.direction == "lower" else theme.INFO
        heading = QLabel(
            f"<b>{change.label}</b>&nbsp;&nbsp;"
            f"<span style='color:{theme.TEXT_DIM}'>{change.current_display}</span> "
            f"<span style='color:{arrow_colour}'>&rarr;</span> "
            f"<b>{change.new_display}</b>"
        )
        heading.setWordWrap(True)
        inner.addWidget(heading)

        impact = QLabel(
            f"<span style='color:{colour}; font-weight:700'>{change.impact_summary}</span>"
            f"&nbsp;&nbsp;<span style='color:{theme.TEXT_DIM}'>{change.menu}</span>"
        )
        impact.setWordWrap(True)
        inner.addWidget(impact)

        for pro in change.pros:
            row = QLabel(f"<span style='color:{theme.OK}'>+</span>&nbsp; {pro}")
            row.setWordWrap(True)
            inner.addWidget(row)
        for con in change.cons:
            row = QLabel(f"<span style='color:{theme.WARN}'>&minus;</span>&nbsp; {con}")
            row.setWordWrap(True)
            inner.addWidget(row)
        if change.reason:
            inner.addWidget(dim("Why: " + change.reason))
        return frame

    def _cfg_change_widget(self, change: compare.CfgChange) -> QWidget:
        holder = QWidget()
        inner = QVBoxLayout(holder)
        inner.setContentsMargins(0, 6, 0, 6)
        inner.setSpacing(3)

        if change.action == "add":
            head = (f"<span style='color:{theme.OK}'>+</span> "
                    f"<span style='font-family:{theme.MONO}'>{change.key} {change.new}</span>")
        elif change.action == "change":
            head = (f"<span style='color:{theme.INFO}'>~</span> "
                    f"<span style='font-family:{theme.MONO}'>{change.key}</span> "
                    f"<span style='color:{theme.TEXT_DIM}'>{change.current} &rarr;</span> "
                    f"<b>{change.new}</b>")
        else:
            head = (f"<span style='color:{theme.BAD}'>-</span> "
                    f"<span style='font-family:{theme.MONO}'>{change.key} {change.current}</span>"
                    f"<span style='color:{theme.TEXT_DIM}'> &nbsp;removed - saving rewrites the "
                    "whole file, and the restore point keeps it</span>")
        label = QLabel(head)
        label.setWordWrap(True)
        inner.addWidget(label)

        if change.summary:
            inner.addWidget(dim(change.summary))
        for pro in change.pros:
            row = QLabel(f"<span style='color:{theme.OK}'>+</span>&nbsp; {pro}")
            row.setWordWrap(True)
            inner.addWidget(row)
        for con in change.cons:
            row = QLabel(f"<span style='color:{theme.WARN}'>&minus;</span>&nbsp; {con}")
            row.setWordWrap(True)
            inner.addWidget(row)
        if change.risk in ("high", "unsafe") or change.confidence == "legacy":
            inner.addWidget(dim(f"[{change.confidence or 'unknown'} / risk: {change.risk or 'unknown'}]"))
        return holder

    def _fill_scroll(self, area: QScrollArea, items: list[tuple[str, str, str, str | None]]) -> None:
        container = area.widget()
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for severity, title, body, how in items:
            frame, inner = card("")
            colour = theme.SEVERITY_COLOUR.get(severity, theme.TEXT_DIM)
            heading = QLabel(
                f"<span style='color:{colour}; font-weight:700'>{severity.upper()}</span>"
                f"&nbsp;&nbsp;<b>{title}</b>"
            )
            heading.setWordWrap(True)
            inner.addWidget(heading)
            inner.addWidget(dim(body))
            if how:
                inner.addWidget(dim("How: " + how))
            layout.addWidget(frame)
        layout.addStretch(1)

    # -- actions -----------------------------------------------------------

    def _guard_game_closed(self) -> bool:
        if paths.is_game_running():
            QMessageBox.warning(
                self, "Battlefield 6 is running",
                "Close the game first. It rewrites its settings on exit and would "
                "discard anything written now.",
            )
            return False
        return True

    def _targets(self) -> dict[str, "Path | None"]:
        return {"user_cfg": self.game.user_cfg, "profsave": self.game.profsave}

    def backup_now(self) -> None:
        point = writer.create_restore_point(self._targets(), "Manual backup")
        if point is None:
            QMessageBox.information(
                self, "Nothing to back up",
                "Neither User.cfg nor PROFSAVE_profile was found, so there is nothing to snapshot yet.",
            )
            return
        writer.prune_restore_points()
        QMessageBox.information(
            self, "Backed up",
            f"Restore point taken {point.stamp}.\n\n{point.describe()}\n\n{point.directory}",
        )
        self.statusBar().showMessage(f"Restore point taken {point.stamp}")

    def locate_files(self) -> None:
        dialog = LocateDialog(self.game, self)
        dialog.exec()
        if dialog.changed:
            self.redetect()

    def open_restore(self) -> None:
        dialog = RestoreDialog(self)
        dialog.exec()
        if dialog.restored:
            # The files on disk changed, so the comparison is stale.
            self.refresh()
            self.statusBar().showMessage("Configuration restored.")

    def save_user_cfg(self) -> None:
        if self.rec is None or not self._guard_game_closed():
            return
        default = self.game.user_cfg or Path.home() / "User.cfg"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save User.cfg (must sit next to the game executable)",
            str(default), "Config files (*.cfg)",
        )
        if not path:
            return
        point = writer.create_restore_point({"user_cfg": Path(path), "profsave": self.game.profsave},
                                            "Before saving User.cfg")
        try:
            result = writer.write_user_cfg(self.rec, Path(path), restore_point=point)
        except Exception as exc:
            QMessageBox.critical(self, "Could not write User.cfg", str(exc))
            return
        writer.prune_restore_points()
        detail = result.message
        if point:
            detail += f"\n\nRestore point taken first:\n{point.directory}"
        QMessageBox.information(self, "User.cfg saved", detail)
        self.statusBar().showMessage(result.message)
        self.refresh()

    def apply_everything(self) -> None:
        if self.rec is None or self.comparison is None or not self._guard_game_closed():
            return

        cfg_path = self.game.user_cfg
        profsave = self.game.profsave
        if cfg_path is None and profsave is None:
            QMessageBox.warning(
                self, "Nothing to write",
                "Neither the game folder nor PROFSAVE_profile was found. Use 'Save User.cfg only' "
                "and pick the install folder by hand.",
            )
            return

        plan_lines: list[str] = []
        profsave_plan: list[tuple[str, str, str]] = []
        if profsave is not None:
            existing = writer.parse_profsave(profsave.read_text(encoding="utf-8", errors="ignore"))
            profsave_plan = writer.profsave_plan(self.rec, existing)
            plan_lines.append(
                f"In-game settings: {len(profsave_plan)} value(s) change in {profsave.name}"
                if profsave_plan else "In-game settings: already match, nothing to write"
            )
        else:
            plan_lines.append("In-game settings: PROFSAVE_profile not found, skipping")

        if cfg_path is not None:
            active = [c for c in self.comparison.cfg_changes if c.action != "same"]
            removals = self.comparison.cfg_removals
            plan_lines.append(f"User.cfg: {len(active)} line(s) change in {cfg_path}")
            if removals:
                plan_lines.append(
                    f"  including {len(removals)} existing line(s) that will be removed: "
                    + ", ".join(c.key for c in removals[:6])
                    + (" ..." if len(removals) > 6 else "")
                )

        detail = "\n".join(plan_lines)
        estimate = (
            f"\n\nEstimated effect: {self.comparison.current_predicted} -> "
            f"{self.comparison.new_predicted} FPS ({self.comparison.fps_delta:+d})."
            if self.comparison.available else ""
        )
        confirm = QMessageBox.question(
            self, "Apply configuration",
            f"{detail}{estimate}\n\nA restore point covering both files is taken first, and "
            "'Restore...' puts everything back in one click.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        point = writer.create_restore_point(
            self._targets(), f"Before applying {self.rec.target.preset}"
        )
        messages: list[str] = []
        try:
            if cfg_path is not None:
                messages.append(writer.write_user_cfg(self.rec, cfg_path, restore_point=point).message)
            if profsave is not None and profsave_plan:
                messages.append(writer.write_profsave(self.rec, profsave, restore_point=point).message)
        except Exception as exc:
            QMessageBox.critical(
                self, "Apply failed",
                f"{exc}\n\nNothing else was written. Use 'Restore...' if you need to roll back.",
            )
            return

        writer.prune_restore_points()
        if point:
            messages.append(f"\nRestore point: {point.stamp}\n{point.directory}")
        QMessageBox.information(self, "Applied", "\n".join(messages) or "Nothing needed changing.")
        self.statusBar().showMessage("Configuration applied.")
        self.refresh()

    def export_report(self) -> None:
        if self.rec is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export report", str(Path.home() / "bf6-tuner-report.txt"),
            "Text report (*.txt);;JSON (*.json)",
        )
        if not path:
            return
        target = Path(path)
        content = (
            writer.render_json(self.rec, self.comparison)
            if target.suffix.lower() == ".json"
            else writer.render_report(self.rec, self.comparison)
        )
        target.write_text(content, encoding="utf-8")
        self.statusBar().showMessage(f"Report written to {target}")
        QMessageBox.information(self, "Report exported", str(target))


def run() -> int:
    # Qt 6 handles high-DPI scaling on its own; no attribute needed.
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyleSheet(theme.STYLESHEET)

    try:
        db = database.load()
    except Exception as exc:
        QMessageBox.critical(None, f"{APP_NAME} could not start", str(exc))
        return 1

    window = MainWindow(db)
    window.show()
    return app.exec()
