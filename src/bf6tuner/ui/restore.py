"""The restore dialog.

Recovery has to be one obvious action, not a file picker pointed at a folder of
timestamped .bak files. Each restore point covers every config file the app
touches, taken at the same moment, so putting things back is a single choice.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

from .. import writer
from . import theme


class RestoreDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restore a previous configuration")
        self.setMinimumSize(760, 480)
        self.setStyleSheet(theme.STYLESHEET)
        self.restored = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("Restore points")
        title.setObjectName("Title")
        layout.addWidget(title)

        blurb = QLabel(
            "Every time this app writes a config file it first snapshots both files together. "
            "Restoring puts them back exactly as they were — including deleting a file that "
            "did not exist at the time."
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
        self.detail.setMinimumHeight(52)
        layout.addWidget(self.detail)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.folder_button = QPushButton("Open backup folder")
        self.folder_button.clicked.connect(self._open_folder)
        buttons.addWidget(self.folder_button)
        buttons.addStretch(1)

        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete)
        buttons.addWidget(self.delete_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)

        self.restore_button = QPushButton("Restore this")
        self.restore_button.setObjectName("Primary")
        self.restore_button.clicked.connect(self._restore)
        buttons.addWidget(self.restore_button)
        layout.addLayout(buttons)

        self.reload()

    # -- data --------------------------------------------------------------

    def reload(self) -> None:
        self.list.clear()
        self.points = writer.list_restore_points()
        for point in self.points:
            item = QListWidgetItem(f"{point.stamp}    {point.label}")
            item.setData(Qt.UserRole, point.directory)
            self.list.addItem(item)
        empty = not self.points
        if empty:
            self.detail.setText(
                f"No restore points yet. One is created automatically the first time you "
                f"apply anything.\n{writer.backup_root()}"
            )
        for button in (self.restore_button, self.delete_button):
            button.setEnabled(False)
        if self.points:
            self.list.setCurrentRow(0)

    def _selected(self):
        row = self.list.currentRow()
        return self.points[row] if 0 <= row < len(self.points) else None

    def _on_selection(self) -> None:
        point = self._selected()
        enabled = point is not None
        for button in (self.restore_button, self.delete_button):
            button.setEnabled(enabled)
        if point is None:
            return
        lines = [f"Taken {point.stamp} by BF6 Tuner {point.app_version or '?'}"]
        for entry in point.files:
            label = writer.ROLE_LABELS.get(str(entry["role"]), str(entry["role"]))
            if entry.get("existed"):
                lines.append(f"  {label}: {entry['original']}  ({int(entry.get('size', 0)):,} bytes)")
            else:
                lines.append(f"  {label}: did not exist — restoring will delete the current file")
        self.detail.setText("\n".join(lines))

    # -- actions -----------------------------------------------------------

    def _restore(self) -> None:
        point = self._selected()
        if point is None:
            return
        summary = "\n".join(
            f"  {writer.ROLE_LABELS.get(str(e['role']), str(e['role']))} -> "
            + (str(e["original"]) if e.get("existed") else f"delete {Path(str(e['original'])).name}")
            for e in point.files
        )
        confirm = QMessageBox.question(
            self, "Restore this configuration",
            f"Put your configuration back to how it was at {point.stamp}?\n\n{summary}\n\n"
            "Battlefield 6 must be closed.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            results = writer.restore_from(point)
        except OSError as exc:
            QMessageBox.critical(self, "Restore failed", str(exc))
            return
        self.restored = True
        QMessageBox.information(
            self, "Restored", "\n".join(result.message for result in results)
        )
        self.accept()

    def _delete(self) -> None:
        point = self._selected()
        if point is None:
            return
        confirm = QMessageBox.question(
            self, "Delete restore point",
            f"Delete the snapshot taken {point.stamp}? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            writer.delete_restore_point(point)
            self.reload()

    def _open_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl

        root = writer.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
