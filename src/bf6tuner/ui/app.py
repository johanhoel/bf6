"""The main window.

Everything recomputes on any change, because the whole value of the tool is
seeing the predicted frame rate move when you change the target. Nothing is
written to disk until an explicit button press.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QStyle, QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit,
    QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from .. import benchmark, compare, database, hardware, icon, keybinds, paths, prefs, update, writer
from ..engine import LINKED_CFG_KEYS, PRESETS, Recommendation, Target, recommend
from . import theme
from .locate import LocateDialog
from .restore import RestoreDialog
from .update_dialog import UpdateDialog


def app_icon() -> QIcon:
    """Renders the same reticle icon packaging/make_icon.py bakes into the
    .exe, so a source checkout looks the same, not like a generic Python app.

    Only 3 sizes, not the full SIZES tuple in bf6tuner.icon - the 256px render
    is the expensive one (O(size^2) pure-Python pixel loop) and Qt scales a
    QIcon down cleanly, so there is little to gain from rendering all six.
    """
    result = QIcon()
    for size in (256, 64, 32):
        pixmap = QPixmap()
        pixmap.loadFromData(icon.render_png(size), "PNG")
        result.addPixmap(pixmap)
    return result

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
CFG_GROUP_TITLES = {
    "cpu_threading": "CPU Threading",
    "render_pipeline": "Render Pipeline",
    "frame_pacing": "Frame Pacing",
    "post_process": "Post Processing",
    "world_render": "World Render",
    "overlay": "Overlay",
    "misc": "Misc",
}


def card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    # A subtle drop shadow gives the card real elevation off the window
    # background - QSS alone has no box-shadow equivalent, so this is the
    # one bit of styling that has to be done in Python rather than theme.py.
    shadow = QGraphicsDropShadowEffect(frame)
    shadow.setBlurRadius(24)
    shadow.setOffset(0, 3)
    shadow.setColor(QColor(0, 0, 0, 110))
    frame.setGraphicsEffect(shadow)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 18)
    layout.setSpacing(10)
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


# Same threshold per resource everywhere a setting's impact is shown (the
# Impact column and the detail pane below it) so the two never disagree.
# VRAM's threshold is higher than GPU/CPU's because a lot of settings carry
# a residual vram: 1 that isn't worth calling out on its own.
_IMPACT_THRESHOLD = {"gpu": 1, "cpu": 1, "vram": 2}
_IMPACT_ORDER = ("gpu", "cpu", "vram")


def _cost_label(val: int) -> str:
    if val >= 5:
        return "very high"
    if val >= 3:
        return "high"
    if val >= 2:
        return "medium"
    if val >= 1:
        return "low"
    return "none"


def _impact_breakdown(setting: dict) -> list[tuple[str, int]]:
    """[(resource, cost), ...] for resources this setting meaningfully costs."""
    impact = setting.get("impact", {})
    return [
        (resource, impact.get(resource, 0))
        for resource in _IMPACT_ORDER
        if impact.get(resource, 0) >= _IMPACT_THRESHOLD[resource]
    ]


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


class UpdateCheckWorker(QThread):
    """Talks to the GitHub API, so keep it off the UI thread too."""

    finished_ok = Signal(object)

    def run(self) -> None:
        # update.check_for_update() already never raises - it returns an
        # UpdateInfo(status="error") instead - so there is nothing to catch here.
        self.finished_ok.emit(update.check_for_update())


class BenchmarkWorker(QThread):
    """Runs PresentMon and parses its output; blocks for the whole capture
    duration, so this has to be off the UI thread."""

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, exe: Path, process_name: str, output_csv: Path, duration_s: int,
                 pid: int | None = None) -> None:
        super().__init__()
        self.exe = exe
        self.process_name = process_name
        self.output_csv = output_csv
        self.duration_s = duration_s
        self.pid = pid

    def run(self) -> None:
        try:
            benchmark.run_capture(self.exe, self.process_name, self.output_csv, self.duration_s,
                                  pid=self.pid)
            stats = benchmark.parse_csv(self.output_csv)
        except benchmark.BenchmarkError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit(traceback.format_exc()[-1500:])
        else:
            self.finished_ok.emit(stats)


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
        self._settings_row_kind: list[tuple[str, str]] = []
        self._settings_collapsed: set[str] = set()
        self._settings_filter: str = ""
        self.cfg_overrides: dict = prefs.load_cfg_overrides()
        self._cfg_editors: dict[str, QWidget] = {}
        self._cfg_rows: dict[str, int] = {}
        self._cfg_row_kind: list[tuple[str, str]] = []
        self._cfg_collapsed: set[str] = set()
        self._cfg_filter: str = ""
        self.update_info: update.UpdateInfo | None = None
        self._busy_count = 0
        self._presentmon_path: Path | None = None
        self._has_persisted_target = False
        self._row_height_cache: int | None = None
        # The single source of truth for which preset backs the current
        # recommendation - NOT "whichever preset button is checked", because
        # a custom profile in use highlights no preset button at all (see
        # load_selected_profile) while still needing a concrete preset
        # underneath for the engine to compute anything. Read this, never
        # self.preset_group.checkedId() (which is -1, not a valid PRESETS
        # index, whenever a profile is active).
        self._active_preset = PRESETS[1]  # "competitive" - matches the default checked button
        self._loading = True

        self.setWindowTitle(f"{APP_NAME} {__version__} - Battlefield 6 configurator")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1080, 700)
        self._size_to_screen()

        self._build_menu_bar()

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(18, 16, 18, 12)
        outer.setSpacing(14)

        outer.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_sidebar(), 0)
        body.addWidget(self._build_main_panel(), 1)
        outer.addLayout(body, 1)

        outer.addLayout(self._build_action_bar())

        self.statusBar().showMessage(f"Database {db.version} loaded from {db.source}")
        self._loading = False
        self.redetect()
        self.check_for_updates(silent=True)

    def _size_to_screen(self) -> None:
        """Open at a size proportioned to the actual screen, not a fixed
        1320x880 that looks tiny on a 4K/ultrawide display and cramped on a
        laptop panel. Not fullscreen - a comfortable majority of the
        available desktop (excluding the taskbar), capped so the layout
        (designed around a ~390px fixed sidebar + a few hundred more of
        content) doesn't stretch absurdly wide on a 5120px ultrawide, and
        centered on screen.
        """
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        if available is None:
            self.resize(1320, 880)
            return
        width = min(max(int(available.width() * 0.8), 1080), 1600)
        height = min(max(int(available.height() * 0.85), 700), 1000)
        self.resize(width, height)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())

    # -- construction ------------------------------------------------------

    def _build_menu_bar(self) -> None:
        """A conventional menu bar: everything here already exists as a
        button somewhere - this adds keyboard shortcuts and discoverability
        on top, it does not replace the action bar."""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self._add_action(file_menu, "Save &profile as...", "Ctrl+Shift+S", self.save_profile_as)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Locate files...", "Ctrl+L", self.locate_files)
        self._add_action(file_menu, "Bac&k up now", "Ctrl+B", self.backup_now)
        self._add_action(file_menu, "&Restore...", "Ctrl+Shift+R", self.open_restore)
        file_menu.addSeparator()
        self._add_action(file_menu, "&Export report...", "Ctrl+E", self.export_report)
        self._add_action(file_menu, "&Save User.cfg only", "Ctrl+S", self.save_user_cfg)
        self._add_action(file_menu, "&Apply everything", "Ctrl+Return", self.apply_everything)
        file_menu.addSeparator()
        self._add_action(file_menu, "E&xit", "Ctrl+Q", self.close)

        view_menu = menubar.addMenu("&View")
        self._add_action(view_menu, "&Re-detect hardware", "F5", self.redetect)
        self._add_action(view_menu, "&Focus search", "Ctrl+F", self._focus_search)
        view_menu.addSeparator()
        self._add_action(view_menu, "Expand all settings categories", None,
                          lambda: self._set_all_settings_collapsed(False))
        self._add_action(view_menu, "Collapse all settings categories", None,
                          lambda: self._set_all_settings_collapsed(True))
        view_menu.addSeparator()
        self._add_action(view_menu, "Reset all in-game setting overrides", None,
                          self.reset_all_overrides)
        self._add_action(view_menu, "Reset all User.cfg overrides", None,
                          self.reset_all_cfg_overrides)

        help_menu = menubar.addMenu("&Help")
        self._add_action(help_menu, "&Check for updates...", None,
                          lambda: self.check_for_updates(silent=False))
        self._add_action(help_menu, "View project on GitHub", None, self._open_github)
        help_menu.addSeparator()
        self._add_action(help_menu, "&About BF6 Tuner...", None, self.show_about_dialog)

    @staticmethod
    def _add_action(menu, text: str, shortcut: str | None, slot) -> QAction:
        action = QAction(text, menu)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _focus_search(self) -> None:
        """Jumps to whichever tab's search box is relevant right now."""
        index = self.tabs.currentIndex()
        settings_index = self.tabs.indexOf(self._settings_tab_widget)
        cfg_index = self.tabs.indexOf(self._cfg_tab_widget)
        target = self.settings_search if index == settings_index else self.cfg_search if index == cfg_index else None
        if target is None:
            self.tabs.setCurrentIndex(settings_index)
            target = self.settings_search
        target.setFocus()
        target.selectAll()

    def _open_github(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(f"https://github.com/{update.REPO}"))

    def show_about_dialog(self) -> None:
        commit = update.local_commit()
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>Hardware-aware Battlefield 6 settings and User.cfg configurator.</p>"
            f"<p style='color:{theme.TEXT_DIM}'>Build commit: {commit[:7] or 'unknown'}<br>"
            f"Database {self.db.version} from {self.db.source}</p>"
            f"<p><a href='https://github.com/{update.REPO}'>github.com/{update.REPO}</a></p>",
        )

    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("HeaderBar")
        row = QHBoxLayout(frame)
        row.setContentsMargins(4, 0, 4, 14)

        icon_label = QLabel()
        icon_label.setPixmap(app_icon().pixmap(36, 36))
        row.addWidget(icon_label)

        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        subtitle = dim("Hardware-aware Battlefield 6 settings and User.cfg")
        # A fixed single line, not the wrapping dim() default - there is
        # always room for this short a subtitle, and wrapping it here just
        # looks like a layout glitch rather than a deliberate two-line title.
        subtitle.setWordWrap(False)
        stack = QVBoxLayout()
        stack.setSpacing(0)
        stack.addWidget(title)
        stack.addWidget(subtitle)
        row.addLayout(stack)
        row.addStretch(1)

        self.busy_indicator = QProgressBar()
        self.busy_indicator.setRange(0, 0)  # indeterminate - a marquee, not a percentage
        self.busy_indicator.setTextVisible(False)
        self.busy_indicator.setFixedWidth(90)
        self.busy_indicator.setFixedHeight(6)
        self.busy_indicator.setVisible(False)
        row.addWidget(self.busy_indicator)

        style = self.style()
        self.update_button = QPushButton("Update available")
        self.update_button.setObjectName("Primary")
        self.update_button.setIcon(style.standardIcon(QStyle.SP_ArrowUp))
        self.update_button.setToolTip("main has changes this build does not. Click to see what's new.")
        self.update_button.clicked.connect(lambda: self.show_update_dialog(force_check=False))
        self.update_button.setVisible(False)
        row.addWidget(self.update_button)

        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.clicked.connect(lambda: self.check_for_updates(silent=False))
        row.addWidget(self.check_updates_button)

        self.redetect_button = QPushButton("Re-detect hardware")
        self.redetect_button.setIcon(style.standardIcon(QStyle.SP_BrowserReload))
        self.redetect_button.clicked.connect(self.redetect)
        row.addWidget(self.redetect_button)
        return frame

    def _build_profiles_card(self) -> QFrame:
        profiles_card, profiles_layout = card("Profiles")
        self.profile_combo = _NoScrollComboBox()
        # Loading is an explicit button, not automatic on selection: Qt only
        # emits currentIndexChanged when the index actually changes, so
        # auto-apply-on-select would silently do nothing if you re-picked the
        # profile you'd already drifted away from - the one time you'd most
        # want it to reload. An explicit action also matches how "Update"
        # and "Delete" already work here, and how the rest of the app never
        # changes anything without a deliberate click.
        self.profile_combo.currentIndexChanged.connect(self._on_profile_combo_changed)
        profiles_layout.addWidget(self.profile_combo)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.load_profile_button = QPushButton("Load")
        self.load_profile_button.setEnabled(False)
        self.load_profile_button.clicked.connect(self.load_selected_profile)
        row.addWidget(self.load_profile_button)

        save_as_button = QPushButton("Save as...")
        save_as_button.clicked.connect(self.save_profile_as)
        row.addWidget(save_as_button)

        self.update_profile_button = QPushButton("Update")
        self.update_profile_button.setEnabled(False)
        self.update_profile_button.clicked.connect(self.update_current_profile)
        row.addWidget(self.update_profile_button)

        self.delete_profile_button = QPushButton("Delete")
        self.delete_profile_button.setEnabled(False)
        self.delete_profile_button.clicked.connect(self.delete_current_profile)
        row.addWidget(self.delete_profile_button)
        profiles_layout.addLayout(row)

        profiles_layout.addWidget(dim(
            "Bundles the preset, target and every override into one saved, "
            "switchable slot - beyond the 4 built-in presets. Pick one and "
            "press Load."
        ))

        self._reload_profile_combo()
        return profiles_card

    # -- named profiles -------------------------------------------------------
    # Deliberately no separate "currently loaded profile" state: Load/Update/
    # Delete all act on whatever the combo box currently shows, full stop.
    # That is easier to predict than tracking "loaded" separately from
    # "selected" - the two could otherwise silently drift apart.

    def _reload_profile_combo(self, select: str | None = None) -> None:
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        self.profile_combo.addItem("— none selected —")
        for name in sorted(prefs.load_profiles()):
            self.profile_combo.addItem(name)
        index = self.profile_combo.findText(select or "")
        self.profile_combo.setCurrentIndex(index if index >= 0 else 0)
        self.profile_combo.blockSignals(False)
        self._on_profile_combo_changed(self.profile_combo.currentIndex())

    def _selected_profile_name(self) -> str | None:
        index = self.profile_combo.currentIndex()
        return self.profile_combo.itemText(index) if index > 0 else None

    def _on_profile_combo_changed(self, index: int) -> None:
        has_selection = index > 0
        self.load_profile_button.setEnabled(has_selection)
        self.update_profile_button.setEnabled(has_selection)
        self.delete_profile_button.setEnabled(has_selection)

    def _collect_profile_data(self) -> dict:
        label = self.resolution_box.currentText()
        width, height = (int(part) for part in label.split("x", 1))
        data: dict = {
            "preset": self._active_preset,
            "width": width, "height": height,
            "refresh_hz": self.refresh_box.value(),
        }
        for key, box in self.checkboxes.items():
            data[key] = box.isChecked()
        data["setting_overrides"] = dict(self.setting_overrides)
        data["cfg_overrides"] = dict(self.cfg_overrides)
        return data

    def load_selected_profile(self) -> None:
        name = self._selected_profile_name()
        data = prefs.load_profiles().get(name) if name else None
        if name is None or data is None:
            return

        self._loading = True
        try:
            preset = data.get("preset")
            self._active_preset = preset if preset in PRESETS else PRESETS[1]
            self.preset_blurb.setText(PRESET_BLURB[self._active_preset])
            # A loaded profile is shown active via the combo selection above,
            # never via one of the 4 preset buttons - the two are mutually
            # exclusive "what's active" indicators (see _on_preset).
            checked_button = self.preset_group.checkedButton()
            if checked_button is not None:
                checked_button.setChecked(False)

            label = f"{data.get('width', 2560)}x{data.get('height', 1440)}"
            index = self.resolution_box.findText(label)
            if index < 0:
                self.resolution_box.addItem(label)
                index = self.resolution_box.count() - 1
            self.resolution_box.setCurrentIndex(index)
            self.refresh_box.setValue(int(data.get("refresh_hz", 144)))

            for key, box in self.checkboxes.items():
                if key in data:
                    box.setChecked(bool(data[key]))

            self.setting_overrides = dict(data.get("setting_overrides", {}))
            self.cfg_overrides = dict(data.get("cfg_overrides", {}))
            prefs.save_setting_overrides(self.setting_overrides)
            prefs.save_cfg_overrides(self.cfg_overrides)
        finally:
            self._loading = False

        self.statusBar().showMessage(f'Loaded profile "{name}".')
        self.refresh()

    def save_profile_as(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            self, "Save profile", "Name this profile:",
            text=self._selected_profile_name() or "",
        )
        name = name.strip()
        if not ok or not name:
            return
        if name in prefs.load_profiles():
            confirm = QMessageBox.question(
                self, "Overwrite profile",
                f'A profile named "{name}" already exists. Overwrite it?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
        prefs.save_profile(name, self._collect_profile_data())
        self._reload_profile_combo(select=name)
        # Now shown active via the combo, same as a Load - no preset button
        # should also look selected (see _on_preset / load_selected_profile).
        checked_button = self.preset_group.checkedButton()
        if checked_button is not None:
            checked_button.setChecked(False)
        self.statusBar().showMessage(f'Saved profile "{name}".')

    def update_current_profile(self) -> None:
        name = self._selected_profile_name()
        if name is None:
            return
        prefs.save_profile(name, self._collect_profile_data())
        self.statusBar().showMessage(f'Updated profile "{name}".')

    def delete_current_profile(self) -> None:
        name = self._selected_profile_name()
        if name is None:
            return
        confirm = QMessageBox.question(
            self, "Delete profile", f'Delete the profile "{name}"?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        prefs.delete_profile(name)
        self._reload_profile_combo()
        # No profile is competing for the "what's active" indicator anymore -
        # the underlying preset (unchanged by deleting the profile) can show
        # as selected again.
        button = self.preset_group.button(PRESETS.index(self._active_preset))
        if button is not None:
            button.setChecked(True)
        self.statusBar().showMessage(f'Deleted profile "{name}".')

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

        layout.addWidget(self._build_profiles_card())

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

        self._apply_persisted_target()

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
            ["Setting", "Value", "Impact", "", "Why"], [250, 200, 90, 155, -1]
        )
        # Tab-root widgets are all stored (not just passed straight to
        # addTab) so setTabText/setCurrentIndex calls elsewhere can look
        # up "whichever index this tab is at right now" via
        # self.tabs.indexOf(...) instead of a hardcoded position - a
        # hardcoded index silently points at the wrong tab the next time
        # a tab gets inserted or reordered (this happened for real: adding
        # the Key Bindings tab below silently relabelled it "Benchmark"
        # until this was fixed).
        self._settings_tab_widget = self._build_settings_tab()
        self.tabs.addTab(self._settings_tab_widget, "In-game settings")

        self.cfg_table = self._make_table(
            ["Command", "Value", "", "Why"], [260, 160, 155, -1]
        )
        self._cfg_tab_widget = self._build_cfg_tab()
        self.tabs.addTab(self._cfg_tab_widget, "User.cfg")

        self.warnings_area = self._make_scroll()
        self.tabs.addTab(self.warnings_area, "Warnings")

        self.checks_area = self._make_scroll()
        self.tabs.addTab(self.checks_area, "System checks")

        self.keybinds_area = self._make_scroll()
        self.tabs.addTab(self.keybinds_area, "Key Bindings")

        self._benchmark_tab_widget = self._build_benchmark_tab()
        self.tabs.addTab(self._benchmark_tab_widget, "Benchmark")
        self._fill_benchmark_results()

        layout.addWidget(self.tabs, 1)
        return panel

    # -- benchmark (PresentMon) ----------------------------------------------

    def _build_benchmark_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        setup_card, setup_layout = card("Capture")

        path_row = QHBoxLayout()
        self.presentmon_label = dim("PresentMon not located.")
        path_row.addWidget(self.presentmon_label, 1)
        locate_pm_button = QPushButton("Locate PresentMon...")
        locate_pm_button.clicked.connect(self.locate_presentmon)
        path_row.addWidget(locate_pm_button)
        open_folder_button = QPushButton("Open captures folder")
        open_folder_button.clicked.connect(self.open_benchmark_folder)
        path_row.addWidget(open_folder_button)
        setup_layout.addLayout(path_row)

        control_row = QHBoxLayout()
        control_row.addWidget(dim("Duration"))
        self.benchmark_duration = QSpinBox()
        self.benchmark_duration.setRange(10, 900)
        self.benchmark_duration.setSingleStep(10)
        self.benchmark_duration.setValue(60)
        self.benchmark_duration.setSuffix(" s")
        control_row.addWidget(self.benchmark_duration)
        control_row.addStretch(1)
        self.start_benchmark_button = QPushButton("Start Recording")
        self.start_benchmark_button.setObjectName("Primary")
        self.start_benchmark_button.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.start_benchmark_button.setEnabled(False)
        self.start_benchmark_button.clicked.connect(self.start_benchmark)
        control_row.addWidget(self.start_benchmark_button)
        setup_layout.addLayout(control_row)

        self.benchmark_progress = QProgressBar()
        self.benchmark_progress.setVisible(False)
        setup_layout.addWidget(self.benchmark_progress)

        setup_layout.addWidget(dim(
            "Captures real frame times via PresentMon while Battlefield 6 is running, then "
            "compares the measured average / 1% low / 0.1% low FPS against this app's "
            "prediction for your current settings - closing the loop the README talks about: "
            "the estimate is a model, this is a measurement. Every capture is saved "
            "automatically below. Needs PresentMon (github.com/GameTechDev/PresentMon) - "
            "never bundled or auto-downloaded, same policy as everything else this app fetches."
        ))
        layout.addWidget(setup_card)

        self.benchmark_results_area = self._make_scroll()
        layout.addWidget(self.benchmark_results_area, 1)

        self._refresh_presentmon_status()
        return container

    def _refresh_presentmon_status(self) -> None:
        saved = prefs.load().get("presentmon_exe")
        found = benchmark.find_presentmon(Path(saved) if saved else None)
        self._presentmon_path = found
        if found:
            self.presentmon_label.setText(f"PresentMon: {found}")
            self.presentmon_label.setStyleSheet("")
        else:
            self.presentmon_label.setText(
                "PresentMon not located - download it from github.com/GameTechDev/PresentMon "
                "and click Locate."
            )
            self.presentmon_label.setStyleSheet(f"color: {theme.WARN};")
        self.start_benchmark_button.setEnabled(found is not None)

    def locate_presentmon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate PresentMon", "", "Executable (*.exe);;All files (*)",
        )
        if not path:
            return
        prefs.set_override("presentmon_exe", path)
        self._refresh_presentmon_status()

    def open_benchmark_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(benchmark.benchmark_root())))

    def start_benchmark(self) -> None:
        if self.rec is None or self._presentmon_path is None:
            return
        if not paths.is_game_running():
            QMessageBox.warning(
                self, "Battlefield 6 is not running",
                "Start the game first - PresentMon needs a live process to capture frames from.",
            )
            return

        process_name = self.game.executable.name if self.game.executable else "bf6.exe"
        pid = paths.find_running_game_pid()
        duration = self.benchmark_duration.value()
        output_csv = benchmark.benchmark_root() / "_last_capture.csv"

        self.start_benchmark_button.setEnabled(False)
        self._busy_start()
        self.benchmark_progress.setVisible(True)
        self.benchmark_progress.setRange(0, duration)
        self.benchmark_progress.setValue(0)
        target_desc = f"PID {pid}" if pid else f"process name '{process_name}' - no PID found"
        self.statusBar().showMessage(f"Recording for {duration}s ({target_desc}) - play normally...")

        self._benchmark_elapsed = 0
        self._benchmark_timer = QTimer(self)
        self._benchmark_timer.setInterval(1000)
        self._benchmark_timer.timeout.connect(self._on_benchmark_tick)
        self._benchmark_timer.start()

        self._benchmark_worker = BenchmarkWorker(
            self._presentmon_path, process_name, output_csv, duration, pid=pid
        )
        self._benchmark_worker.finished_ok.connect(self._on_benchmark_finished)
        self._benchmark_worker.failed.connect(self._on_benchmark_failed)
        self._benchmark_worker.start()

    def _on_benchmark_tick(self) -> None:
        self._benchmark_elapsed += 1
        self.benchmark_progress.setValue(min(self._benchmark_elapsed, self.benchmark_progress.maximum()))

    def _end_benchmark_run(self) -> None:
        timer = getattr(self, "_benchmark_timer", None)
        if timer is not None:
            timer.stop()
        self.benchmark_progress.setVisible(False)
        self.start_benchmark_button.setEnabled(self._presentmon_path is not None)
        self._busy_stop()

    def _on_benchmark_finished(self, stats: benchmark.FrameStats) -> None:
        self._end_benchmark_run()
        rec = self.rec
        profile = self.profile
        recording = benchmark.Recording(
            stats=stats,
            taken_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            preset=rec.target.preset, predicted_fps=rec.predicted_fps,
            gpu_fps=rec.gpu_fps, cpu_fps=rec.cpu_fps, bottleneck=rec.bottleneck,
            resolution=f"{rec.target.width}x{rec.target.height}", refresh_hz=rec.target.refresh_hz,
            cpu_name=profile.cpu_name, gpu_name=profile.gpu_name,
        )
        benchmark.save_recording(recording)
        self._fill_benchmark_results()
        self.statusBar().showMessage(
            f"Captured {stats.sample_count} frames - measured {stats.avg_fps:.0f} FPS "
            f"vs predicted {rec.predicted_fps} FPS."
        )

    def _on_benchmark_failed(self, message: str) -> None:
        self._end_benchmark_run()
        QMessageBox.warning(self, "Recording failed", message)

    def _fill_benchmark_results(self) -> None:
        container = self.benchmark_results_area.widget()
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        recordings = benchmark.list_recordings()
        if not recordings:
            frame, inner = card("")
            inner.addWidget(dim(
                "No recordings yet. Locate PresentMon, start the game, and hit Start Recording."
            ))
            layout.addWidget(frame)
        for path, recording in recordings:
            layout.addWidget(self._benchmark_card(path, recording))
        layout.addStretch(1)

        count = len(recordings)
        self.tabs.setTabText(
            self.tabs.indexOf(self._benchmark_tab_widget),
            f"Benchmark ({count})" if count else "Benchmark",
        )

    def _benchmark_card(self, path: Path, recording: benchmark.Recording) -> QWidget:
        frame, inner = card("")
        stats = recording.stats
        delta = recording.delta_fps
        delta_colour = theme.OK if delta >= 0 else (theme.BAD if delta < -10 else theme.WARN)

        heading = QLabel(
            f"<b>{recording.taken_at.replace('T', ' ')[:19]}</b>"
            f"&nbsp;&nbsp;<span style='color:{theme.TEXT_DIM}'>"
            f"{recording.preset} · {recording.resolution} @ {recording.refresh_hz} Hz · "
            f"{recording.gpu_name}</span>"
        )
        heading.setWordWrap(True)
        inner.addWidget(heading)

        summary = QLabel(
            f"Measured <b>{stats.avg_fps:.0f} FPS</b> average"
            + (f", <b>{stats.low_1pct_fps:.0f}</b> 1% low" if stats.low_1pct_fps else "")
            + (f", <b>{stats.low_01pct_fps:.0f}</b> 0.1% low" if stats.low_01pct_fps else "")
            + f" over {stats.sample_count} frames / {stats.duration_s:.0f}s"
            f"&nbsp;&nbsp;<span style='color:{theme.TEXT_DIM}'>vs predicted "
            f"{recording.predicted_fps} FPS ({recording.bottleneck}-limited)</span>"
            f"&nbsp;&nbsp;<span style='color:{delta_colour}; font-weight:700'>{delta:+d}</span>"
        )
        summary.setWordWrap(True)
        inner.addWidget(summary)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        delete_button = QPushButton("Delete")
        delete_button.setObjectName("TableButton")
        delete_button.clicked.connect(lambda _=False, p=path: self.delete_benchmark_recording(p))
        button_row.addWidget(delete_button)
        inner.addLayout(button_row)
        return frame

    def delete_benchmark_recording(self, path: Path) -> None:
        confirm = QMessageBox.question(
            self, "Delete recording", "Delete this recorded capture?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        benchmark.delete_recording(path)
        self._fill_benchmark_results()


    def _build_table_nav_row(
        self, *, search_slot, jump_slot, expand_slot, collapse_slot,
        search_attr: str, jump_attr: str, placeholder: str,
    ) -> QHBoxLayout:
        """Search box + category jump combo + expand/collapse, shared by the
        settings and User.cfg tabs so both scale the same way as the tables grow."""
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        row.setSpacing(8)

        search = QLineEdit()
        search.setPlaceholderText(placeholder)
        search.setClearButtonEnabled(True)
        search.textChanged.connect(search_slot)
        setattr(self, search_attr, search)
        row.addWidget(search, 1)

        jump = _NoScrollComboBox()
        jump.addItem("Jump to category...")
        jump.currentIndexChanged.connect(jump_slot)
        setattr(self, jump_attr, jump)
        row.addWidget(jump)

        expand_button = QPushButton("Expand all")
        expand_button.clicked.connect(expand_slot)
        row.addWidget(expand_button)

        collapse_button = QPushButton("Collapse all")
        collapse_button.clicked.connect(collapse_slot)
        row.addWidget(collapse_button)
        return row

    @staticmethod
    def _show_no_matches_hint(note: QLabel, default_text: str, filter_text: str, any_match: bool) -> None:
        """Search feedback: say so plainly when a query matches nothing,
        rather than leaving the user looking at an all-hidden, blank table."""
        if filter_text and not any_match:
            note.setText(f"No matches for “{filter_text}”. Clear the search to see everything.")
            note.setStyleSheet(f"color: {theme.WARN};")
        else:
            note.setText(default_text)
            note.setStyleSheet("")

    def _build_settings_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        self._settings_note_default = (
            "Every value here is editable. Change one and the prediction, the frame cap "
            "and the comparison all update to match. Click a category to collapse it."
        )
        self.override_note = dim(self._settings_note_default)
        row.addWidget(self.override_note, 1)

        impact_legend = QLabel(
            "Impact:&nbsp;"
            f"<span style='color:{theme.GPU_COLOUR}; font-weight:600'>GPU</span>&nbsp;&nbsp;"
            f"<span style='color:{theme.CPU_COLOUR}; font-weight:600'>CPU</span>&nbsp;&nbsp;"
            f"<span style='color:{theme.VRAM_COLOUR}; font-weight:600'>VRAM</span>"
        )
        impact_legend.setObjectName("Dim")
        impact_legend.setToolTip(
            "Each row's Impact column is tagged with the resource(s) it meaningfully "
            "costs, colour-matched to this legend - a quick way to spot which settings "
            "are worth lowering first on a CPU-limited or GPU-limited machine."
        )
        row.addWidget(impact_legend)

        self.reset_overrides_button = QPushButton("Reset all to recommended")
        self.reset_overrides_button.clicked.connect(self.reset_all_overrides)
        row.addWidget(self.reset_overrides_button)
        layout.addLayout(row)

        layout.addLayout(self._build_table_nav_row(
            search_slot=self._on_settings_search_changed,
            jump_slot=self._on_settings_jump,
            expand_slot=lambda: self._set_all_settings_collapsed(False),
            collapse_slot=lambda: self._set_all_settings_collapsed(True),
            search_attr="settings_search", jump_attr="settings_jump",
            placeholder="Search settings by name or category...",
        ))

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

    def _build_cfg_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(8)

        sub_tabs = QTabWidget()

        commands_tab = QWidget()
        commands_layout = QVBoxLayout(commands_tab)
        commands_layout.setContentsMargins(0, 8, 0, 0)
        commands_layout.setSpacing(8)

        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 6, 0)
        self._cfg_note_default = (
            "Every line the app decides to write is editable here too. Unlike the in-game "
            "settings, these are on/off policy calls, not a modelled frame-time cost - "
            "overriding one is reflected in the file and the comparison, but will not move "
            "the predicted FPS above. Click a row for the full explanation, pros, cons, risk "
            "and confidence. The frame cap is set from the In-game settings tab instead, so "
            "the two never disagree."
        )
        self.cfg_override_note = dim(self._cfg_note_default)
        row.addWidget(self.cfg_override_note, 1)

        self.reset_cfg_overrides_button = QPushButton("Reset all to recommended")
        self.reset_cfg_overrides_button.clicked.connect(self.reset_all_cfg_overrides)
        row.addWidget(self.reset_cfg_overrides_button)
        commands_layout.addLayout(row)

        commands_layout.addLayout(self._build_table_nav_row(
            search_slot=self._on_cfg_search_changed,
            jump_slot=self._on_cfg_jump,
            expand_slot=lambda: self._set_all_cfg_collapsed(False),
            collapse_slot=lambda: self._set_all_cfg_collapsed(True),
            search_attr="cfg_search", jump_attr="cfg_jump",
            placeholder="Search commands by key or category...",
        ))

        splitter = QSplitter(Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.cfg_table)

        self.cfg_detail = QTextEdit()
        self.cfg_detail.setReadOnly(True)
        self.cfg_detail.setObjectName("SettingDetail")
        self.cfg_detail.setMinimumHeight(80)
        self.cfg_detail.setMaximumHeight(200)
        self.cfg_detail.setPlaceholderText(
            "Click any row to see the full explanation, pros, cons, risk and confidence."
        )
        splitter.addWidget(self.cfg_detail)
        splitter.setSizes([9999, 150])
        commands_layout.addWidget(splitter, 1)

        sub_tabs.addTab(commands_tab, "Commands")

        self.cfg_view = QPlainTextEdit()
        self.cfg_view.setReadOnly(True)
        self.cfg_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        sub_tabs.addTab(self.cfg_view, "Raw file preview")

        layout.addWidget(sub_tabs, 1)
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
        so rows - including category header rows - are laid out on the first
        pass; later calls only update values and row visibility (collapse/search).
        """
        rec = self.rec
        assert rec is not None
        table = self.settings_table
        building = table.rowCount() == 0

        if building:
            groups: dict[str, list] = {}
            for choice in rec.settings:
                groups.setdefault(choice.menu, []).append(choice)

            row_kind: list[tuple[str, str]] = []
            for menu, choices in groups.items():
                row_kind.append(("header", menu))
                for choice in choices:
                    row_kind.append(("choice", choice.setting_id))
            self._settings_row_kind = row_kind

            table.setRowCount(len(row_kind))
            table.currentCellChanged.connect(self._on_setting_row_changed)
            table.cellClicked.connect(self._on_settings_cell_clicked)

            self.settings_jump.blockSignals(True)
            self.settings_jump.clear()
            self.settings_jump.addItem("Jump to category...")
            for menu in groups:
                self.settings_jump.addItem(menu)
            self.settings_jump.blockSignals(False)

            for row, (kind, key) in enumerate(row_kind):
                if kind == "header":
                    self._make_header_row(table, row, span=5)
                    continue

                choice = next(c for c in rec.settings if c.setting_id == key)
                setting = self.db.setting(choice.setting_id) or {}
                name = QTableWidgetItem(choice.label)
                name.setToolTip(choice.menu)
                table.setItem(row, 0, name)

                editor = self._make_editor(choice, setting)
                if editor is not None:
                    self._setting_editors[choice.setting_id] = editor
                    table.setCellWidget(row, 1, editor)
                else:
                    table.setItem(row, 1, QTableWidgetItem(""))

                impact_label = QLabel(self._impact_badges_html(setting))
                impact_label.setTextFormat(Qt.RichText)
                impact_label.setToolTip(self._impact_tooltip(setting))
                impact_label.setAlignment(Qt.AlignCenter)
                table.setCellWidget(row, 2, impact_label)

                if editor is not None:
                    reset = QPushButton("Reset")
                    reset.setObjectName("TableButton")
                    reset.setFlat(True)
                    reset.clicked.connect(
                        lambda _=False, sid=choice.setting_id: self.reset_override(sid)
                    )
                    table.setCellWidget(row, 3, reset)

                table.setItem(row, 4, QTableWidgetItem(""))

        choice_by_id = {c.setting_id: c for c in rec.settings}

        for row, (kind, key) in enumerate(self._settings_row_kind):
            if kind == "header":
                continue
            choice = choice_by_id.get(key)
            if choice is None:
                continue
            self._setting_rows[choice.setting_id] = row
            setting = self.db.setting(choice.setting_id) or {}
            unverified = setting.get("confidence") == "unverified"

            name_item = table.item(row, 0)
            if name_item is not None:
                label = choice.label + (" (unverified)" if unverified else "")
                name_item.setText(("◆ " if choice.overridden else "") + label)
                name_item.setToolTip(
                    choice.menu + ("\nUnverified: a plausible addition, not confirmed against "
                                   "a real BF6 profile - see the detail pane." if unverified else "")
                )
                if choice.overridden:
                    font = QFont()
                    font.setBold(True)
                    name_item.setFont(font)
                    name_item.setForeground(_OVERRIDE_FG)
                    name_item.setBackground(_OVERRIDE_BG)
                else:
                    name_item.setFont(QFont())
                    name_item.setForeground(QColor(theme.TEXT_DIM) if unverified else _NORMAL_FG)
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

            reset_button = table.cellWidget(row, 3)
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

            why = table.item(row, 4)
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
        self.tabs.setTabText(
            self.tabs.indexOf(self._settings_tab_widget),
            f"In-game settings ({count} changed)" if count else "In-game settings",
        )
        self._update_settings_header_texts()
        self._apply_settings_filter()

    def _make_header_row(self, table: QTableWidget, row: int, span: int) -> None:
        """A full-width, clickable category row. Click toggles collapse via
        the table's cellClicked handler; text/arrow is set separately since it
        depends on collapse state and per-category counts, which change."""
        item = QTableWidgetItem("")
        item.setFlags(Qt.ItemIsEnabled)
        font = QFont()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor(theme.TEXT))
        item.setBackground(QColor(theme.BG_RAISED))
        table.setItem(row, 0, item)
        table.setSpan(row, 0, 1, span)
        table.setRowHeight(row, self._editor_row_height())

    # -- category collapse / search (in-game settings) ----------------------

    def _update_settings_header_texts(self) -> None:
        if self.rec is None:
            return
        counts: dict[str, int] = {}
        for choice in self.rec.settings:
            counts[choice.menu] = counts.get(choice.menu, 0) + 1
        table = self.settings_table
        for row, (kind, key) in enumerate(self._settings_row_kind):
            if kind != "header":
                continue
            item = table.item(row, 0)
            if item is None:
                continue
            arrow = "▸" if key in self._settings_collapsed else "▾"
            item.setText(f"{arrow}  {key}  ({counts.get(key, 0)})")

    def _apply_settings_filter(self) -> None:
        table = self.settings_table
        text = self._settings_filter.strip().lower()
        choice_by_id = {c.setting_id: c for c in (self.rec.settings if self.rec else [])}

        group_has_match: dict[str, bool] = {}
        row_matches: dict[int, bool] = {}
        current_group = ""
        for row, (kind, key) in enumerate(self._settings_row_kind):
            if kind == "header":
                current_group = key
                group_has_match.setdefault(current_group, False)
                continue
            choice = choice_by_id.get(key)
            haystack = f"{choice.label} {current_group}".lower() if choice else key.lower()
            is_match = not text or text in haystack
            row_matches[row] = is_match
            if is_match:
                group_has_match[current_group] = True

        current_group = ""
        for row, (kind, key) in enumerate(self._settings_row_kind):
            if kind == "header":
                current_group = key
                table.setRowHidden(row, bool(text) and not group_has_match.get(current_group, False))
                continue
            collapsed = not text and current_group in self._settings_collapsed
            table.setRowHidden(row, collapsed or not row_matches.get(row, True))

        self._show_no_matches_hint(
            self.override_note, self._settings_note_default,
            text, any(group_has_match.values()),
        )

    def _on_settings_search_changed(self, text: str) -> None:
        self._settings_filter = text
        self._apply_settings_filter()

    def _on_settings_cell_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._settings_row_kind):
            return
        kind, key = self._settings_row_kind[row]
        if kind != "header":
            return
        if key in self._settings_collapsed:
            self._settings_collapsed.discard(key)
        else:
            self._settings_collapsed.add(key)
        self._update_settings_header_texts()
        self._apply_settings_filter()

    def _set_all_settings_collapsed(self, collapsed: bool) -> None:
        menus = {key for kind, key in self._settings_row_kind if kind == "header"}
        if collapsed:
            self._settings_collapsed |= menus
        else:
            self._settings_collapsed -= menus
        self._update_settings_header_texts()
        self._apply_settings_filter()

    def _on_settings_jump(self, index: int) -> None:
        if index <= 0:
            return
        menu = self.settings_jump.itemText(index)
        self._reveal_settings_group(menu)
        self.settings_jump.setCurrentIndex(0)

    def _reveal_settings_group(self, menu: str) -> None:
        self._settings_collapsed.discard(menu)
        self._update_settings_header_texts()
        self._apply_settings_filter()
        table = self.settings_table
        for row, (kind, key) in enumerate(self._settings_row_kind):
            if kind == "header" and key == menu:
                item = table.item(row, 0)
                if item is not None:
                    table.scrollToItem(item)
                break

    def _reveal_settings_row(self, setting_id: str) -> None:
        row = self._setting_rows.get(setting_id)
        if row is None or row >= len(self._settings_row_kind):
            return
        group = ""
        for kind, key in reversed(self._settings_row_kind[: row + 1]):
            if kind == "header":
                group = key
                break
        if group:
            self._settings_collapsed.discard(group)
        self._update_settings_header_texts()
        if self._settings_filter:
            self.settings_search.clear()  # triggers _apply_settings_filter itself
        else:
            self._apply_settings_filter()
        table = self.settings_table
        table.setCurrentCell(row, 0)
        item = table.item(row, 0)
        if item is not None:
            table.scrollToItem(item)

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

    # -- User.cfg table and overrides ---------------------------------------

    def _make_cfg_editor(self, line, command: dict) -> QWidget | None:
        """An editor matched to the command's declared type, or None for the
        frame-cap line, which is owned by the 'Frame limit' in-game setting."""
        if line.key in LINKED_CFG_KEYS:
            return None

        kind = command.get("type")
        if kind == "bool":
            box = _NoScrollComboBox()
            box.addItem("Off", 0)
            box.addItem("On", 1)
            box.currentIndexChanged.connect(
                lambda _=0, key=line.key: self._on_cfg_editor_changed(key)
            )
            return box

        if kind == "int":
            spin = _NoScrollSpinBox()
            spin.setRange(int(command.get("min", 0)), int(command.get("max", 64)))
            spin.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(
                lambda _=0, key=line.key: self._on_cfg_editor_changed(key)
            )
            return spin

        if kind == "float":
            spin = QDoubleSpinBox()
            spin.setRange(float(command.get("min", 0.0)), float(command.get("max", 2.0)))
            spin.setSingleStep(0.05)
            spin.setDecimals(2)
            spin.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(
                lambda _=0.0, key=line.key: self._on_cfg_editor_changed(key)
            )
            return spin

        return None

    def _sync_cfg_table(self) -> None:
        """Same build-once-then-update-values, grouped-by-category approach as
        _sync_settings_table (see its docstring)."""
        rec = self.rec
        assert rec is not None
        table = self.cfg_table
        building = table.rowCount() == 0

        lines_by_key = {line.key: line for line in rec.cfg if line.key is not None}

        if building:
            groups: dict[str, list] = {}
            for line in lines_by_key.values():
                command = self.db.command(line.key) or {}
                group = command.get("group", "misc")
                groups.setdefault(group, []).append(line)

            row_kind: list[tuple[str, str]] = []
            for group, lines in groups.items():
                row_kind.append(("header", group))
                for line in lines:
                    row_kind.append(("line", line.key))
            self._cfg_row_kind = row_kind

            table.setRowCount(len(row_kind))
            table.currentCellChanged.connect(self._on_cfg_row_changed)
            table.cellClicked.connect(self._on_cfg_cell_clicked)

            self.cfg_jump.blockSignals(True)
            self.cfg_jump.clear()
            self.cfg_jump.addItem("Jump to category...")
            for group in groups:
                self.cfg_jump.addItem(CFG_GROUP_TITLES.get(group, group))
            self.cfg_jump.blockSignals(False)

            for row, (kind, key) in enumerate(row_kind):
                if kind == "header":
                    self._make_header_row(table, row, span=4)
                    continue

                line = lines_by_key[key]
                command = self.db.command(line.key) or {}
                name = QTableWidgetItem(line.key)
                name.setToolTip(command.get("summary", ""))
                table.setItem(row, 0, name)

                editor = self._make_cfg_editor(line, command)
                if editor is not None:
                    self._cfg_editors[line.key] = editor
                    table.setCellWidget(row, 1, editor)
                else:
                    table.setItem(row, 1, QTableWidgetItem(""))

                if line.key in LINKED_CFG_KEYS:
                    linked = QPushButton("Frame limit ->")
                    linked.setObjectName("TableButton")
                    linked.setFlat(True)
                    linked.setToolTip(
                        "Set from the In-game settings tab, so the file and the prediction "
                        "never disagree about the cap."
                    )
                    linked.clicked.connect(self._goto_frame_limit)
                    table.setCellWidget(row, 2, linked)
                elif editor is not None:
                    reset = QPushButton("Reset")
                    reset.setObjectName("TableButton")
                    reset.setFlat(True)
                    reset.clicked.connect(
                        lambda _=False, key=line.key: self.reset_cfg_override(key)
                    )
                    table.setCellWidget(row, 2, reset)

                table.setItem(row, 3, QTableWidgetItem(""))

        for row, (kind, key) in enumerate(self._cfg_row_kind):
            if kind == "header":
                continue
            line = lines_by_key.get(key)
            if line is None:
                continue
            command = self.db.command(line.key) or {}
            self._cfg_rows[line.key] = row

            name_item = table.item(row, 0)
            if name_item is not None:
                name_item.setText(("◆ " if line.overridden else "") + line.key)
                if line.overridden:
                    font = QFont()
                    font.setBold(True)
                    name_item.setFont(font)
                    name_item.setForeground(_OVERRIDE_FG)
                    name_item.setBackground(_OVERRIDE_BG)
                else:
                    name_item.setFont(QFont())
                    name_item.setForeground(_NORMAL_FG)
                    name_item.setBackground(QColor())

            editor = self._cfg_editors.get(line.key)
            if editor is not None:
                editor.blockSignals(True)
                if isinstance(editor, QComboBox):
                    index = editor.findData(int(line.value))
                    if index >= 0:
                        editor.setCurrentIndex(index)
                elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                    try:
                        editor.setValue(float(line.value) if isinstance(editor, QDoubleSpinBox)
                                         else int(round(float(line.value))))
                    except (TypeError, ValueError):
                        pass
                editor.blockSignals(False)
                if line.overridden:
                    editor.setStyleSheet(
                        f"background-color: {_OVERRIDE_BG.name()};"
                        f" border: 2px solid {_OVERRIDE_EDGE};"
                        f" border-radius: 6px;"
                        f" color: {_OVERRIDE_EDGE};"
                        f" font-weight: 600;"
                    )
                    editor.setToolTip(
                        f"You set this to {line.value}.\nEngine recommended: {line.recommended_value}"
                    )
                else:
                    editor.setStyleSheet("")
                    editor.setToolTip("")
            elif line.key in LINKED_CFG_KEYS:
                cell = table.item(row, 1)
                if cell is not None:
                    cell.setText(str(line.value))
                    cell.setForeground(Qt.gray)

            reset_button = table.cellWidget(row, 2)
            if isinstance(reset_button, QPushButton) and line.key not in LINKED_CFG_KEYS:
                reset_button.setEnabled(line.overridden)
                if line.overridden:
                    reset_button.setText(f"↩ {line.recommended_value}")
                    reset_button.setToolTip(
                        f"Click to restore the recommended value: {line.recommended_value}"
                    )
                else:
                    reset_button.setText("Reset")
                    reset_button.setToolTip("Already matches the recommendation")

            why = table.item(row, 3)
            if why is not None:
                summary = command.get("summary", line.comment)
                if line.overridden:
                    why_text = f"was: {line.recommended_value}    {summary}"
                    why.setForeground(_OVERRIDE_FG)
                else:
                    why_text = summary
                    why.setForeground(QColor(theme.TEXT_DIM))
                why.setText(why_text)
                why.setToolTip(why_text)

        count = len(rec.cfg_overrides)
        self.reset_cfg_overrides_button.setEnabled(bool(count))
        self.tabs.setTabText(
            self.tabs.indexOf(self._cfg_tab_widget),
            f"User.cfg ({count} changed)" if count else "User.cfg",
        )
        self._update_cfg_header_texts()
        self._apply_cfg_filter()

    # -- category collapse / search (User.cfg) -------------------------------

    def _update_cfg_header_texts(self) -> None:
        if self.rec is None:
            return
        counts: dict[str, int] = {}
        for line in self.rec.cfg:
            if line.key is None:
                continue
            command = self.db.command(line.key) or {}
            group = command.get("group", "misc")
            counts[group] = counts.get(group, 0) + 1
        table = self.cfg_table
        for row, (kind, key) in enumerate(self._cfg_row_kind):
            if kind != "header":
                continue
            item = table.item(row, 0)
            if item is None:
                continue
            arrow = "▸" if key in self._cfg_collapsed else "▾"
            title = CFG_GROUP_TITLES.get(key, key)
            item.setText(f"{arrow}  {title}  ({counts.get(key, 0)})")

    def _apply_cfg_filter(self) -> None:
        table = self.cfg_table
        text = self._cfg_filter.strip().lower()
        lines_by_key = {line.key: line for line in (self.rec.cfg if self.rec else []) if line.key}

        group_has_match: dict[str, bool] = {}
        row_matches: dict[int, bool] = {}
        current_group = ""
        for row, (kind, key) in enumerate(self._cfg_row_kind):
            if kind == "header":
                current_group = key
                group_has_match.setdefault(current_group, False)
                continue
            title = CFG_GROUP_TITLES.get(current_group, current_group)
            haystack = f"{key} {title}".lower()
            is_match = not text or text in haystack
            row_matches[row] = is_match
            if is_match:
                group_has_match[current_group] = True

        current_group = ""
        for row, (kind, key) in enumerate(self._cfg_row_kind):
            if kind == "header":
                current_group = key
                table.setRowHidden(row, bool(text) and not group_has_match.get(current_group, False))
                continue
            collapsed = not text and current_group in self._cfg_collapsed
            table.setRowHidden(row, collapsed or not row_matches.get(row, True))

        self._show_no_matches_hint(
            self.cfg_override_note, self._cfg_note_default,
            text, any(group_has_match.values()),
        )

    def _on_cfg_search_changed(self, text: str) -> None:
        self._cfg_filter = text
        self._apply_cfg_filter()

    def _on_cfg_cell_clicked(self, row: int, _col: int) -> None:
        if row < 0 or row >= len(self._cfg_row_kind):
            return
        kind, key = self._cfg_row_kind[row]
        if kind != "header":
            return
        if key in self._cfg_collapsed:
            self._cfg_collapsed.discard(key)
        else:
            self._cfg_collapsed.add(key)
        self._update_cfg_header_texts()
        self._apply_cfg_filter()

    def _set_all_cfg_collapsed(self, collapsed: bool) -> None:
        groups = {key for kind, key in self._cfg_row_kind if kind == "header"}
        if collapsed:
            self._cfg_collapsed |= groups
        else:
            self._cfg_collapsed -= groups
        self._update_cfg_header_texts()
        self._apply_cfg_filter()

    def _on_cfg_jump(self, index: int) -> None:
        if index <= 0:
            return
        title = self.cfg_jump.itemText(index)
        group = next((g for g in CFG_GROUP_TITLES if CFG_GROUP_TITLES.get(g, g) == title), title)
        self._cfg_collapsed.discard(group)
        self._update_cfg_header_texts()
        self._apply_cfg_filter()
        table = self.cfg_table
        for row, (kind, key) in enumerate(self._cfg_row_kind):
            if kind == "header" and key == group:
                item = table.item(row, 0)
                if item is not None:
                    table.scrollToItem(item)
                break
        self.cfg_jump.setCurrentIndex(0)

    def _goto_frame_limit(self) -> None:
        self.tabs.setCurrentIndex(self.tabs.indexOf(self._settings_tab_widget))
        self._reveal_settings_row("frame_limit")

    def _on_cfg_editor_changed(self, key: str) -> None:
        if self._loading:
            return
        editor = self._cfg_editors.get(key)
        if editor is None:
            return
        value = editor.currentData() if isinstance(editor, QComboBox) else editor.value()

        line = next((c for c in (self.rec.cfg if self.rec else []) if c.key == key), None)
        if line is not None and not line.overridden and value == line.value:
            return
        if line is not None and line.overridden and value == line.recommended_value:
            self.reset_cfg_override(key)
            return

        self.cfg_overrides[key] = value
        prefs.set_cfg_override(key, value)
        self.refresh()

    def reset_cfg_override(self, key: str) -> None:
        self.cfg_overrides.pop(key, None)
        prefs.set_cfg_override(key, None)
        self.refresh()

    def reset_all_cfg_overrides(self) -> None:
        if not self.cfg_overrides:
            return
        count = len(self.cfg_overrides)
        confirm = QMessageBox.question(
            self, "Reset User.cfg",
            f"Drop {count} User.cfg line(s) you changed and go back to the recommendation?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if confirm != QMessageBox.Yes:
            return
        self.cfg_overrides.clear()
        prefs.clear_cfg_overrides()
        self.refresh()

    def _on_cfg_row_changed(self, row: int, _col: int, _prev_row: int, _prev_col: int) -> None:
        if self.rec is None or row < 0 or row >= len(self._cfg_row_kind):
            self.cfg_detail.clear()
            return
        kind, key = self._cfg_row_kind[row]
        if kind != "line":
            self.cfg_detail.clear()
            return
        line = next((l for l in self.rec.cfg if l.key == key), None)
        if line is None:
            self.cfg_detail.clear()
            return
        command = self.db.command(line.key)
        self._update_cfg_detail(line, command)

    def _update_cfg_detail(self, line, command: dict | None) -> None:
        if command is None:
            self.cfg_detail.setPlainText(line.comment)
            return

        confidence = command.get("confidence", "")
        risk = command.get("risk", "")
        group = command.get("group", "").replace("_", " ")

        parts: list[str] = []
        parts.append(
            f"<b style='font-size:14px; font-family:{theme.MONO}'>{line.key}</b>"
            f"&nbsp;&nbsp;<span style='color:{theme.TEXT_DIM}'>{group}</span>"
        )

        badge_colour = {"legacy": theme.TEXT_DIM, "community": theme.INFO,
                        "documented": theme.OK}.get(confidence, theme.TEXT_DIM)
        risk_colour = {"safe": theme.OK, "moderate": theme.INFO, "high": theme.WARN,
                       "unsafe": theme.BAD}.get(risk, theme.TEXT_DIM)
        parts.append(
            f"<br><span style='color:{theme.TEXT_DIM}; font-size:11px'>"
            f"<span style='color:{badge_colour}'>{confidence or 'unknown'}</span> confidence"
            f" &middot; <span style='color:{risk_colour}'>{risk or 'unknown'}</span> risk</span>"
        )

        detail = command.get("detail") or command.get("summary", "")
        if detail:
            parts.append(f"<br><br>{detail}")

        pros = command.get("pros", [])
        cons = command.get("cons", [])
        if pros or cons:
            parts.append(
                f"<br><br><span style='color:{theme.INFO}; font-weight:600'>"
                "Applying the value this app writes</span>"
            )
            for p in pros:
                parts.append(f"<br><span style='color:{theme.OK}'>+</span>&nbsp;{p}")
            for c in cons:
                parts.append(f"<br><span style='color:{theme.WARN}'>−</span>&nbsp;{c}")

        if line.overridden:
            parts.append(
                f"<br><br><span style='color:{theme.WARN}'>You have overridden this to "
                f"{line.value}. The engine recommended {line.recommended_value}. This does not "
                "move the predicted FPS above - measure it with the in-game frame time graph.</span>"
            )

        self.cfg_detail.setHtml("".join(parts))

    # -- setting detail pane -----------------------------------------------

    def _on_setting_row_changed(
        self, row: int, _col: int, _prev_row: int, _prev_col: int
    ) -> None:
        if self.rec is None or row < 0 or row >= len(self._settings_row_kind):
            self.setting_detail.clear()
            return
        kind, key = self._settings_row_kind[row]
        if kind != "choice":
            self.setting_detail.clear()
            return
        choice = next((c for c in self.rec.settings if c.setting_id == key), None)
        if choice is None:
            self.setting_detail.clear()
            return
        setting = self.db.setting(choice.setting_id)
        self._update_setting_detail(choice, setting)

    @staticmethod
    def _impact_badges_html(setting: dict) -> str:
        """Small coloured tags for the settings table's Impact column - one
        per hardware resource this setting meaningfully costs, so a glance
        down the column shows which rows are GPU-heavy, CPU-heavy, or eating
        VRAM without opening the detail pane for each one."""
        breakdown = _impact_breakdown(setting)
        if not breakdown:
            return ""
        return "&nbsp;".join(
            f"<span style='color:{theme.RESOURCE_COLOUR[resource]}; font-weight:600;"
            f" font-size:10px'>{resource.upper()}</span>"
            for resource, _cost in breakdown
        )

    @staticmethod
    def _impact_tooltip(setting: dict) -> str:
        breakdown = _impact_breakdown(setting)
        if not breakdown:
            return "No meaningful GPU, CPU, or VRAM cost."
        return "Performance impact — " + " · ".join(
            f"{resource.upper()}: {_cost_label(cost)}" for resource, cost in breakdown
        )

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

        parts: list[str] = []
        parts.append(
            f"<b style='font-size:14px'>{choice.label}</b>"
            f"&nbsp;&nbsp;<span style='color:{theme.TEXT_DIM}'>{menu}</span>"
        )
        if setting.get("confidence") == "unverified":
            parts.append(
                f"<br><span style='color:{theme.WARN}; font-size:11px'>UNVERIFIED —</span>"
                f"<span style='color:{theme.TEXT_DIM}; font-size:11px'> a plausible addition "
                "based on common Frostbite/FPS conventions, not confirmed against a real BF6 "
                "profile or menu. It has no profile key on purpose, so it is never written "
                "automatically - set it by hand and treat the value below as a starting "
                "point.</span>"
            )

        cost_bits = [
            f"<span style='color:{theme.RESOURCE_COLOUR[resource]}'>"
            f"{resource.upper()}</span>: {_cost_label(cost)}"
            for resource, cost in _impact_breakdown(setting)
        ]
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

    def _editor_row_height(self) -> int:
        """The row height every table with combo/spin-box cell editors
        shares, measured from real widget metrics on this machine rather
        than a hand-picked constant.

        Font size and DPI scaling vary enough across machines that a
        constant tuned by eye on one screen has repeatedly turned out too
        tight on another - this is at least the fourth round of exactly
        this bug (see ARCHITECTURE.md's work log). Measuring `sizeHint()`
        on throwaway widgets, built with the app's real stylesheet already
        applied, makes this self-correcting for whatever font/DPI the
        machine it's actually running on has, instead of guessing again.
        """
        if self._row_height_cache is None:
            combo = _NoScrollComboBox()
            combo.addItem("Sample")
            spin = _NoScrollSpinBox()
            spin.setSuffix(" %")
            tallest = max(combo.sizeHint().height(), spin.sizeHint().height())
            # A few px of headroom beyond the tallest editor's own preferred
            # size - Qt's sizeHint is already generous, but this has been
            # wrong in the too-tight direction every time before, never the
            # too-loose one, so the margin is deliberately on that side.
            self._row_height_cache = max(tallest + 8, 32)
        return self._row_height_cache

    def _make_table(self, headers: list[str], widths: list[int]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(self._editor_row_height())
        table.setAlternatingRowColors(True)
        # Word-wrap + resizeRowsToContents() (the previous approach) computes
        # each row's height against the stretch column's width at the moment
        # of the call - if that hasn't settled yet (or just varies with a long
        # "Why" sentence), a single row can balloon to hundreds of pixels tall.
        # Fixed-height single-line rows are predictable; Qt elides overflowing
        # text with "..." by default, and the full text is always available
        # via the row's tooltip and the detail pane below.
        table.setWordWrap(False)
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

        style = self.style()

        self.locate_button = QPushButton("Locate files...")
        self.locate_button.setIcon(style.standardIcon(QStyle.SP_DirOpenIcon))
        self.locate_button.setToolTip(
            "Point the app at PROFSAVE_profile or the game folder by hand. Remembered afterwards."
        )
        self.locate_button.clicked.connect(self.locate_files)
        row.addWidget(self.locate_button)

        self.backup_button = QPushButton("Back up now")
        self.backup_button.setIcon(style.standardIcon(QStyle.SP_DriveHDIcon))
        self.backup_button.setToolTip("Snapshot both config files without changing anything.")
        self.backup_button.clicked.connect(self.backup_now)
        row.addWidget(self.backup_button)

        self.restore_button = QPushButton("Restore...")
        self.restore_button.setIcon(style.standardIcon(QStyle.SP_DialogResetButton))
        self.restore_button.setToolTip("Put your configuration back to an earlier snapshot.")
        self.restore_button.clicked.connect(self.open_restore)
        row.addWidget(self.restore_button)

        self.export_button = QPushButton("Export report")
        self.export_button.setIcon(style.standardIcon(QStyle.SP_DialogSaveButton))
        self.export_button.clicked.connect(self.export_report)
        row.addWidget(self.export_button)

        self.save_button = QPushButton("Save User.cfg only")
        self.save_button.setIcon(style.standardIcon(QStyle.SP_DialogSaveButton))
        self.save_button.clicked.connect(self.save_user_cfg)
        row.addWidget(self.save_button)

        self.apply_button = QPushButton("Apply everything")
        self.apply_button.setObjectName("Primary")
        self.apply_button.setIcon(style.standardIcon(QStyle.SP_DialogApplyButton))
        self.apply_button.setToolTip(
            "Writes User.cfg and the in-game settings, after taking one restore point covering both."
        )
        self.apply_button.clicked.connect(self.apply_everything)
        row.addWidget(self.apply_button)
        return row

    # -- detection ---------------------------------------------------------

    def _busy_start(self) -> None:
        """Reference-counted so two concurrent background jobs (hardware
        detection and the startup update check both fire from __init__) don't
        have one job's completion hide the indicator while the other is
        still running."""
        self._busy_count += 1
        self.busy_indicator.setVisible(True)

    def _busy_stop(self) -> None:
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count == 0:
            self.busy_indicator.setVisible(False)

    def redetect(self) -> None:
        self.redetect_button.setEnabled(False)
        self._busy_start()
        self.statusBar().showMessage("Detecting hardware...")
        for key, label in self.hw_labels.items():
            label.setText(f"detecting {key.lower()}...")
            label.setStyleSheet(f"font-style: italic; color: {theme.TEXT_DIM};")
        self.worker = DetectWorker()
        self.worker.finished_ok.connect(self._on_detected)
        self.worker.failed.connect(self._on_detect_failed)
        self.worker.start()

    def _on_detect_failed(self, detail: str) -> None:
        self.redetect_button.setEnabled(True)
        self._busy_stop()
        self.statusBar().showMessage("Hardware detection failed.")
        QMessageBox.warning(self, "Detection failed", detail[-1500:])

    def _on_detected(self, profile: hardware.HardwareProfile, game: paths.GamePaths) -> None:
        self.profile = profile
        self.game = game
        self.redetect_button.setEnabled(True)
        self._busy_stop()
        for label in self.hw_labels.values():
            label.setStyleSheet("")

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

        # First-ever launch (no persisted target yet, see prefs.py): start
        # from whichever preset actually needs the fewest changes against
        # what's really saved right now, instead of a hardcoded default that
        # may not match reality at all - then pull in every individual
        # setting that still differs from that preset, so the app opens
        # showing your actual configuration, not just the nearest preset.
        if not self._has_persisted_target:
            detected_preset = self._detect_closest_preset()
            if detected_preset is not None:
                self._active_preset = detected_preset
                button = self.preset_group.button(PRESETS.index(detected_preset))
                if button is not None:
                    button.setChecked(True)
                    self.preset_blurb.setText(PRESET_BLURB[detected_preset])
                self._seed_overrides_from_current()

        self._loading = False

        self.statusBar().showMessage(
            "Hardware detected." if p.detected else "Using a sample profile - detection unavailable here."
        )
        self._refresh_keybinds()
        self.refresh()

    def _refresh_keybinds(self) -> None:
        """Populate the read-only Key Bindings tab from the real
        PROFSAVE_profile. Independent of preset/target - keybinds don't
        affect the FPS prediction at all - so this only needs to re-run
        when the game paths (re)detect, not on every refresh().
        """
        container = self.keybinds_area.widget()
        layout = container.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.game.profsave or not self.game.profsave.is_file():
            layout.addWidget(dim(
                "PROFSAVE_profile not found - use Locate... to point at it, then "
                "re-detect hardware, to read your real key bindings."
            ))
            layout.addStretch(1)
            return

        try:
            text = self.game.profsave.read_text(encoding="utf-8", errors="ignore")
            bindings = keybinds.read_keybindings(text)
        except Exception as exc:
            layout.addWidget(dim(f"Could not read key bindings: {exc}"))
            layout.addStretch(1)
            return

        names = keybinds.dik_names()
        note = dim(
            "Read-only - this app never writes key bindings. Keyboard bindings are "
            "confirmed against the public DirectInput scan-code standard, "
            "cross-checked directly against this real profile. Mouse and controller "
            "bindings are not decoded yet and show their raw stored values. A few "
            "labels are marked (confirmed) - checked directly against the in-game "
            "menu; the rest are inferred from Battlefield 6's own internal names "
            "and may not be exact."
        )
        layout.addWidget(note)

        current_category = None
        for binding in sorted(bindings, key=lambda b: (b.category, b.label)):
            if binding.category != current_category:
                current_category = binding.category
                heading = QLabel(current_category.upper())
                heading.setObjectName("CardTitle")
                layout.addWidget(heading)
            frame, inner = card("")
            label_text = binding.label
            if not binding.label_confirmed:
                label_text += " (label not menu-confirmed)"
            title = QLabel(f"<b>{label_text}</b>")
            title.setWordWrap(True)
            inner.addWidget(title)
            inner.addWidget(dim(" / ".join(binding.display_slots(names))))
            layout.addWidget(frame)

        if not bindings:
            layout.addWidget(dim(
                "No key bindings found in your profile yet - Battlefield 6 only "
                "writes a binding once its settings page has been opened in-game."
            ))
        layout.addStretch(1)

    # -- update check --------------------------------------------------------

    def check_for_updates(self, silent: bool) -> None:
        """Ask GitHub whether main has moved on. Runs off the UI thread since it
        is a network call; `silent` controls whether a dialog pops up when there
        is nothing new (the startup check should not interrupt anyone)."""
        self.check_updates_button.setEnabled(False)
        self._busy_start()
        if not silent:
            self.statusBar().showMessage("Checking for updates...")
        self._update_worker = UpdateCheckWorker()
        self._update_worker.finished_ok.connect(
            lambda info: self._on_update_checked(info, silent)
        )
        self._update_worker.start()

    def _on_update_checked(self, info: update.UpdateInfo, silent: bool) -> None:
        self.check_updates_button.setEnabled(True)
        self._busy_stop()
        self.update_info = info
        # "Skip this version" only ever suppresses the passive startup nag
        # (the sidebar button + status bar message below) - an explicit,
        # non-silent check (the Check for updates button, or opening the
        # dialog again) always shows the real state regardless of what was
        # skipped before. Once main moves past the skipped commit,
        # latest_sha no longer matches and the banner returns on its own.
        skipped = (
            silent and info.available and bool(info.latest_sha)
            and info.latest_sha == prefs.load_skipped_update_sha()
        )
        self.update_button.setVisible(info.available and not skipped)
        if info.available:
            plural = "" if info.ahead_by == 1 else "s"
            self.update_button.setText(f"Update available ({info.ahead_by} commit{plural})")

        if silent:
            if info.available and not skipped:
                self.statusBar().showMessage(
                    f"An update is available - {info.ahead_by} commit(s) ahead of this build."
                )
            return

        if info.status == "error":
            self.statusBar().showMessage("Could not check for updates.")
            QMessageBox.warning(
                self, "Could not check for updates",
                f"{info.error}\n\nYou can always check manually at "
                f"https://github.com/{update.REPO}/commits/{update.BRANCH}",
            )
            return

        self.statusBar().showMessage(
            "Up to date." if info.status == "up_to_date" else "Checked for updates."
        )
        UpdateDialog(info, self).exec()

    def show_update_dialog(self, force_check: bool) -> None:
        if force_check or self.update_info is None:
            self.check_for_updates(silent=False)
            return
        UpdateDialog(self.update_info, self).exec()

    # -- recomputation -----------------------------------------------------

    def _on_preset(self, index: int) -> None:
        # Qt has already visually checked this button (the user clicked it
        # directly) - clicking one of the 4 built-in presets means a custom
        # profile, if one was shown selected, no longer is: the two are
        # mutually exclusive "what's active" indicators, never both at once.
        self._active_preset = PRESETS[index]
        self.preset_blurb.setText(PRESET_BLURB[PRESETS[index]])
        if self.profile_combo.currentIndex() != 0:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(0)
            self.profile_combo.blockSignals(False)
            self._on_profile_combo_changed(0)
        self.refresh()

    def current_target(self) -> Target:
        label = self.resolution_box.currentText()
        width, height = (int(part) for part in label.split("x", 1))
        return Target(
            preset=self._active_preset,
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

    def _apply_persisted_target(self) -> None:
        """Restore the last explicitly-chosen preset/toggles (see prefs.py's
        module docstring for why these, unlike most state, are remembered
        rather than re-derived). Called during sidebar construction, while
        `_loading` is still True, so this never triggers a premature refresh.

        Also restores which named profile (see `_build_profiles_card`) was
        last selected, purely so the combo box shows it instead of "- none
        selected -" - it does **not** re-apply that profile's settings (the
        preset/checkboxes/overrides restored above already carry whatever
        was actually in effect, via the exact same mechanism regardless of
        whether they came from a profile or manual choices). This is
        deliberately just the combo's displayed selection, not a "currently
        loaded profile" tracking state - seeing your settings drift from a
        profile you loaded, with the combo still innocently showing its
        name, would be worse than showing no selection at all. Only
        restoring which name was last *picked* avoids that: it can never
        claim more than "this was the last one you looked at." When a
        profile name is restored, no preset button is highlighted either -
        the two indicators are mutually exclusive (see `_on_preset`).
        """
        saved = prefs.load_target()
        self._has_persisted_target = bool(saved)
        if not saved:
            return
        preset = saved.get("preset")
        profile_name = saved.get("profile")
        has_profile = isinstance(profile_name, str) and bool(profile_name)
        if preset in PRESETS:
            self._active_preset = preset
            self.preset_blurb.setText(PRESET_BLURB[preset])
            # A profile being active is mutually exclusive with a preset
            # button looking selected (see _on_preset / load_selected_profile) -
            # only highlight the button when nothing was last shown via the
            # profile combo instead.
            if has_profile:
                checked_button = self.preset_group.checkedButton()
                if checked_button is not None:
                    checked_button.setChecked(False)
            else:
                button = self.preset_group.button(PRESETS.index(preset))
                if button is not None:
                    button.setChecked(True)
        for key, box in self.checkboxes.items():
            if isinstance(saved.get(key), bool):
                box.setChecked(saved[key])
        if has_profile:
            self._reload_profile_combo(select=profile_name)

    def _save_target(self) -> None:
        values: dict[str, object] = {
            "preset": self._active_preset,
            "profile": self._selected_profile_name(),
        }
        for key, box in self.checkboxes.items():
            values[key] = box.isChecked()
        prefs.save_target(values)

    def _detect_closest_preset(self) -> str | None:
        """Best-effort guess at which preset needs the fewest changes against
        what is actually saved right now - the matching logic itself lives in
        `compare.closest_preset` so it is testable without Qt.
        """
        return compare.closest_preset(
            self.db, self.profile, self.current_target(),
            self.game.profsave, self.game.user_cfg,
            overrides=self.setting_overrides, cfg_overrides=self.cfg_overrides,
        )

    def _seed_overrides_from_current(self) -> None:
        """First-ever launch only, right after `_detect_closest_preset` has
        already picked and checked the closest preset's button: pull in
        every individual setting that still differs from that preset in the
        real current profile, so the app opens matching what is actually
        configured rather than just the nearest built-in preset. The actual
        diffing/filtering logic lives in `compare.seed_overrides_from_current`
        (testable without Qt) - this just computes the one comparison it
        needs and persists the result the same way any other override is
        persisted.
        """
        try:
            base_rec = recommend(
                self.db, self.profile, self.current_target(),
                overrides=self.setting_overrides, cfg_overrides=self.cfg_overrides,
            )
            comparison = compare.from_paths(
                self.db, base_rec, self.game.profsave, self.game.user_cfg
            )
        except Exception:
            return
        if not comparison.available:
            return
        seed = compare.seed_overrides_from_current(comparison)
        if not seed:
            return
        self.setting_overrides.update(seed)
        prefs.save_setting_overrides(self.setting_overrides)

    def refresh(self) -> None:
        if self._loading:
            return
        media = (
            self.profile.drive_media.get(self.game.install_drive)
            if self.game.install_drive else None
        )
        self.rec = recommend(
            self.db, self.profile, self.current_target(), install_drive_media=media,
            overrides=self.setting_overrides, cfg_overrides=self.cfg_overrides,
        )
        self.comparison = compare.from_paths(
            self.db, self.rec, self.game.profsave, self.game.user_cfg
        )
        self._render()
        self._save_target()

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
        self._sync_cfg_table()

        self.cfg_view.setPlainText(writer.render_user_cfg(rec).replace("\r\n", "\n"))

        self._fill_scroll(self.warnings_area, [
            (w.severity, w.title, w.body, None) for w in rec.warnings
        ] or [("ok", "Nothing to flag", "No warnings for this configuration.", None)])

        self._fill_scroll(self.checks_area, [
            (t.get("severity", "low"), t["label"], t.get("why", ""), t.get("how", ""))
            for t in rec.tweaks
        ] or [("ok", "Nothing to check", "No system-level issues detected.", None)])

        self._fill_comparison()
        changed = 0 if comparison is None else (
            len(comparison.changes) + len([c for c in comparison.cfg_changes if c.action != "same"])
        )
        self.tabs.setTabText(
            self.tabs.indexOf(self.comparison_area),
            f"Current vs recommended ({changed})" if changed else "Current vs recommended",
        )
        self.tabs.setTabText(self.tabs.indexOf(self.warnings_area), f"Warnings ({len(rec.warnings)})")
        self.tabs.setTabText(self.tabs.indexOf(self.checks_area), f"System checks ({len(rec.tweaks)})")

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
        tags = []
        if change.risk in ("high", "unsafe") or change.confidence == "legacy":
            tags.append(f"{change.confidence or 'unknown'} / risk: {change.risk or 'unknown'}")
        if self.rec is not None and change.key in self.rec.cfg_overrides:
            tags.append("you overrode this in the User.cfg tab")
        if tags:
            inner.addWidget(dim(f"[{'; '.join(tags)}]"))
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
            if severity == "ok":
                # A genuinely clean bill of health reads better as a positive
                # checkmark than as just another neutral "INFO" badge.
                badge = f"<span style='color:{theme.OK}; font-weight:700'>&#10003; ALL CLEAR</span>"
            else:
                colour = theme.SEVERITY_COLOUR.get(severity, theme.TEXT_DIM)
                badge = f"<span style='color:{colour}; font-weight:700'>{severity.upper()}</span>"
            heading = QLabel(f"{badge}&nbsp;&nbsp;<b>{title}</b>")
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
    app.setWindowIcon(app_icon())

    try:
        db = database.load()
    except Exception as exc:
        QMessageBox.critical(None, f"{APP_NAME} could not start", str(exc))
        return 1

    window = MainWindow(db)
    window.show()
    return app.exec()
