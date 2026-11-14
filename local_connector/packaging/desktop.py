"""Native desktop controls around the existing local-only runtime."""

from __future__ import annotations

import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor


def run_desktop(runtime, address: str, stopped: threading.Event, updater=None, feed: str | None = None) -> int:
    # Imported only by the desktop composition root, never by the standalone wheel.
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QLabel,
        QMenu,
        QMessageBox,
        QPushButton,
        QSystemTrayIcon,
        QVBoxLayout,
        QWidget,
    )

    app = QApplication([])
    app.setApplicationName("AI Assistant Connector")
    app.setQuitOnLastWindowClosed(False)
    window = QWidget()
    window.setWindowTitle("AI Assistant Connector — local folders")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("Local-only connector. Only explicitly selected folders are indexed. No cloud activation."))
    layout.addWidget(QLabel("Closing the window hides it when a system tray is available. Use Quit to stop."))
    status = QLabel("Ready. Open local controls to review selection and index status.")
    status.setWordWrap(True)
    layout.addWidget(status)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="connector-updates")
    pending = []

    def report(message: str) -> None:
        status.setText(message)
        window.show()

    def choose_folder() -> None:
        folder = QFileDialog.getExistingDirectory(window, "Select an allowed local folder (read-only)")
        if not folder:
            return
        if QMessageBox.question(
            window, "Replace selected scope?", "Replace the current folder selection and clear its derived index? Original files remain untouched."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            runtime.select_roots([folder])
            report("Folder selected. Review status in local controls.")
        except (OSError, ValueError):
            report("Selection denied: folder access or cache separation could not be verified.")

    def delete_index() -> None:
        if QMessageBox.question(
            window, "Delete local index?", "Delete derived local content and pause indexing? Original files are not deleted."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            runtime.delete_index(confirmed=True)
            report("Index deleted; indexing remains paused until explicit Resume.")
        except (OSError, ValueError):
            report("Index deletion unavailable; completion is not confirmed.")

    def resume() -> None:
        try:
            runtime.resume_indexing()
            report("Indexing resumed for the selected scope.")
        except (OSError, ValueError):
            report("Resume denied. Review roots and reselect any replaced folder.")

    def check_updates() -> None:
        if updater is None or not feed:
            report("Updates disabled: no authenticated release feed/key is bundled.")
        elif not pending:
            status.setText("Checking the authenticated update feed…")
            pending.append(executor.submit(updater.automatic_update, feed))

    def rollback() -> None:
        if updater is None:
            report("Rollback unavailable: no authenticated installation is registered.")
            return
        if QMessageBox.question(
            window, "Roll back connector?", "Select the previous authenticated application version for the next restart? Local index and credentials are unchanged."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            updater.rollback(confirmed=True)
            report("Previous authenticated version selected. Quit and restart; data was not downgraded.")
        except (OSError, ValueError):
            report("Rollback denied: previous version or installation state could not be authenticated.")

    def quit_app() -> None:
        stopped.set()
        app.quit()

    actions = [
        ("Open local controls", lambda: webbrowser.open(address)),
        ("Choose folder", choose_folder),
        ("Delete local index", delete_index),
        ("Resume indexing", resume),
        ("Check for updates", check_updates),
        ("Roll back application", rollback),
        ("Quit", quit_app),
    ]
    menu = QMenu()
    for title, callback in actions:
        button = QPushButton(title)
        button.clicked.connect(callback)
        layout.addWidget(button)
        action = QAction(title, menu)
        action.triggered.connect(callback)
        menu.addAction(action)
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor("#334155"))
    tray = QSystemTrayIcon(QIcon(pixmap), app)
    tray.setToolTip("AI Assistant Connector — local-only")
    tray.setContextMenu(menu)
    tray.activated.connect(lambda _: window.show())
    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    if tray_available:
        tray.show()
    else:
        app.setQuitOnLastWindowClosed(True)
    window.show()

    def tick() -> None:
        if stopped.is_set():
            app.quit()
        if pending and pending[0].done():
            job = pending.pop()
            try:
                installed = job.result()
                status.setText("Update authenticated and installed. Quit and restart to use it." if installed else "No newer authenticated update.")
            except (OSError, ValueError):
                status.setText("Update unavailable or rejected. Current application remains selected.")

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(200)
    update_timer = QTimer()
    update_timer.timeout.connect(check_updates)
    update_timer.start(6 * 60 * 60 * 1000)
    if feed and updater is not None:
        QTimer.singleShot(0, check_updates)
    try:
        return app.exec()
    finally:
        stopped.set()
        tray.hide()
        executor.shutdown(wait=True, cancel_futures=True)
