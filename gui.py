"""PySide6 GUI wrapper for the ePro Contact Audit Agent."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from epro.checkpoint import clear_progress, has_progress, load_meta
from epro.config import default_site, resolve_state
from epro.pipeline import run_batch

DARK_STYLE = """
QMainWindow {
    background-color: #1e1e2e;
}
QLabel {
    color: #cdd6f4;
}
QLabel#title {
    font-size: 20px;
    font-weight: bold;
    color: #89b4fa;
    padding: 8px 0;
}
QLabel#subtitle {
    font-size: 12px;
    color: #a6adc8;
    padding-bottom: 8px;
}
QLabel#output-label {
    color: #a6e3a1;
    font-size: 12px;
    padding: 4px 0;
}
QLineEdit {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
    selection-background-color: #89b4fa;
}
QLineEdit:focus {
    border: 1px solid #89b4fa;
}
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 13px;
}
QComboBox:focus {
    border: 1px solid #89b4fa;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #a6adc8;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
    outline: none;
}
QPushButton#process-btn {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#process-btn:hover {
    background-color: #b4d0fb;
}
QPushButton#process-btn:disabled {
    background-color: #45475a;
    color: #6c7086;
}
QPushButton#stop-btn {
    background-color: #f38ba8;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: bold;
}
QPushButton#stop-btn:hover {
    background-color: #f5a3ba;
}
QPushButton#stop-btn:disabled {
    background-color: #45475a;
    color: #6c7086;
}
QPushButton#restart-btn {
    background-color: #45475a;
    color: #cdd6f4;
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-size: 13px;
}
QPushButton#restart-btn:hover {
    background-color: #585b70;
}
QPushButton#restart-btn:disabled {
    background-color: #313244;
    color: #6c7086;
}
QPushButton#clear-btn {
    background-color: #45475a;
    color: #cdd6f4;
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-size: 13px;
}
QPushButton#clear-btn:hover {
    background-color: #585b70;
}
QPushButton#open-output-btn {
    background-color: #a6e3a1;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-size: 13px;
    font-weight: bold;
}
QPushButton#open-output-btn:hover {
    background-color: #c6f0c1;
}
QPushButton#browse-btn {
    background-color: #45475a;
    color: #cdd6f4;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton#browse-btn:hover {
    background-color: #585b70;
}
QTextEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 8px;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 12px;
}
QFrame#separator {
    background-color: #313244;
    max-height: 1px;
}
"""


class AuditWorker(QThread):
    log_message = Signal(str)
    progress_signal = Signal(int, int, str)
    finished_signal = Signal(str, str)

    def __init__(
        self,
        site_url: str,
        input_path: str,
        output_dir: str,
        extract_mode: str,
        api_key: str,
        limit: int | None,
        resume: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.site_url = site_url
        self.input_path = input_path
        self.output_dir = output_dir
        self.extract_mode = extract_mode
        self.api_key = api_key
        self.limit = limit
        self.resume = resume
        self._cancel = threading.Event()

    def request_stop(self):
        self._cancel.set()

    def run(self):
        try:
            state = resolve_state(base_url=self.site_url) if self.site_url else default_site()
            paths = asyncio.run(
                run_batch(
                    state=state,
                    input_path=Path(self.input_path),
                    output_dir=Path(self.output_dir),
                    extract_mode=self.extract_mode,
                    limit=self.limit,
                    api_key=self.api_key or None,
                    log=self.log_message.emit,
                    should_stop=self._cancel.is_set,
                    resume=self.resume,
                    progress=lambda i, total, cid: self.progress_signal.emit(i, total, cid),
                )
            )
            if paths.get("stopped"):
                done = paths.get("completed") or 0
                total = paths.get("total") or 0
                self.finished_signal.emit(
                    "stopped",
                    f"Stopped at {done}/{total}. Click Resume to continue. {paths.get('xlsx', '')}",
                )
            else:
                self.finished_signal.emit("complete", str(paths["xlsx"]))
        except Exception as e:
            self.log_message.emit(f"Error: {e}")
            self.finished_signal.emit("error", str(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ePro Contact Audit Agent")
        self.resize(860, 720)
        self.setStyleSheet(DARK_STYLE)
        self.worker: AuditWorker | None = None
        self._output_dir = Path("outputs")
        self._last_log_at = time.monotonic()
        self._stop_requested = False
        self._stop_deadline = 0.0
        self._warned_stuck = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(2000)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._build()
        self._load_gui_state()
        self._refresh_resume_state()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        title = QLabel("ePro Contact Audit Agent")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel(
            "Scrape public contract pages and agency attachments, then list supplier contacts that are not on the vendor profile"
        )
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)

        sep = QFrame()
        sep.setObjectName("separator")
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        layout.addWidget(QLabel("Anthropic API Key (required for hybrid / llm)"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setPlaceholderText("sk-ant-...  or leave blank for regex-only")
        self.api_key_input.setEchoMode(QLineEdit.Password)
        env_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if env_key:
            self.api_key_input.setText(env_key)
        layout.addWidget(self.api_key_input)

        layout.addWidget(QLabel("ePro site (used to build public PO URLs from contract IDs)"))
        self.site_input = QLineEdit()
        self.site_input.setText("https://oregonbuys.gov")
        self.site_input.setPlaceholderText("https://oregonbuys.gov")
        layout.addWidget(self.site_input)

        layout.addWidget(QLabel("Contract list (CSV / Excel)"))
        file_row = QHBoxLayout()
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("contracts.xlsx")
        self.input_path.textChanged.connect(self._refresh_resume_state)
        file_row.addWidget(self.input_path, stretch=1)
        browse = QPushButton("Browse")
        browse.setObjectName("browse-btn")
        browse.clicked.connect(self._on_browse)
        file_row.addWidget(browse)
        layout.addLayout(file_row)

        layout.addWidget(QLabel("Extract mode"))
        self.extract_combo = QComboBox()
        self.extract_combo.addItems(["hybrid", "regex", "llm"])
        layout.addWidget(self.extract_combo)

        layout.addWidget(QLabel("Limit (optional)"))
        self.limit_input = QLineEdit()
        self.limit_input.setPlaceholderText("Leave blank to process the whole list")
        layout.addWidget(self.limit_input)

        btn_row = QHBoxLayout()
        self.process_btn = QPushButton("Run")
        self.process_btn.setObjectName("process-btn")
        self.process_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self.process_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stop-btn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        btn_row.addWidget(self.stop_btn)

        self.restart_btn = QPushButton("Start over")
        self.restart_btn.setObjectName("restart-btn")
        self.restart_btn.setEnabled(False)
        self.restart_btn.clicked.connect(self._on_start_over)
        btn_row.addWidget(self.restart_btn)

        self.open_output_btn = QPushButton("Open Output Folder")
        self.open_output_btn.setObjectName("open-output-btn")
        self.open_output_btn.clicked.connect(self._on_open_output)
        btn_row.addWidget(self.open_output_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("subtitle")
        layout.addWidget(self.progress_label)

        layout.addWidget(QLabel("Log"))
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        layout.addWidget(self.log_area, stretch=1)

        self.output_label = QLabel("")
        self.output_label.setObjectName("output-label")
        self.output_label.setWordWrap(True)
        layout.addWidget(self.output_label)

        bottom = QHBoxLayout()
        clear_btn = QPushButton("Clear Log")
        clear_btn.setObjectName("clear-btn")
        clear_btn.clicked.connect(self.log_area.clear)
        bottom.addWidget(clear_btn)
        bottom.addStretch()
        layout.addLayout(bottom)

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select contract list",
            "",
            "Spreadsheets (*.xlsx *.xls *.csv);;All files (*.*)",
        )
        if path:
            self.input_path.setText(path)
            self._save_gui_state()

    def _on_run(self):
        self._start_batch(resume=self._has_checkpoint())

    def _on_start_over(self):
        path = self.input_path.text().strip()
        if path and Path(path).is_file():
            clear_progress(self._output_dir, Path(path))
            self.log_area.append("Cleared saved progress. Next Run starts from the first contract.\n")
        self._refresh_resume_state()
        self.progress_label.setText("")

    def _start_batch(self, *, resume: bool):
        input_path = self.input_path.text().strip()
        if not input_path:
            self.log_area.append("[Error] Choose a CSV or Excel file of contract IDs.\n")
            return
        if not Path(input_path).is_file():
            self.log_area.append(f"[Error] File not found: {input_path}\n")
            return

        extract_mode = self.extract_combo.currentText()
        api_key = self.api_key_input.text().strip()
        if extract_mode in ("llm", "hybrid") and not api_key:
            self.log_area.append(
                "[Warning] No API key — hybrid/llm will not call the model; regex still runs.\n"
            )

        limit = None
        raw_limit = self.limit_input.text().strip()
        if raw_limit:
            try:
                limit = int(raw_limit)
            except ValueError:
                self.log_area.append("[Error] Limit must be an integer.\n")
                return

        self._set_processing(True)
        self.output_label.setText("")
        self.progress_label.setText("Starting…")
        self.log_area.append("=" * 50)
        action = "Resuming" if resume else "Starting"
        self.log_area.append(f"{action} {extract_mode} / {Path(input_path).name}\n")

        self.worker = AuditWorker(
            self.site_input.text().strip(),
            input_path,
            str(self._output_dir),
            extract_mode,
            api_key,
            limit,
            resume,
        )
        self.worker.log_message.connect(self._on_log)
        self.worker.progress_signal.connect(self._on_progress)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()
        self._watchdog.start()
        self._save_gui_state()

    def _on_stop(self):
        if not self.worker or not self.worker.isRunning():
            self._set_processing(False)
            return
        self.log_area.append("Stop requested. Saving completed contracts…")
        self._stop_requested = True
        self._stop_deadline = time.monotonic() + 12
        self.stop_btn.setEnabled(False)
        self.worker.request_stop()

    def _on_log(self, message: str):
        self._last_log_at = time.monotonic()
        self._warned_stuck = False
        self.log_area.append(message)

    def _on_progress(self, current: int, total: int, contract_id: str):
        extra = f"  {contract_id}" if contract_id else ""
        self.progress_label.setText(f"Progress: {current} / {total}{extra}")

    def _on_finished(self, status: str, message: str):
        self._watchdog.stop()
        self._stop_requested = False
        self._set_processing(False)
        self._refresh_resume_state()
        if status == "complete":
            self.progress_label.setText("Complete")
            self.output_label.setText(f"Workbook: {message}")
            self.log_area.append(f"\nDone. {message}\n")
        elif status == "stopped":
            self.output_label.setText(message)
            self.log_area.append(f"\n{message}\n")
        else:
            self.log_area.append(f"\nFailed: {message}\n")
            if self._has_checkpoint():
                self.log_area.append("Saved progress is still available. Click Resume to continue.\n")

    def _on_watchdog(self):
        if not self.worker or not self.worker.isRunning():
            if self._stop_requested:
                self._after_force_stop()
            return
        now = time.monotonic()
        if self._stop_requested and now >= self._stop_deadline:
            self.log_area.append(
                "The current scrape did not unwind. Force-stopping. Finished contracts are saved."
            )
            try:
                self.worker.terminate()
                self.worker.wait(4000)
            except Exception:
                pass
            self._after_force_stop()
            return
        silent = now - self._last_log_at
        if silent > 90 and not self._warned_stuck:
            self._warned_stuck = True
            self.log_area.append(
                "No new log for 90s — this contract may be stuck. Click Stop to save progress and Resume later."
            )
        if silent > 240 and not self._stop_requested:
            self.log_area.append("Still no progress after 4 minutes. Stopping so you can Resume.")
            self._on_stop()

    def _after_force_stop(self):
        self._watchdog.stop()
        self._stop_requested = False
        self.worker = None
        self._set_processing(False)
        self._refresh_resume_state()
        self.log_area.append("Stopped. Click Resume to continue from the next unfinished contract.\n")

    def _set_processing(self, busy: bool):
        self.process_btn.setEnabled(not busy)
        self.stop_btn.setEnabled(busy)
        self.restart_btn.setEnabled(not busy and self._has_checkpoint())
        if busy:
            self.process_btn.setText("Running…")
        else:
            self._refresh_resume_state()

    def _has_checkpoint(self) -> bool:
        path = self.input_path.text().strip()
        if not path or not Path(path).is_file():
            return False
        return has_progress(self._output_dir, Path(path))

    def _refresh_resume_state(self):
        if self.worker and self.worker.isRunning():
            return
        if self._has_checkpoint():
            meta = load_meta(self._output_dir, Path(self.input_path.text().strip())) or {}
            done = int(meta.get("completed") or 0)
            total = int(meta.get("total") or 0)
            self.process_btn.setText("Resume")
            self.restart_btn.setEnabled(True)
            if total:
                self.progress_label.setText(f"Saved progress: {done} / {total}. Click Resume to continue.")
        else:
            self.process_btn.setText("Run")
            self.restart_btn.setEnabled(False)

    def _on_open_output(self):
        self._output_dir.mkdir(exist_ok=True)
        os.startfile(str(self._output_dir.resolve()))

    def _gui_state_path(self) -> Path:
        return self._output_dir / ".gui_state.json"

    def _load_gui_state(self):
        path = self._gui_state_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        last = str(data.get("input_path") or "").strip()
        if last and Path(last).is_file():
            self.input_path.setText(last)
        site = str(data.get("site_url") or "").strip()
        if site:
            self.site_input.setText(site)

    def _save_gui_state(self):
        self._output_dir.mkdir(exist_ok=True)
        data = {
            "input_path": self.input_path.text().strip(),
            "site_url": self.site_input.text().strip(),
        }
        try:
            self._gui_state_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self._on_stop()
            if self.worker:
                self.worker.wait(3000)
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
