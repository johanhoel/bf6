"""The apply-history dialog.

Read-only, on purpose: this is a log of what was applied and when (see
writer.applied_config_entry/prefs.record_applied_config), not another way to
roll back a file - that is what RestoreDialog and its restore points are
for. Each entry does show the restore-point stamp taken at the same moment,
so a user who wants to go back to a specific one still knows which stamp to
look for in Restore....
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .. import prefs
from . import theme


def _summary_line(entry: dict) -> str:
    label = entry.get("profile_name") or (entry.get("preset") or "?").capitalize()
    return (
        f"{entry.get('timestamp', '?')}    {label}    "
        f"{entry.get('resolution', '?')}@{entry.get('refresh_hz', '?')}Hz    "
        f"{entry.get('predicted_fps', '?')} FPS"
    )


class HistoryDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Apply history")
        self.setMinimumSize(720, 460)
        self.setStyleSheet(theme.STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Apply history")
        title.setObjectName("Title")
        layout.addWidget(title)

        blurb = QLabel(
            "The last 10 configurations actually applied - User.cfg and/or "
            "PROFSAVE_profile written for real - newest first. This is a log of what "
            "and when, not a rollback tool: use Restore... for that, matching a restore "
            "point's timestamp against one shown here if you need a specific one back."
        )
        blurb.setObjectName("Dim")
        blurb.setWordWrap(True)
        layout.addWidget(blurb)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        self.list.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.list, 1)

        self.detail = QLabel("")
        self.detail.setObjectName("Mono")
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(80)
        layout.addWidget(self.detail)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.clear_button = QPushButton("Clear history")
        self.clear_button.clicked.connect(self._clear)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.setObjectName("Primary")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.reload()

    def reload(self) -> None:
        self.list.clear()
        self.entries = prefs.load_apply_history()
        for entry in self.entries:
            self.list.addItem(QListWidgetItem(_summary_line(entry)))
        self.clear_button.setEnabled(bool(self.entries))
        if not self.entries:
            self.detail.setText(
                "Nothing applied yet. An entry is added here every time 'Apply everything' "
                "or 'Save User.cfg only' actually writes a file."
            )
        else:
            self.list.setCurrentRow(0)

    def _selected(self) -> dict | None:
        row = self.list.currentRow()
        return self.entries[row] if 0 <= row < len(self.entries) else None

    def _on_selection(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        wrote = []
        if entry.get("wrote_user_cfg"):
            wrote.append("User.cfg")
        if entry.get("wrote_profsave"):
            wrote.append("PROFSAVE_profile")
        lines = [
            f"Preset: {entry.get('profile_name') or entry.get('preset', '?')}",
            f"Target: {entry.get('resolution', '?')} @ {entry.get('refresh_hz', '?')} Hz",
            f"Predicted: {entry.get('predicted_fps', '?')} FPS "
            f"(GPU limit {entry.get('gpu_fps', '?')}, CPU limit {entry.get('cpu_fps', '?')}, "
            f"{entry.get('bottleneck', '?')}-limited)",
            f"Wrote: {', '.join(wrote) or 'nothing changed'}",
            f"Source: {entry.get('source', 'gui')}",
        ]
        if entry.get("restore_stamp"):
            lines.append(f"Restore point taken at the same time: {entry['restore_stamp']}")
        self.detail.setText("\n".join(lines))

    def _clear(self) -> None:
        confirm = QMessageBox.question(
            self, "Clear apply history", "Clear all 10 entries? This does not touch any restore point.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            prefs.clear_apply_history()
            self.reload()
