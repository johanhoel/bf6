"""Update-available dialog: what changed on `main` since this build, and where
to get it.

Two ways to actually get it, and both are offered, not just one:
- **Open GitHub Actions build** - always available, opens a browser tab. No
  files written, nothing automatic - the original, still-default path.
- **Download and install now** - only for a genuinely frozen build
  (`sys.frozen`; a source checkout has nothing for this to replace), fetches
  the new exe from GitHub's rolling "latest" release in the background, then
  swaps and relaunches. See `bf6tuner.update`'s module docstring for the full
  design (why a Release and not the Actions artifact, why this only applies
  to a frozen build, why the swap needs a detached helper script).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from .. import update


class _DownloadWorker(QThread):
    finished_ok = Signal(Path)
    failed = Signal(str)

    def __init__(self, dest: Path) -> None:
        super().__init__()
        self.dest = dest

    def run(self) -> None:  # pragma: no cover - exercised by hand, not in CI
        try:
            path = update.download_update(self.dest)
        except update.SelfUpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # never let a raw traceback surface here
            self.failed.emit(f"Unexpected error: {exc}")
        else:
            self.finished_ok.emit(path)


class UpdateDialog(QDialog):
    def __init__(self, info: update.UpdateInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self._worker: _DownloadWorker | None = None
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

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminate - a single download has no % to show
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QDialogButtonBox()
        if info.status in ("update_available", "unknown"):
            if getattr(sys, "frozen", False):
                self.install_button = QPushButton("Download and install now")
                self.install_button.setToolTip(
                    "Downloads the new build from GitHub's 'latest' release, replaces this "
                    "exe, and relaunches automatically."
                )
                self.install_button.clicked.connect(self._start_download)
                buttons.addButton(self.install_button, QDialogButtonBox.ActionRole)
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
        self.close_button = buttons.addButton("Close", QDialogButtonBox.RejectRole)
        self.close_button.clicked.connect(self.reject)
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

    # -- self-update -----------------------------------------------------

    def _start_download(self) -> None:
        self.install_button.setEnabled(False)
        self.install_button.setText("Downloading...")
        self.close_button.setEnabled(False)
        self.progress.setVisible(True)

        dest = Path(tempfile.gettempdir()) / "BF6Tuner-update" / Path(sys.executable).name
        self._worker = _DownloadWorker(dest)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.start()

    def _on_download_ok(self, path: Path) -> None:
        self.progress.setVisible(False)
        try:
            update.apply_update_and_relaunch(path)
        except update.SelfUpdateError as exc:
            self._on_download_failed(str(exc))
            return
        # The helper script is now waiting for this process to exit - do that
        # cleanly rather than leaving the window open with nothing left to do.
        QApplication.instance().quit()

    def _on_download_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.install_button.setEnabled(True)
        self.install_button.setText("Download and install now")
        self.close_button.setEnabled(True)
        QMessageBox.warning(self, "Update failed", message)
