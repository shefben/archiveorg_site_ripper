import sys
import os
import time
from datetime import datetime
from urllib.parse import urlparse

from PyQt5 import QtWidgets, QtCore

# Import the core functions from your existing ripper script.
# Adjust the module name if your file is named differently.
from archive_ripper import (
    run_ripper, focus_console_window, session, fetch_url, save_file,
    make_archive_url, log, RATE_LIMIT, TEXT_EXTS, strip_archive_comments,
)


class EraRipWorker(QtCore.QThread):
    """Background worker that queries the CDX API and downloads all unique
    assets for a URL pattern within a date range."""

    progress = QtCore.pyqtSignal(str)
    finished_ok = QtCore.pyqtSignal(int)
    error = QtCore.pyqtSignal(str)

    def __init__(self, url_pattern, start_date, end_date, output_dir, parent=None):
        super().__init__(parent)
        self.url_pattern = url_pattern
        self.start_date = start_date      # YYYYMMDD
        self.end_date = end_date          # YYYYMMDD
        self.output_dir = output_dir
        self._cancelled = False

        # Base URL is everything before the wildcard *
        if '*' in url_pattern:
            self.base_url = url_pattern[:url_pattern.index('*')]
        else:
            self.base_url = url_pattern.rstrip('/') + '/'

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(f"Querying CDX API for: {self.url_pattern}")
            self.progress.emit(f"Date range: {self.start_date} to {self.end_date}")

            entries = self._query_cdx()

            if not entries:
                self.progress.emit("No entries found matching the criteria.")
                self.finished_ok.emit(0)
                return

            self.progress.emit(f"Found {len(entries)} unique files to download.")

            downloaded_count = 0
            for i, (original_url, timestamp) in enumerate(entries):
                if self._cancelled:
                    self.progress.emit("Cancelled by user.")
                    break

                self.progress.emit(
                    f"[{i + 1}/{len(entries)}] {original_url}"
                )
                try:
                    self._download_entry(original_url, timestamp)
                    downloaded_count += 1
                except Exception as e:
                    self.progress.emit(f"  Failed: {e}")

            self.finished_ok.emit(downloaded_count)
        except Exception as e:
            self.error.emit(str(e))

    # ------------------------------------------------------------------

    def _query_cdx(self):
        """Query the CDX API and return a deduplicated list of
        (original_url, earliest_timestamp) tuples."""
        resp = session.get(
            "https://web.archive.org/cdx/search/cdx",
            params={
                'url': self.url_pattern,
                'matchType': 'prefix',
                'from': self.start_date,
                'to': self.end_date,
                'filter': 'statuscode:200',
                'collapse': 'urlkey',
                'fl': 'timestamp,original,digest',
                'output': 'json',
            },
            timeout=300,
        )
        resp.raise_for_status()
        time.sleep(RATE_LIMIT)

        data = resp.json()
        if len(data) <= 1:
            return []

        # data[0] is the header row: ['timestamp', 'original', 'digest']
        # For each unique original URL keep only the earliest timestamp.
        # Also skip rows whose digest we have already seen so that truly
        # identical content is not downloaded twice under different timestamps.
        url_earliest = {}       # original -> (timestamp, digest)
        seen_digests = set()

        for row in data[1:]:
            if len(row) < 3:
                continue
            timestamp, original, digest = row[0], row[1], row[2]

            if digest in seen_digests:
                # Same content already covered by an earlier entry.
                if original not in url_earliest:
                    continue
            seen_digests.add(digest)

            if original not in url_earliest or timestamp < url_earliest[original][0]:
                url_earliest[original] = (timestamp, digest)

        result = [(url, ts) for url, (ts, _digest) in url_earliest.items()]
        result.sort(key=lambda x: x[0])
        return result

    def _download_entry(self, original_url, timestamp):
        """Download a single Wayback snapshot and save it under *output_dir*
        using the same relative directory structure as the original site."""

        # Compute relative path from the base URL
        if original_url.startswith(self.base_url):
            rel_path = original_url[len(self.base_url):]
        else:
            parsed = urlparse(original_url)
            rel_path = parsed.path.lstrip('/')

        rel_path = rel_path.strip('/')
        if not rel_path:
            rel_path = 'index.html'

        local_path = os.path.join(self.output_dir, rel_path)

        # Skip files we already have on disk
        if os.path.exists(local_path):
            self.progress.emit(f"  Skipped (exists): {rel_path}")
            return

        # Use raw (id_/) mode for known text extensions so the Wayback
        # toolbar / banner is not injected into the response.
        ext = os.path.splitext(urlparse(original_url).path)[1].lower()
        raw = ext in TEXT_EXTS

        wayback_url = make_archive_url(timestamp, original_url, raw=raw)
        data = fetch_url(wayback_url)

        # Strip leftover archive.org comments from text content
        if raw and data:
            try:
                text = data.decode('utf-8', 'ignore')
                text = strip_archive_comments(text)
                data = text.encode('utf-8')
            except Exception:
                pass

        save_file(data, local_path)
        self.progress.emit(f"  Saved: {rel_path}")


