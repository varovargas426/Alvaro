from __future__ import annotations

import datetime
import hashlib
import os
import traceback
from textwrap import shorten
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtWidgets

from .ai import (
    AiJsonParseError,
    MAX_NOTE_CHARS,
    dictionary_response_text,
    has_openai_key,
    normalize_tags,
    parse_dictionary_response,
    parse_quiz_response,
    quiz_response_text,
    suggest_tags,
    summarize_with_ai,
)
from .models import CalendarEntry, DictionaryEntry, Note, QuizQuestion, StudyData
from .storage import get_data_path, load_data, save_data

LOG_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "app.log"))


def log_to_file(message: str, exc_info: bool = True) -> None:
    log_dir = os.path.dirname(LOG_PATH)
    os.makedirs(log_dir, exist_ok=True)
    timestamp = datetime.datetime.now().isoformat(timespec="seconds")
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")
        if exc_info:
            traceback_text = traceback.format_exc()
            log_file.write(traceback_text)
            if not traceback_text.endswith("\n"):
                log_file.write("\n")


class _AiWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str)

    def __init__(self, task_func) -> None:
        super().__init__()
        self._task_func = task_func
        self.last_exception: Exception | None = None

    def run(self) -> None:
        try:
            result = self._task_func()
        except Exception as exc:  # noqa: BLE001
            self.last_exception = exc
            message = str(exc) or exc.__class__.__name__
            if isinstance(exc, AiJsonParseError):
                log_to_file(
                    f"AI JSON parse failed. Raw content:\n{exc.raw_content}",
                    exc_info=True,
                )
            else:
                log_to_file("AI worker failed.", exc_info=True)
            self.failed.emit(message)
            return
        self.finished.emit(result)


