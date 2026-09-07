"""Update-available dialog: what changed on `main` since this build, and where
to get it. There is no installer or auto-download - see bf6tuner.update's
docstring for why - so this only ever opens a browser tab, never writes files.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from .. import update


class UpdateDialog(QDialog):
    def __init__(self, info: update.UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.setWindowTitle("Check for updates")
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        heading = QLabel(self._heading_text())
        heading.setWordWrap(True)
        heading.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(heading)

        body = QLabel(self._body_text())
        body.setWordWrap(True)
        body.setObjectName("Dim")
        layout.addWidget(body)

        if info.commits:
            listing = QListWidget()
            listing.setAlternatingRowColors(True)
            for commit in info.commits:
                text = f"{commit.short_sha}  {commit.subject}"
                if commit.author:
                    text += f"   - {commit.author}"
                item = QListWidgetItem(text)
                item.setData(Qt.UserRole, commit.url)
                listing.addItem(item)
            listing.itemDoubleClicked.connect(self._open_commit)
            layout.addWidget(listing, 1)
            hint = QLabel("Double-click a commit to view it on GitHub.")
            hint.setObjectName("Dim")
            layout.addWidget(hint)

        buttons = QDialogButtonBox()
        if info.status in ("update_available", "unknown"):
            download = QPushButton("Open GitHub Actions build →")
            download.setToolTip(
                "Opens the Actions run for the latest commit on main, where the built "
                "executables are attached as an artifact - see README 'Getting the executable'."
            )
            download.clicked.connect(self._open_build)
            buttons.addButton(download, QDialogButtonBox.ActionRole)
        if info.compare_url:
            compare = QPushButton("View full diff on GitHub")
            compare.clicked.connect(lambda: self._open(info.compare_url))
            buttons.addButton(compare, QDialogButtonBox.ActionRole)
        close = buttons.addButton("Close", QDialogButtonBox.RejectRole)
        close.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _heading_text(self) -> str:
        info = self.info
        if info.status == "update_available":
            plural = "" if info.ahead_by == 1 else "s"
            return f"An update is available — {info.ahead_by} commit{plural} ahead of this build."
        if info.status == "up_to_date":
            return "You're up to date."
        if info.status == "unknown":
            return "Here's what has recently landed on main."
        return "Couldn't check for updates."

    def _body_text(self) -> str:
        info = self.info
        if info.status == "up_to_date":
            return f"This build matches main ({info.current_sha[:7] or 'unknown commit'})."
        if info.status == "unknown":
            return (
                "This build's exact commit could not be matched against main's history "
                "(a dev build, or the history has moved on since), so here are the most "
                "recent changes instead of an exact count."
            )
        if info.status == "error":
            return info.error or "No details available."
        return "Everything below is on main but not in the build you are running."

    def _open(self, url: str) -> None:
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _open_build(self) -> None:
        self._open(self.info.build_url)

    def _open_commit(self, item: QListWidgetItem) -> None:
        self._open(item.data(Qt.UserRole))
