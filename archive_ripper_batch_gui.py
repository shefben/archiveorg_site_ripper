import sys
import os

from PyQt5 import QtWidgets, QtCore

# Import the core functions from your existing ripper script.
# Adjust the module name if your file is named differently.
from archive_ripper import run_ripper, focus_console_window


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