class EraRipDialog(QtWidgets.QDialog):
    """Dialog that lets the user specify a URL pattern + date range and
    download every unique asset captured by the Wayback Machine."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rip Entire Era")
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()

        self.url_edit = QtWidgets.QLineEdit()
        self.url_edit.setPlaceholderText(
            "http://steamcommunity.com/public/*"
        )
        form.addRow("URL Pattern:", self.url_edit)

        self.start_date_edit = QtWidgets.QLineEdit()
        self.start_date_edit.setPlaceholderText("MM/DD/YYYY")
        form.addRow("Start Date:", self.start_date_edit)

        self.end_date_edit = QtWidgets.QLineEdit()
        self.end_date_edit.setPlaceholderText("MM/DD/YYYY")
        form.addRow("End Date:", self.end_date_edit)

        dir_layout = QtWidgets.QHBoxLayout()
        self.output_edit = QtWidgets.QLineEdit()
        self.output_edit.setPlaceholderText("Select output folder...")
        self.output_edit.setText("output")
        browse_btn = QtWidgets.QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_output)
        dir_layout.addWidget(self.output_edit)
        dir_layout.addWidget(browse_btn)
        form.addRow("Output Folder:", dir_layout)

        layout.addLayout(form)

        # Live log area
        self.log_area = QtWidgets.QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMinimumHeight(200)
        layout.addWidget(self.log_area)

        # Buttons
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)

        self.rip_btn = QtWidgets.QPushButton("Rip")
        self.rip_btn.clicked.connect(self._start_rip)
        btn_layout.addWidget(self.rip_btn)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)

        self.resize(650, 500)

    # --- helpers ---

    def _browse_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Output Folder"
        )
        if path:
            self.output_edit.setText(path)

    @staticmethod
    def _validate_date(date_str):
        """Return *YYYYMMDD* string if *date_str* is valid MM/DD/YYYY,
        otherwise ``None``."""
        try:
            dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
            return dt.strftime("%Y%m%d")
        except ValueError:
            return None

    # --- actions ---

    def _start_rip(self):
        url = self.url_edit.text().strip()
        start_raw = self.start_date_edit.text().strip()
        end_raw = self.end_date_edit.text().strip()
        output_dir = self.output_edit.text().strip() or "output"

        if not url or not start_raw or not end_raw:
            QtWidgets.QMessageBox.warning(
                self, "Missing Fields",
                "Please fill in the URL pattern, start date, and end date.",
            )
            return

        start_ymd = self._validate_date(start_raw)
        if not start_ymd:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Date",
                "Start date must be in MM/DD/YYYY format.",
            )
            return

        end_ymd = self._validate_date(end_raw)
        if not end_ymd:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Date",
                "End date must be in MM/DD/YYYY format.",
            )
            return

        if start_ymd > end_ymd:
            QtWidgets.QMessageBox.warning(
                self, "Invalid Date Range",
                "Start date must be before or equal to end date.",
            )
            return

        # Lock down the form while the worker runs
        self._set_inputs_enabled(False)
        self.log_area.clear()

        focus_console_window()

        self.worker = EraRipWorker(url, start_ymd, end_ymd, output_dir, self)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.start()

    def _on_cancel(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)
        self.close()

    # --- worker callbacks ---

    def _on_progress(self, msg):
        self.log_area.append(msg)
        log(msg)

    def _on_done(self, count):
        QtWidgets.QMessageBox.information(
            self, "Era Rip Complete",
            f"Downloaded {count} file(s).\n\n"
            "Check the log above and the console window for details.",
        )

    def _on_error(self, msg):
        QtWidgets.QMessageBox.critical(
            self, "Error",
            f"Era rip failed:\n{msg}\n\nCheck the console for details.",
        )

    def _on_worker_finished(self):
        self._set_inputs_enabled(True)
        self.worker = None

    def _set_inputs_enabled(self, enabled):
        self.url_edit.setEnabled(enabled)
        self.start_date_edit.setEnabled(enabled)
        self.end_date_edit.setEnabled(enabled)
        self.output_edit.setEnabled(enabled)
        self.rip_btn.setEnabled(enabled)


class BatchRipperWorker(QtCore.QThread):
    job_started = QtCore.pyqtSignal(int, str, str)   # index, url, save_path
    job_finished = QtCore.pyqtSignal(int, str)       # index, final_page
    job_error = QtCore.pyqtSignal(int, str)          # index, error_message
    all_done = QtCore.pyqtSignal()

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        # jobs is a list of dicts: {"url": ..., "save_path": ...}
        self.jobs = jobs

    def run(self):
        # Process each job sequentially
        for idx, job in enumerate(self.jobs):
            url = job["url"]
            save_path = job["save_path"]
            try:
                self.job_started.emit(idx, url, save_path)

                if not save_path:
                    output_dir = "output"
                    savename = None
                else:
                    output_dir = os.path.dirname(save_path) or "output"
                    savename = os.path.basename(save_path)

                page = run_ripper(
                    url=url,
                    output_dir=output_dir,
                    concurrency=1,
                    savename=savename,
                    reset=False,
                )
                self.job_finished.emit(idx, page)
            except Exception as e:
                self.job_error.emit(idx, str(e))
        self.all_done.emit()


class BatchMainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Archive.org Ripper (Batch)")
        self.jobs = []          # list of {"url": ..., "save_path": ...}
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        # --- Form for a single entry ---
        form_layout = QtWidgets.QFormLayout()

        self.url_edit = QtWidgets.QLineEdit()
        self.url_edit.setPlaceholderText("https://web.archive.org/web/XXXXXXXXXX/http://example.com/...")
        form_layout.addRow("Archive URL:", self.url_edit)

        save_layout = QtWidgets.QHBoxLayout()
        self.save_edit = QtWidgets.QLineEdit()
        self.save_edit.setPlaceholderText("Optional: choose file to save as (e.g. output/index.html)")
        browse_btn = QtWidgets.QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_save)
        save_layout.addWidget(self.save_edit)
        save_layout.addWidget(browse_btn)
        form_layout.addRow("Save as:", save_layout)

        main_layout.addLayout(form_layout)

        # --- Queue view ---
        queue_group = QtWidgets.QGroupBox("Queued jobs")
        queue_layout = QtWidgets.QVBoxLayout(queue_group)
        self.queue_list = QtWidgets.QListWidget()
        # Allow double-click to re-run a single job
        self.queue_list.itemDoubleClicked.connect(self.on_item_double_clicked)
        queue_layout.addWidget(self.queue_list)
        main_layout.addWidget(queue_group)

        # --- Buttons ---
        btn_layout = QtWidgets.QHBoxLayout()
        btn_layout.addStretch(1)

        self.add_btn = QtWidgets.QPushButton("Add more")
        self.add_btn.clicked.connect(self.add_job)
        btn_layout.addWidget(self.add_btn)

        self.exec_btn = QtWidgets.QPushButton("Execute")
        self.exec_btn.clicked.connect(self.execute_jobs)
        btn_layout.addWidget(self.exec_btn)

        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)
        btn_layout.addWidget(self.cancel_btn)

        self.era_btn = QtWidgets.QPushButton("Rip Entire Era")
        self.era_btn.clicked.connect(self.open_era_dialog)
        btn_layout.addWidget(self.era_btn)

        main_layout.addLayout(btn_layout)

        self.resize(700, 350)

    # ------------- UI actions -------------

    def browse_save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Select output file",
            "",
            "HTML Files (*.html *.htm);;All Files (*)",
        )
        if path:
            self.save_edit.setText(path)

    def open_era_dialog(self):
        dialog = EraRipDialog(self)
        dialog.exec_()

    def add_job(self):
        url = self.url_edit.text().strip()
        save_path = self.save_edit.text().strip()

        if not url:
            QtWidgets.QMessageBox.warning(self, "Missing URL", "Please enter an archive.org snapshot URL.")
            return

        # Store in internal list
        job = {"url": url, "save_path": save_path}
        self.jobs.append(job)

        # Add to visual queue
        label = url
        if save_path:
            label += f"  ?  {save_path}"
        self.queue_list.addItem(label)

        # Clear form for next entry
        self.url_edit.clear()
        self.save_edit.clear()
        self.url_edit.setFocus()

    def execute_jobs(self):
        if not self.jobs:
            QtWidgets.QMessageBox.information(self, "No jobs", "There are no jobs in the queue to execute.")
            return

        # Bring console to front so the user can watch the chaos.
        focus_console_window()

        # Disable UI while running
        self._set_ui_running_state(True)

        # Make a copy of jobs for the worker
        jobs_copy = list(self.jobs)

        self.worker = BatchRipperWorker(jobs_copy, self)
        self.worker.job_started.connect(self.on_job_started)
        self.worker.job_finished.connect(self.on_job_finished)
        self.worker.job_error.connect(self.on_job_error)
        self.worker.all_done.connect(self.on_all_done)
        self.worker.finished.connect(self.on_worker_finished)
        self.worker.start()

    def _set_ui_running_state(self, running: bool):
        self.add_btn.setEnabled(not running)
        self.exec_btn.setEnabled(not running)
        self.era_btn.setEnabled(not running)
        # Cancel just closes window; leaving it disabled while a job is
        # in progress avoids pretending we support mid-run cancel.
        self.cancel_btn.setEnabled(not running)
        self.url_edit.setEnabled(not running)
        self.save_edit.setEnabled(not running)
        # Still allow inspecting the queue
        self.queue_list.setEnabled(True)

    # ------------- Worker callbacks (batch mode) -------------

    def on_job_started(self, index: int, url: str, save_path: str):
        if 0 <= index < self.queue_list.count():
            item = self.queue_list.item(index)
            # Avoid stacking multiple prefixes if re-run
            base_text = item.text()
            if base_text.startswith("[DONE] ") or base_text.startswith("[ERROR] ") or base_text.startswith("[RUNNING] "):
                # Strip old status tag
                base_text = base_text.split("] ", 1)[-1]
            item.setText(f"[RUNNING] {base_text}")

    def on_job_finished(self, index: int, page_path: str):
        if 0 <= index < self.queue_list.count():
            item = self.queue_list.item(index)
            base_text = item.text()
            if base_text.startswith("[DONE] ") or base_text.startswith("[ERROR] ") or base_text.startswith("[RUNNING] "):
                base_text = base_text.split("] ", 1)[-1]
            item.setText(f"[DONE] {base_text}")

    def on_job_error(self, index: int, message: str):
        if 0 <= index < self.queue_list.count():
            item = self.queue_list.item(index)
            base_text = item.text()
            if base_text.startswith("[DONE] ") or base_text.startswith("[ERROR] ") or base_text.startswith("[RUNNING] "):
                base_text = base_text.split("] ", 1)[-1]
            item.setText(f"[ERROR] {base_text}  ({message})")

    def on_all_done(self):
        QtWidgets.QMessageBox.information(
            self,
            "Batch complete",
            "All queued jobs have finished.\n\nCheck the console window for full logs.",
        )
        # Clear the internal queue AND the visible list so user has a fresh batch
        self.jobs.clear()
        self.queue_list.clear()

    def on_worker_finished(self):
        self.worker = None
        self._set_ui_running_state(False)
        # Re-enable Cancel button now that nothing is running
        self.cancel_btn.setEnabled(True)

    # ------------- Single-item re-execution (double-click) -------------

    def on_item_double_clicked(self, item: QtWidgets.QListWidgetItem):
        # If a worker is already running (batch or single), don't start another.
        if self.worker is not None and self.worker.isRunning():
            QtWidgets.QMessageBox.warning(
                self,
                "Busy",
                "A job is already running.\nWait for it to finish before starting another.",
            )
            return

        row = self.queue_list.row(item)
        if row < 0 or row >= len(self.jobs):
            return

        job = self.jobs[row]

        # Bring console front for this single run
        focus_console_window()

        # Disable main controls during single-job run
        self._set_ui_running_state(True)

        # Reuse BatchRipperWorker with a single job.
        jobs_copy = [job]
        self.worker = BatchRipperWorker(jobs_copy, self)

        # Wrap callbacks so they know which row to update.
        self.worker.job_started.connect(
            lambda idx, url, save_path, row=row: self.on_single_job_started(row, url, save_path)
        )
        self.worker.job_finished.connect(
            lambda idx, page_path, row=row: self.on_single_job_finished(row, page_path)
        )
        self.worker.job_error.connect(
            lambda idx, msg, row=row: self.on_single_job_error(row, msg)
        )
        self.worker.finished.connect(self.on_single_worker_finished)
        # Don't hook all_done here; we don't want to clear the list on a single re-run.
        self.worker.start()

    def on_single_job_started(self, row: int, url: str, save_path: str):
        if 0 <= row < self.queue_list.count():
            item = self.queue_list.item(row)
            base_text = item.text()
            if base_text.startswith("[DONE] ") or base_text.startswith("[ERROR] ") or base_text.startswith("[RUNNING] "):
                base_text = base_text.split("] ", 1)[-1]
            item.setText(f"[RUNNING] {base_text}")

    def on_single_job_finished(self, row: int, page_path: str):
        if 0 <= row < self.queue_list.count():
            item = self.queue_list.item(row)
            base_text = item.text()
            if base_text.startswith("[DONE] ") or base_text.startswith("[ERROR] ") or base_text.startswith("[RUNNING] "):
                base_text = base_text.split("] ", 1)[-1]
            item.setText(f"[DONE] {base_text}")

        QtWidgets.QMessageBox.information(
            self,
            "Job complete",
            f"Job finished.\n\nOutput:\n{page_path}\n\nCheck console for full log.",
        )

    def on_single_job_error(self, row: int, message: str):
        if 0 <= row < self.queue_list.count():
            item = self.queue_list.item(row)
            base_text = item.text()
            if base_text.startswith("[DONE] ") or base_text.startswith("[ERROR] ") or base_text.startswith("[RUNNING] "):
                base_text = base_text.split("] ", 1)[-1]
            item.setText(f"[ERROR] {base_text}  ({message})")

        QtWidgets.QMessageBox.critical(
            self,
            "Job failed",
            f"Job failed:\n{message}\n\nCheck console for details.",
        )

    def on_single_worker_finished(self):
        self.worker = None
        self._set_ui_running_state(False)
        self.cancel_btn.setEnabled(True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = BatchMainWindow()
    win.show()
    app.exec_()


if __name__ == "__main__":
    main()
