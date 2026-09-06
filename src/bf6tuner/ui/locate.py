"""Pointing the app at the two config files by hand.

Detection covers the layouts we know about, but Battlefield moves these files
between releases and storefronts, so there has to be a way to just say where
they are. Anything chosen here is remembered.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from .. import paths, prefs
from . import theme


class LocateDialog(QDialog):
    def __init__(self, game: paths.GamePaths, parent=None) -> None:
        super().__init__(parent)
        self.game = game
        self.changed = False
        self.setWindowTitle("Locate Battlefield 6 files")
        self.setMinimumWidth(760)
        self.setStyleSheet(theme.STYLESHEET)

        stored = prefs.load()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        title = QLabel("Locate Battlefield 6 files")
        title.setObjectName("Title")
        layout.addWidget(title)

        self.rows: dict[str, QLabel] = {}
        for role, heading, blurb in (
            ("install_dir", "Game folder (for User.cfg)",
             "The folder containing the Battlefield 6 executable. User.cfg goes here, "
             "next to the .exe - not in Documents."),
            ("profsave", "PROFSAVE_profile (in-game settings)",
             "Usually under Documents\\Battlefield 6\\settings, sometimes in a 'steam' or "
             "per-account subfolder. It only exists once the game has saved its video settings."),
        ):
            block = QVBoxLayout()
            block.setSpacing(4)
            label = QLabel(f"<b>{heading}</b>")
            block.addWidget(label)
            note = QLabel(blurb)
            note.setObjectName("Dim")
            note.setWordWrap(True)
            block.addWidget(note)

            row = QHBoxLayout()
            value = QLabel(self._current(role, stored))
            value.setObjectName("Mono")
            value.setWordWrap(True)
            self.rows[role] = value
            row.addWidget(value, 1)

            browse = QPushButton("Browse...")
            browse.clicked.connect(lambda _=False, r=role: self._browse(r))
            row.addWidget(browse)

            clear = QPushButton("Clear")
            clear.setToolTip("Forget the manual path and go back to auto-detection.")
            clear.clicked.connect(lambda _=False, r=role: self._clear(r))
            row.addWidget(clear)

            block.addLayout(row)
            layout.addLayout(block)

        if game.profsave is None:
            diagnostics = QLabel(game.search_summary())
            diagnostics.setObjectName("Mono")
            diagnostics.setWordWrap(True)
            layout.addWidget(diagnostics)

        if game.profsave_candidates:
            found = QLabel(
                "Profile files found:<br>"
                + "<br>".join(str(path) for path in game.profsave_candidates[:6])
            )
            found.setObjectName("Mono")
            found.setWordWrap(True)
            layout.addWidget(found)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = QPushButton("Done")
        close.setObjectName("Primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _current(self, role: str, stored: dict[str, str]) -> str:
        if role in stored:
            return f"{stored[role]}   [set by hand]"
        detected = self.game.install_dir if role == "install_dir" else self.game.profsave
        return str(detected) if detected else "not found"

    def _browse(self, role: str) -> None:
        if role == "install_dir":
            start = str(self.game.install_dir or Path.home())
            chosen = QFileDialog.getExistingDirectory(self, "Select the Battlefield 6 game folder", start)
        else:
            start = str(self.game.profsave or self.game.documents_dir or Path.home())
            chosen, _ = QFileDialog.getOpenFileName(
                self, "Select PROFSAVE_profile", start, "Profile (PROFSAVE*);;All files (*)"
            )
        if not chosen:
            return
        prefs.set_override(role, chosen)
        self.rows[role].setText(f"{chosen}   [set by hand]")
        self.changed = True

    def _clear(self, role: str) -> None:
        prefs.set_override(role, None)
        self.rows[role].setText("cleared - will auto-detect on the next scan")
        self.changed = True
