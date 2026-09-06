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
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .. import APP_NAME, __version__
from .. import database, hardware, paths, writer
from ..engine import PRESETS, Recommendation, Target, recommend
from . import theme

RESOLUTIONS = [
    ("1920x1080", 1920, 1080), ("2560x1080", 2560, 1080), ("2560x1440", 2560, 1440),
    ("3440x1440", 3440, 1440), ("3840x1600", 3840, 1600), ("3840x2160", 3840, 2160),
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


class DetectWorker(QThread):
    """Hardware detection shells out to PowerShell, so keep it off the UI thread."""

    finished_ok = Signal(object, object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.finished_ok.emit(hardware.detect(), paths.discover())
        except Exception:
            self.failed.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self, db: database.Database) -> None:
        super().__init__()
        self.db = db
        self.profile = hardware.HardwareProfile()
        self.game = paths.GamePaths()
        self.rec: Recommendation | None = None
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
        panel.setFixedWidth(372)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
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
        for label, _, _ in RESOLUTIONS:
            self.resolution_box.addItem(label)
        self.resolution_box.currentIndexChanged.connect(self.refresh)
        form.addWidget(self.resolution_box, 0, 1)

        form.addWidget(dim("Refresh rate"), 1, 0)
        self.refresh_box = QSpinBox()
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
        return panel

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
        self.prediction_note = dim("")
        pred_layout.addWidget(self.prediction_note)
        layout.addWidget(pred_card)

        self.tabs = QTabWidget()
        self.settings_table = self._make_table(["Setting", "Value", "Why"], [260, 170, -1])
        self.tabs.addTab(self._wrap(self.settings_table), "In-game settings")

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

    def _make_table(self, headers: list[str], widths: list[int]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setWordWrap(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        header = table.horizontalHeader()
        for index, width in enumerate(widths):
            if width < 0:
                header.setSectionResizeMode(index, QHeaderView.Stretch)
            else:
                header.setSectionResizeMode(index, QHeaderView.Fixed)
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

        self.export_button = QPushButton("Export report")
        self.export_button.clicked.connect(self.export_report)
        row.addWidget(self.export_button)

        self.restore_button = QPushButton("Restore backup")
        self.restore_button.clicked.connect(self.restore_backup)
        row.addWidget(self.restore_button)

        self.ingame_button = QPushButton("Apply in-game settings")
        self.ingame_button.setToolTip("Patches PROFSAVE_profile. Battlefield 6 must be closed.")
        self.ingame_button.clicked.connect(self.apply_ingame)
        row.addWidget(self.ingame_button)

        self.save_button = QPushButton("Save User.cfg")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self.save_user_cfg)
        row.addWidget(self.save_button)
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
        self.rec = recommend(self.db, self.profile, self.current_target(), install_drive_media=media)
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
        note = (
            "Modelled estimate, not a measurement. Turn the in-game FPS overlay on and compare."
        )
        if rec.headroom_note:
            note = rec.headroom_note + "  " + note
        self.prediction_note.setText(note)

        table = self.settings_table
        table.setRowCount(0)
        for choice in rec.settings:
            row = table.rowCount()
            table.insertRow(row)
            name = QTableWidgetItem(choice.label)
            name.setToolTip(choice.menu)
            value_text = "leave as-is" if choice.value == "keep" else choice.display
            value = QTableWidgetItem(value_text)
            if choice.personal:
                value.setForeground(Qt.gray)
            else:
                font = QFont()
                font.setBold(True)
                value.setFont(font)
            why = QTableWidgetItem(choice.reason)
            why.setToolTip(choice.reason)
            table.setItem(row, 0, name)
            table.setItem(row, 1, value)
            table.setItem(row, 2, why)
        table.resizeRowsToContents()

        self.cfg_view.setPlainText(writer.render_user_cfg(rec).replace("\r\n", "\n"))

        self._fill_scroll(self.warnings_area, [
            (w.severity, w.title, w.body, None) for w in rec.warnings
        ] or [("info", "Nothing to flag", "No warnings for this configuration.", None)])

        self._fill_scroll(self.checks_area, [
            (t.get("severity", "low"), t["label"], t.get("why", ""), t.get("how", ""))
            for t in rec.tweaks
        ] or [("info", "Nothing to check", "No system-level issues detected.", None)])

        self.tabs.setTabText(2, f"Warnings ({len(rec.warnings)})")
        self.tabs.setTabText(3, f"System checks ({len(rec.tweaks)})")

        target_path = self.game.user_cfg or Path("(install folder not found)")
        self.path_label.setText(
            f"User.cfg -> {target_path}\n"
            f"In-game  -> {self.game.profsave or '(PROFSAVE_profile not found)'}"
        )
        self.ingame_button.setEnabled(self.game.profsave is not None)

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
        try:
            result = writer.write_user_cfg(self.rec, Path(path))
        except Exception as exc:
            QMessageBox.critical(self, "Could not write User.cfg", str(exc))
            return
        detail = result.message
        if result.backup:
            detail += f"\n\nPrevious version backed up to:\n{result.backup}"
        QMessageBox.information(self, "User.cfg saved", detail)
        self.statusBar().showMessage(result.message)

    def apply_ingame(self) -> None:
        if self.rec is None or not self.game.profsave or not self._guard_game_closed():
            return
        existing = writer.parse_profsave(
            self.game.profsave.read_text(encoding="utf-8", errors="ignore")
        )
        plan = writer.profsave_plan(self.rec, existing)
        if not plan:
            QMessageBox.information(
                self, "Nothing to change",
                "None of the keys this app manages differ from your current profile. "
                "Note that only keys already present in PROFSAVE_profile are ever touched.",
            )
            return
        preview = "\n".join(f"  {key}: {old}  ->  {new}" for key, old, new in plan[:24])
        if len(plan) > 24:
            preview += f"\n  ... and {len(plan) - 24} more"
        confirm = QMessageBox.question(
            self, "Apply in-game settings",
            f"{len(plan)} setting(s) will change in:\n{self.game.profsave}\n\n{preview}\n\n"
            "A timestamped backup is written first. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            result = writer.write_profsave(self.rec, self.game.profsave)
        except Exception as exc:
            QMessageBox.critical(self, "Could not write settings", str(exc))
            return
        QMessageBox.information(
            self, "In-game settings applied",
            result.message + (f"\n\nBackup: {result.backup}" if result.backup else ""),
        )

    def export_report(self) -> None:
        if self.rec is None:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "Export report", str(Path.home() / "bf6-tuner-report.txt"),
            "Text report (*.txt);;JSON (*.json)",
        )
        if not path:
            return
        target = Path(path)
        content = writer.render_json(self.rec) if target.suffix.lower() == ".json" \
            else writer.render_report(self.rec)
        target.write_text(content, encoding="utf-8")
        self.statusBar().showMessage(f"Report written to {target}")
        QMessageBox.information(self, "Report exported", str(target))

    def restore_backup(self) -> None:
        options = [("user_cfg", self.game.user_cfg), ("profsave", self.game.profsave)]
        entries: list[tuple[Path, Path]] = []
        for tag, destination in options:
            if destination is None:
                continue
            for backup in writer.list_backups(tag):
                entries.append((backup, destination))
        if not entries:
            QMessageBox.information(
                self, "No backups", f"Nothing has been backed up yet.\n\n{writer.backup_root()}"
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose a backup to restore", str(writer.backup_root()), "Backups (*.bak)"
        )
        if not path:
            return
        backup = Path(path)
        destination = next((d for b, d in entries if b == backup), None)
        if destination is None:
            QMessageBox.warning(
                self, "Unknown backup",
                "That backup does not correspond to a file this app currently knows about.",
            )
            return
        if not self._guard_game_closed():
            return
        result = writer.restore(backup, destination)
        QMessageBox.information(self, "Restored", result.message)


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