class CyberStudyApp(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CyberStudy Coach")
        self.resize(1000, 700)
        self.data = load_data()
        self._ai_threads: List[QtCore.QThread] = []
        self._active_ai_thread: Optional[QtCore.QThread] = None
        self._active_ai_worker: Optional[_AiWorker] = None
        self._ai_success_handler = None
        self._last_notes_hash: Optional[str] = None
        self._pending_ai_note: Optional[Note] = None
        self._auto_ai_timer = QtCore.QTimer(self)
        self._auto_ai_timer.setSingleShot(True)
        self._auto_ai_timer.timeout.connect(self._run_auto_ai_pipeline)
        self._quiz_current_index = 0
        self._quiz_order: List[int] = []
        self._quiz_score_correct = 0
        self._quiz_score_incorrect = 0
        self._quiz_feedback = ""

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_notes_tab(), "Notes")
        tabs.addTab(self._build_dictionary_tab(), "Dictionary")
        tabs.addTab(self._build_calendar_tab(), "Calendar")
        tabs.addTab(self._build_quiz_tab(), "Quizzes")
        self.setCentralWidget(tabs)

        if not has_openai_key():
            data_path = get_data_path()
            self.statusBar().showMessage(
                "Set OPENAI_API_KEY to enable ChatGPT features (summaries, glossary, quizzes)."
                f" Data is stored in {data_path}."
            )

    def _build_notes_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        form = QtWidgets.QFormLayout()
        self.note_title = QtWidgets.QLineEdit()
        self.note_content = QtWidgets.QTextEdit()
        form.addRow("Title:", self.note_title)
        form.addRow("Content:", self.note_content)
        layout.addLayout(form)

        button_row = QtWidgets.QHBoxLayout()
        self.save_summary_btn = QtWidgets.QPushButton("Save + Summarize")
        self.save_summary_btn.clicked.connect(self._save_and_summarize)
        button_row.addWidget(self.save_summary_btn)
        layout.addLayout(button_row)

        self.summary_label = QtWidgets.QLabel("Summary: (empty)")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.chatgpt_output = QtWidgets.QTextEdit()
        self.chatgpt_output.setReadOnly(True)
        self.chatgpt_output.setPlaceholderText("ChatGPT output will appear here.")
        layout.addWidget(self.chatgpt_output)

        filter_row = QtWidgets.QHBoxLayout()
        self.tag_filter_enabled = QtWidgets.QCheckBox("Filter by tag")
        self.tag_filter_enabled.stateChanged.connect(self._refresh_notes_list)
        self.tag_filter_combo = QtWidgets.QComboBox()
        self.tag_filter_combo.currentIndexChanged.connect(self._refresh_notes_list)
        filter_row.addWidget(self.tag_filter_enabled)
        filter_row.addWidget(self.tag_filter_combo)
        layout.addLayout(filter_row)

        self.note_details = QtWidgets.QTextEdit()
        self.note_details.setReadOnly(True)
        self.note_details.setPlaceholderText("Select a note to view full content.")
        self.notes_list = QtWidgets.QListWidget()
        self.notes_list.currentRowChanged.connect(self._update_note_details)
        self.notes_list.itemClicked.connect(self._open_note_details_dialog)
        self._refresh_notes_list()
        layout.addWidget(self.notes_list)
        layout.addWidget(self.note_details)

        notes_actions = QtWidgets.QHBoxLayout()
        edit_btn = QtWidgets.QPushButton("Edit note")
        edit_btn.clicked.connect(self._edit_note)
        delete_btn = QtWidgets.QPushButton("Delete note")
        delete_btn.clicked.connect(self._delete_note)
        notes_actions.addWidget(edit_btn)
        notes_actions.addWidget(delete_btn)
        layout.addLayout(notes_actions)

        return widget

    def _build_dictionary_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        self.dict_search = QtWidgets.QLineEdit()
        self.dict_search.setPlaceholderText("Search terms…")
        self.dict_search.textChanged.connect(self._filter_dictionary)
        layout.addWidget(self.dict_search)

        helper = QtWidgets.QLabel(
            "Glossary terms are generated automatically from your notes."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)

        self.dictionary_details = QtWidgets.QTextEdit()
        self.dictionary_details.setReadOnly(True)
        self.dictionary_details.setPlaceholderText("Select a term to view the full definition.")
        self.dict_list = QtWidgets.QListWidget()
        self.dict_list.currentRowChanged.connect(self._update_dictionary_details)
        self._refresh_dictionary_list()
        layout.addWidget(self.dict_list)
        layout.addWidget(self.dictionary_details)

        dict_actions = QtWidgets.QHBoxLayout()
        edit_btn = QtWidgets.QPushButton("Edit term")
        edit_btn.clicked.connect(self._edit_dictionary_entry)
        delete_btn = QtWidgets.QPushButton("Delete term")
        delete_btn.clicked.connect(self._delete_dictionary_entry)
        dict_actions.addWidget(edit_btn)
        dict_actions.addWidget(delete_btn)
        layout.addLayout(dict_actions)

        return widget

    def _build_calendar_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        form = QtWidgets.QFormLayout()
        self.calendar_title = QtWidgets.QLineEdit()
        self.calendar_date = QtWidgets.QDateEdit()
        self.calendar_date.setCalendarPopup(True)
        self.calendar_date.setDate(QtCore.QDate.currentDate())
        self.calendar_details = QtWidgets.QTextEdit()
        form.addRow("Title:", self.calendar_title)
        form.addRow("Date:", self.calendar_date)
        form.addRow("Details:", self.calendar_details)
        layout.addLayout(form)

        save_btn = QtWidgets.QPushButton("Save event")
        save_btn.clicked.connect(self._save_calendar_entry)
        layout.addWidget(save_btn)

        self.calendar_list = QtWidgets.QListWidget()
        self._refresh_calendar_list()
        layout.addWidget(self.calendar_list)

        return widget

    def _build_quiz_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        self.quiz_empty_label = QtWidgets.QLabel("No quiz yet. Add notes to auto-generate.")
        self.quiz_empty_label.setWordWrap(True)
        layout.addWidget(self.quiz_empty_label)

        self.quiz_progress_label = QtWidgets.QLabel("Progress: 0/0")
        layout.addWidget(self.quiz_progress_label)

        self.quiz_score_label = QtWidgets.QLabel("Score: 0 correct / 0 incorrect")
        layout.addWidget(self.quiz_score_label)

        self.quiz_question_label = QtWidgets.QLabel("")
        self.quiz_question_label.setWordWrap(True)
        layout.addWidget(self.quiz_question_label)

        self.quiz_choice_group = QtWidgets.QButtonGroup(self)
        self.quiz_choice_group.setExclusive(True)
        self.quiz_choice_buttons: List[QtWidgets.QRadioButton] = []
        for _ in range(4):
            choice = QtWidgets.QRadioButton("")
            self.quiz_choice_group.addButton(choice)
            self.quiz_choice_buttons.append(choice)
            layout.addWidget(choice)

        self.quiz_feedback_label = QtWidgets.QLabel("")
        self.quiz_feedback_label.setWordWrap(True)
        layout.addWidget(self.quiz_feedback_label)

        buttons_row = QtWidgets.QHBoxLayout()
        self.quiz_submit_btn = QtWidgets.QPushButton("Submit")
        self.quiz_submit_btn.clicked.connect(self._submit_quiz_answer)
        self.quiz_next_btn = QtWidgets.QPushButton("Next")
        self.quiz_next_btn.clicked.connect(self._next_quiz_question)
        self.quiz_reset_btn = QtWidgets.QPushButton("Reset")
        self.quiz_reset_btn.clicked.connect(self._reset_quiz_state)
        buttons_row.addWidget(self.quiz_submit_btn)
        buttons_row.addWidget(self.quiz_next_btn)
        buttons_row.addWidget(self.quiz_reset_btn)
        layout.addLayout(buttons_row)

        self._refresh_quiz_list()

        return widget

    def _save_and_summarize(self) -> None:
        title = self.note_title.text().strip()
        content = self.note_content.toPlainText().strip()
        if not title or not content:
            QtWidgets.QMessageBox.warning(self, "Missing information", "Enter a title and content.")
            return
        note = Note(title=title, content=content, summary="", tags=[])
        self.data.notes.append(note)
        self.set_chatgpt_output(f"Notes count: {len(self.data.notes)}")
        save_data(self.data)
        self.note_title.clear()
        self.note_content.clear()
        self.refresh_notes()
        self._log_ui_update("summary_label.setText")
        self.summary_label.setText("Summary: (queued)")
        self.trigger_auto_ai_pipeline(note)
        if not has_openai_key():
            QtWidgets.QMessageBox.warning(
                self,
                "Missing OpenAI key",
                "Set OPENAI_API_KEY in your environment to use ChatGPT summaries.",
            )
            self.set_chatgpt_output("Summary skipped: missing OPENAI_API_KEY.")
            return

        if len(content) > MAX_NOTE_CHARS:
            self.set_chatgpt_output("Notes too long; summary will be chunked.")

    def _save_calendar_entry(self) -> None:
        title = self.calendar_title.text().strip()
        if not title:
            QtWidgets.QMessageBox.warning(self, "Missing information", "Enter a title.")
            return
        day = self.calendar_date.date().toPython()
        details = self.calendar_details.toPlainText().strip()
        self.data.calendar.append(CalendarEntry(title=title, day=day, details=details))
        save_data(self.data)
        self.calendar_title.clear()
        self.calendar_details.clear()
        self._refresh_calendar_list()

    def _refresh_notes_list(self) -> None:
        self.notes_list.clear()
        for note in self._filtered_notes():
            title_line = note.title
            content_preview = shorten(note.content, width=120, placeholder="…")
            summary_preview = shorten(note.summary or "No summary yet.", width=120, placeholder="…")
            timestamp = getattr(note, "created_at", "")
            timestamp_line = f" — {timestamp}" if timestamp else ""
            item_text = f"{title_line}{timestamp_line}\nContent: {content_preview}\nSummary: {summary_preview}"
            item = QtWidgets.QListWidgetItem(item_text)
            item.setData(QtCore.Qt.UserRole, note)
            self.notes_list.addItem(item)
        self._update_note_details(self.notes_list.currentRow())
        self._refresh_tag_filter_options()

    def _refresh_dictionary_list(self) -> None:
        self.dict_list.clear()
        query = self.dict_search.text().strip().lower()
        for entry in self._filtered_dictionary_entries(query):
            self.dict_list.addItem(entry.term)

        if not self.data.dictionary:
            self.dict_list.addItem("No glossary terms yet.")
        self._update_dictionary_details(self.dict_list.currentRow())

    def _filter_dictionary(self) -> None:
        self._refresh_dictionary_list()

    def _filtered_dictionary_entries(self, query: str) -> List[DictionaryEntry]:
        if not query:
            return self.data.dictionary
        filtered: List[DictionaryEntry] = []
        for entry in self.data.dictionary:
            term = entry.term.lower()
            definition = entry.definition.lower()
            if query in term or query in definition:
                filtered.append(entry)
        return filtered

    def _refresh_calendar_list(self) -> None:
        self.calendar_list.clear()
        for entry in sorted(self.data.calendar, key=lambda item: item.day):
            preview = shorten(entry.details, width=60, placeholder="…")
            self.calendar_list.addItem(f"{entry.day.isoformat()} · {entry.title} — {preview}")

    def _refresh_quiz_list(self) -> None:
        self._reset_quiz_state()

    def set_chatgpt_output(self, text: str) -> None:
        self._log_ui_update("chatgpt_output.setPlainText")
        self.chatgpt_output.setPlainText(text)

    @QtCore.Slot(str)
    def _on_ai_failed(self, message: str) -> None:
        if isinstance(self._active_ai_worker, _AiWorker) and isinstance(
            self._active_ai_worker.last_exception, AiJsonParseError
        ):
            self.set_chatgpt_output(str(self._active_ai_worker.last_exception))
        else:
            self.set_chatgpt_output(f"AI error: {message}")
        self._cleanup_ai_thread()

    @QtCore.Slot(object)
    def _on_ai_finished(self, output: object) -> None:
        try:
            if self._ai_success_handler is not None:
                self._ai_success_handler(output)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AiJsonParseError):
                log_to_file(
                    f"AI JSON parse failed. Raw content:\n{exc.raw_content}",
                    exc_info=True,
                )
                self.set_chatgpt_output(str(exc))
            else:
                log_to_file("AI response handling failed.", exc_info=True)
                self.set_chatgpt_output("AI error. See logs/app.log")
        finally:
            self._cleanup_ai_thread()

    def _cleanup_ai_thread(self) -> None:
        self._set_ai_buttons_enabled(True)
        if self._active_ai_thread in self._ai_threads:
            self._ai_threads.remove(self._active_ai_thread)
        self._active_ai_thread = None
        self._active_ai_worker = None
        self._ai_success_handler = None

    def _run_ai_task(
        self,
        task_func,
        on_success,
        *,
        status_message: Optional[str] = "Thinking...",
    ) -> None:
        self._set_ai_buttons_enabled(False)
        if status_message is not None:
            self.set_chatgpt_output(status_message)
        worker = _AiWorker(task_func)
        thread = QtCore.QThread(self)
        self._active_ai_worker = worker
        self._active_ai_thread = thread
        self._ai_success_handler = on_success
        worker.moveToThread(thread)

        worker.finished.connect(self._on_ai_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(self._on_ai_failed)
        worker.failed.connect(thread.quit)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._ai_threads.append(thread)
        thread.start()

    def _set_ai_buttons_enabled(self, enabled: bool) -> None:
        self.save_summary_btn.setEnabled(enabled)

    def refresh_notes(self) -> None:
        self._refresh_notes_list()

    def refresh_dictionary(self) -> None:
        self._refresh_dictionary_list()

    def refresh_quizzes(self) -> None:
        self._refresh_quiz_list()

    def _notes_hash(self) -> str:
        payload = "\n".join(f"{note.title}\n{note.content}" for note in self.data.notes)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def trigger_auto_ai_pipeline(self, note: Optional[Note]) -> None:
        self._pending_ai_note = note
        self._auto_ai_timer.start(1200)

    def _run_auto_ai_pipeline(self) -> None:
        if not self.data.notes:
            self.data.dictionary = []
            self.data.quizzes = []
            save_data(self.data)
            self.refresh_dictionary()
            self.refresh_quizzes()
            self.set_chatgpt_output("No notes available for auto-updates.")
            self._pending_ai_note = None
            return
        if not has_openai_key():
            self.set_chatgpt_output("Auto updates skipped: missing OPENAI_API_KEY.")
            self._pending_ai_note = None
            return
        notes_hash = self._notes_hash()
        if notes_hash == self._last_notes_hash:
            self._pending_ai_note = None
            return
        self._last_notes_hash = notes_hash
        pending_note = self._pending_ai_note
        self._pending_ai_note = None
        if pending_note is not None:
            self._run_auto_summary(pending_note)
        else:
            self._run_auto_dictionary()

    def _run_auto_summary(self, note: Note) -> None:
        if note not in self.data.notes:
            self._run_auto_dictionary()
            return
        content = note.content.strip()
        self.set_chatgpt_output(f"Auto: summary... len(content)={len(content)}, notes={len(self.data.notes)}")
        if not content:
            self.set_chatgpt_output("Summary skipped: content is empty.")
            self._run_auto_dictionary()
            return
        if not self.data.notes:
            self.set_chatgpt_output("Summary skipped: no notes available.")
            return

        def handle_summary(result: object) -> None:
            if note not in self.data.notes:
                self._run_auto_dictionary()
                return
            summary_text = str(result).strip()
            note.summary = summary_text
            save_data(self.data)
            self.refresh_notes()
            self._log_ui_update("summary_label.setText")
            preview = summary_text.splitlines()[0] if summary_text else "(empty)"
            self.summary_label.setText(f"Summary: {preview}")
            self.set_chatgpt_output(summary_text or "No summary returned.")
            self._run_auto_tags(note)

        try:
            self._run_ai_task(
                lambda: summarize_with_ai(note.content),
                handle_summary,
                status_message=None,
            )
        except Exception as exc:  # noqa: BLE001
            log_to_file("AI summary handler failed.", exc_info=True)
            self.set_chatgpt_output(f"AI error: {exc}")

    def _run_auto_tags(self, note: Note) -> None:
        content = note.content.strip()
        self.set_chatgpt_output(f"Auto: tags... len(content)={len(content)}, notes={len(self.data.notes)}")
        if not content:
            self.set_chatgpt_output("Tags skipped: content is empty.")
            self._run_auto_dictionary()
            return
        if not self.data.notes:
            self.set_chatgpt_output("Tags skipped: no notes available.")
            return

        def handle_tags(result: object) -> None:
            ai_tags = [tag for tag in result if isinstance(tag, str)]
            note.tags = normalize_tags(ai_tags)
            save_data(self.data)
            self.refresh_notes()
            self._run_auto_dictionary()

        try:
            self._run_ai_task(
                lambda: suggest_tags(note.title, note.content),
                handle_tags,
                status_message=None,
            )
        except Exception as exc:  # noqa: BLE001
            log_to_file("AI tag generation handler failed.", exc_info=True)
            self.set_chatgpt_output(f"AI error: {exc}")

    def _run_auto_dictionary(self) -> None:
        self.set_chatgpt_output(f"Auto: dictionary... notes={len(self.data.notes)}")
        if not self.data.notes:
            self.set_chatgpt_output("Dictionary skipped: no notes available.")
            return

        def handle_dictionary(content: object) -> None:
            entries = parse_dictionary_response(str(content))
            self.data.dictionary = sorted(entries, key=lambda entry: entry.term.lower())
            save_data(self.data)
            self.refresh_dictionary()
            self._run_auto_quiz()

        try:
            self._run_ai_task(
                lambda: dictionary_response_text(self.data.notes),
                handle_dictionary,
                status_message=None,
            )
        except Exception as exc:  # noqa: BLE001
            log_to_file("AI glossary handler failed.", exc_info=True)
            self.set_chatgpt_output(f"AI error: {exc}")

    def _run_auto_quiz(self) -> None:
        self.set_chatgpt_output(f"Auto: quiz... notes={len(self.data.notes)}")
        if not self.data.notes:
            self.set_chatgpt_output("Quiz skipped: no notes available.")
            return

        def handle_quiz(content: object) -> None:
            questions = parse_quiz_response(str(content))
            self.data.quizzes = questions
            save_data(self.data)
            self.refresh_quizzes()
            self.set_chatgpt_output("Auto updates complete.")

        try:
            self._run_ai_task(
                lambda: quiz_response_text(self.data.notes),
                handle_quiz,
                status_message=None,
            )
        except Exception as exc:  # noqa: BLE001
            log_to_file("AI quiz handler failed.", exc_info=True)
            self.set_chatgpt_output(f"AI error: {exc}")

    def _filtered_notes(self) -> List[Note]:
        if not self.tag_filter_enabled.isChecked():
            return self.data.notes
        selected = self.tag_filter_combo.currentText().strip().lower()
        if not selected or selected == "all tags":
            return self.data.notes
        return [note for note in self.data.notes if selected in {tag.lower() for tag in note.tags}]

    def _refresh_tag_filter_options(self) -> None:
        current = self.tag_filter_combo.currentText()
        tags = sorted({tag for note in self.data.notes for tag in note.tags})
        self.tag_filter_combo.blockSignals(True)
        self.tag_filter_combo.clear()
        self.tag_filter_combo.addItem("All tags")
        self.tag_filter_combo.addItems(tags)
        if current:
            index = self.tag_filter_combo.findText(current)
            if index >= 0:
                self.tag_filter_combo.setCurrentIndex(index)
        self.tag_filter_combo.blockSignals(False)

    def _reset_quiz_state(self) -> None:
        self._quiz_current_index = 0
        self._quiz_score_correct = 0
        self._quiz_score_incorrect = 0
        self._quiz_feedback = ""
        self._quiz_order = list(range(len(self.data.quizzes)))
        self._render_quiz_question()

    def _render_quiz_question(self) -> None:
        total = len(self._quiz_order)
        if total == 0:
            self.quiz_empty_label.setVisible(True)
            self.quiz_question_label.setText("")
            self.quiz_progress_label.setText("Progress: 0/0")
            self.quiz_score_label.setText("Score: 0 correct / 0 incorrect")
            self.quiz_feedback_label.setText("")
            self._set_quiz_controls_enabled(False)
            return
        self.quiz_empty_label.setVisible(False)
        self._set_quiz_controls_enabled(True)
        current_number = self._quiz_current_index + 1
        self.quiz_progress_label.setText(f"Progress: {current_number}/{total}")
        self.quiz_score_label.setText(
            f"Score: {self._quiz_score_correct} correct / {self._quiz_score_incorrect} incorrect"
        )
        quiz = self.data.quizzes[self._quiz_order[self._quiz_current_index]]
        self.quiz_question_label.setText(f"Q: {quiz.prompt}")
        for index, button in enumerate(self.quiz_choice_buttons):
            text = quiz.choices[index] if index < len(quiz.choices) else ""
            button.setText(text)
            button.setChecked(False)
        self.quiz_feedback_label.setText(self._quiz_feedback)

    def _set_quiz_controls_enabled(self, enabled: bool) -> None:
        self.quiz_submit_btn.setEnabled(enabled)
        self.quiz_next_btn.setEnabled(enabled)
        self.quiz_reset_btn.setEnabled(True)
        for button in self.quiz_choice_buttons:
            button.setEnabled(enabled)

    def _submit_quiz_answer(self) -> None:
        if not self._quiz_order:
            return
        selected = next(
            (idx for idx, button in enumerate(self.quiz_choice_buttons) if button.isChecked()),
            None,
        )
        if selected is None:
            self.quiz_feedback_label.setText("Select an option to check your answer.")
            return
        quiz = self.data.quizzes[self._quiz_order[self._quiz_current_index]]
        if selected == quiz.correct_index:
            self._quiz_score_correct += 1
            result = "Correct!"
        else:
            self._quiz_score_incorrect += 1
            result = "Incorrect."
        explanation = quiz.explanation or "No explanation provided."
        self._quiz_feedback = f"{result} {explanation}"
        self._render_quiz_question()

    def _next_quiz_question(self) -> None:
        if not self._quiz_order:
            return
        self._quiz_current_index = (self._quiz_current_index + 1) % len(self._quiz_order)
        self._quiz_feedback = ""
        self._render_quiz_question()

    def _update_note_details(self, index: int) -> None:
        notes = self._filtered_notes()
        if index < 0 or index >= len(notes):
            self.note_details.setPlainText("")
            return
        note = notes[index]
        tags_line = f"Tags: {', '.join(note.tags)}" if note.tags else "Tags: (none)"
        self.note_details.setPlainText(f"{note.title}\n{tags_line}\n\n{note.content}")

    def _open_note_details_dialog(self, item: QtWidgets.QListWidgetItem) -> None:
        note = item.data(QtCore.Qt.UserRole)
        if not isinstance(note, Note):
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Note details")
        layout = QtWidgets.QVBoxLayout(dialog)
        title_label = QtWidgets.QLabel(note.title)
        title_label.setWordWrap(True)
        layout.addWidget(title_label)
        summary_label = QtWidgets.QLabel("Summary")
        layout.addWidget(summary_label)
        summary_text = QtWidgets.QTextEdit()
        summary_text.setReadOnly(True)
        summary_text.setPlainText(note.summary or "No summary yet.")
        layout.addWidget(summary_text)
        content_label = QtWidgets.QLabel("Content")
        layout.addWidget(content_label)
        content_text = QtWidgets.QTextEdit()
        content_text.setReadOnly(True)
        content_text.setPlainText(note.content)
        layout.addWidget(content_text)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def _update_dictionary_details(self, index: int) -> None:
        entry = self._selected_dictionary_entry()
        if entry is None:
            self.dictionary_details.setPlainText("")
            return
        self.dictionary_details.setPlainText(f"{entry.term}\n\n{entry.definition}")

    def _confirm_delete(self, label: str) -> bool:
        result = QtWidgets.QMessageBox.question(
            self,
            "Confirm delete",
            f"Delete {label}?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        return result == QtWidgets.QMessageBox.Yes

    def _log_ui_update(self, action: str) -> None:
        thread_id = int(QtCore.QThread.currentThreadId())
        log_to_file(f"UI update: {action} (thread_id={thread_id})", exc_info=False)

    def _open_note_edit_dialog(self, note: Note) -> Optional[Tuple[str, str]]:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Edit note")
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        title_input = QtWidgets.QLineEdit(note.title)
        content_input = QtWidgets.QTextEdit(note.content)
        form.addRow("Title:", title_input)
        form.addRow("Content:", content_input)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            title = title_input.text().strip()
            content = content_input.toPlainText().strip()
            if not title or not content:
                QtWidgets.QMessageBox.warning(
                    self, "Missing information", "Enter a title and content."
                )
                return None
            return title, content
        return None

    def _open_dictionary_edit_dialog(self, entry: DictionaryEntry) -> Optional[Tuple[str, str]]:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Edit term")
        layout = QtWidgets.QVBoxLayout(dialog)
        form = QtWidgets.QFormLayout()
        term_input = QtWidgets.QLineEdit(entry.term)
        definition_input = QtWidgets.QTextEdit(entry.definition)
        form.addRow("Term:", term_input)
        form.addRow("Definition:", definition_input)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QtWidgets.QDialog.Accepted:
            term = term_input.text().strip()
            definition = definition_input.toPlainText().strip()
            if not term or not definition:
                QtWidgets.QMessageBox.warning(
                    self, "Missing information", "Enter a term and definition."
                )
                return None
            return term, definition
        return None

    def _edit_note(self) -> None:
        index = self.notes_list.currentRow()
        if index < 0:
            QtWidgets.QMessageBox.information(self, "No selection", "Select a note to edit.")
            return
        notes = self._filtered_notes()
        note = notes[index]
        result = self._open_note_edit_dialog(note)
        if not result:
            return
        title, content = result
        note.title = title
        note.content = content
        note.summary = ""
        self._log_ui_update("summary_label.setText")
        self.summary_label.setText("Summary: (queued)")
        save_data(self.data)
        self.refresh_notes()
        self.trigger_auto_ai_pipeline(note)

    def _delete_note(self) -> None:
        index = self.notes_list.currentRow()
        if index < 0:
            QtWidgets.QMessageBox.information(self, "No selection", "Select a note to delete.")
            return
        notes = self._filtered_notes()
        note = notes[index]
        if not self._confirm_delete(f"note '{note.title}'"):
            return
        self.data.notes.remove(note)
        save_data(self.data)
        self.refresh_notes()
        self.trigger_auto_ai_pipeline(None)

    def _selected_dictionary_entry(self) -> Optional[DictionaryEntry]:
        index = self.dict_list.currentRow()
        if index < 0:
            return None
        query = self.dict_search.text().strip().lower()
        filtered = self._filtered_dictionary_entries(query)
        if index >= len(filtered):
            return None
        return filtered[index]

    def _edit_dictionary_entry(self) -> None:
        entry = self._selected_dictionary_entry()
        if entry is None:
            QtWidgets.QMessageBox.information(self, "No selection", "Select a term to edit.")
            return
        result = self._open_dictionary_edit_dialog(entry)
        if not result:
            return
        term, definition = result
        entry.term = term
        entry.definition = definition
        save_data(self.data)
        self.refresh_dictionary()

    def _delete_dictionary_entry(self) -> None:
        entry = self._selected_dictionary_entry()
        if entry is None:
            QtWidgets.QMessageBox.information(self, "No selection", "Select a term to delete.")
            return
        if not self._confirm_delete(f"term '{entry.term}'"):
            return
        self.data.dictionary.remove(entry)
        save_data(self.data)
        self.refresh_dictionary()

if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = CyberStudyApp()
    window.show()
    app.exec()
