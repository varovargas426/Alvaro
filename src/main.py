from __future__ import annotations

import json
import re
from collections import Counter
from textwrap import shorten
from typing import List

import requests
from PySide6 import QtCore, QtWidgets

from .ai import dictionary_from_notes, generate_text, has_openai_key, quiz_from_notes, summarize_with_ai
from .models import CalendarEntry, Note, QuizQuestion, StudyData
from .storage import get_data_path, load_data, save_data

SUMMARY_MAX_SENTENCES = 3
DEFAULT_REQUEST_TIMEOUT = 10


def summarize_text(text: str) -> str:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    if not sentences:
        return ""
    if len(sentences) <= SUMMARY_MAX_SENTENCES:
        return " ".join(sentences)
    return " ".join(sentences[:SUMMARY_MAX_SENTENCES])


def extract_keywords(text: str, limit: int = 8) -> List[str]:
    tokens = re.findall(r"[A-Za-z0-9_-]{4,}", text.lower())
    stopwords = {"this", "that", "with", "from", "have", "your", "into", "about", "between"}
    filtered = [token for token in tokens if token not in stopwords]
    return [word for word, _ in Counter(filtered).most_common(limit)]


def build_quiz_from_notes(notes: List[Note]) -> List[QuizQuestion]:
    questions: List[QuizQuestion] = []
    for note in notes:
        keywords = extract_keywords(note.content, limit=4)
        for keyword in keywords:
            prompt = f"What does '{keyword}' mean in the context of {note.title}?"
            answer = f"Review your notes about {note.title} to define '{keyword}'."
            questions.append(QuizQuestion(prompt=prompt, answer=answer))
    return questions


def search_wikipedia(query: str) -> str:
    if not query.strip():
        return ""
    response = requests.get(
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(query),
        timeout=DEFAULT_REQUEST_TIMEOUT,
    )
    if response.status_code != 200:
        return "No information found on Wikipedia."
    data = response.json()
    return data.get("extract", "No information found.")


class CyberStudyApp(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CyberStudy Coach")
        self.resize(1000, 700)
        self.data = load_data()

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_notes_tab(), "Notes")
        tabs.addTab(self._build_dictionary_tab(), "Dictionary")
        tabs.addTab(self._build_calendar_tab(), "Calendar")
        tabs.addTab(self._build_quiz_tab(), "Quizzes")
        tabs.addTab(self._build_web_tab(), "Web")
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
        save_btn = QtWidgets.QPushButton("Save note")
        save_btn.clicked.connect(self._save_note)
        summary_btn = QtWidgets.QPushButton("Generate summary")
        summary_btn.clicked.connect(self._generate_summary)
        ai_summary_btn = QtWidgets.QPushButton("Summarize with ChatGPT")
        ai_summary_btn.clicked.connect(self._generate_ai_summary)
        ai_text_btn = QtWidgets.QPushButton("Generate haiku with ChatGPT")
        ai_text_btn.clicked.connect(self._generate_ai_text)
        button_row.addWidget(save_btn)
        button_row.addWidget(summary_btn)
        button_row.addWidget(ai_summary_btn)
        button_row.addWidget(ai_text_btn)
        layout.addLayout(button_row)

        self.summary_label = QtWidgets.QLabel("Summary: (empty)")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.ai_text_output = QtWidgets.QTextEdit()
        self.ai_text_output.setReadOnly(True)
        self.ai_text_output.setPlaceholderText("ChatGPT output will appear here.")
        layout.addWidget(self.ai_text_output)

        self.notes_list = QtWidgets.QListWidget()
        self._refresh_notes_list()
        layout.addWidget(self.notes_list)

        return widget

    def _build_dictionary_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        helper = QtWidgets.QLabel(
            "Glossary terms are generated automatically from your notes."
            " Click the button below to refresh."
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)

        generate_btn = QtWidgets.QPushButton("Generate glossary from notes")
        generate_btn.clicked.connect(self._generate_dictionary)
        layout.addWidget(generate_btn)

        self.dict_list = QtWidgets.QListWidget()
        self._refresh_dictionary_list()
        layout.addWidget(self.dict_list)

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

        generate_btn = QtWidgets.QPushButton("Generate quiz from notes")
        generate_btn.clicked.connect(self._generate_quiz)
        layout.addWidget(generate_btn)

        ai_generate_btn = QtWidgets.QPushButton("Generate quiz with ChatGPT")
        ai_generate_btn.clicked.connect(self._generate_ai_quiz)
        layout.addWidget(ai_generate_btn)

        self.quiz_list = QtWidgets.QListWidget()
        self._refresh_quiz_list()
        layout.addWidget(self.quiz_list)

        return widget

    def _build_web_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        form = QtWidgets.QHBoxLayout()
        self.web_query = QtWidgets.QLineEdit()
        self.web_query.setPlaceholderText("Search Wikipedia…")
        search_btn = QtWidgets.QPushButton("Search")
        search_btn.clicked.connect(self._search_web)
        form.addWidget(self.web_query)
        form.addWidget(search_btn)
        layout.addLayout(form)

        self.web_status = QtWidgets.QLabel("")
        self.web_status.setWordWrap(True)
        layout.addWidget(self.web_status)

        self.web_result = QtWidgets.QTextEdit()
        self.web_result.setReadOnly(True)
        layout.addWidget(self.web_result)

        return widget

    def _save_note(self) -> None:
        title = self.note_title.text().strip()
        content = self.note_content.toPlainText().strip()
        if not title or not content:
            QtWidgets.QMessageBox.warning(self, "Missing information", "Enter a title and content.")
            return
        summary = summarize_text(content)
        self.data.notes.append(Note(title=title, content=content, summary=summary))
        save_data(self.data)
        self.note_title.clear()
        self.note_content.clear()
        self._refresh_notes_list()
        self.summary_label.setText(f"Summary: {summary}")

    def _generate_summary(self) -> None:
        content = self.note_content.toPlainText().strip()
        if not content:
            QtWidgets.QMessageBox.information(self, "No content", "Add text to summarize.")
            return
        summary = summarize_text(content)
        self.summary_label.setText(f"Summary: {summary}")

    def _generate_ai_summary(self) -> None:
        content = self.note_content.toPlainText().strip()
        if not content:
            QtWidgets.QMessageBox.information(self, "No content", "Add text to summarize.")
            return
        if not has_openai_key():
            QtWidgets.QMessageBox.warning(
                self,
                "Missing OpenAI key",
                "Set OPENAI_API_KEY in your environment to use ChatGPT summaries.",
            )
            return
        try:
            summary = summarize_with_ai(content)
        except requests.RequestException:
            summary = "ChatGPT request failed. Check your internet connection."
        except (RuntimeError, ValueError, KeyError):
            summary = "ChatGPT response could not be processed."
        self.summary_label.setText(f"Summary: {summary}")

    def _generate_ai_text(self) -> None:
        if not has_openai_key():
            QtWidgets.QMessageBox.warning(
                self,
                "Missing OpenAI key",
                "Set OPENAI_API_KEY in your environment to use ChatGPT text generation.",
            )
            return
        try:
            text = generate_text("Write a haiku about cybersecurity.")
        except requests.RequestException:
            text = "ChatGPT request failed. Check your internet connection."
        except (RuntimeError, ValueError, KeyError):
            text = "ChatGPT response could not be processed."
        self.ai_text_output.setPlainText(text)

    def _generate_dictionary(self) -> None:
        if not self.data.notes:
            QtWidgets.QMessageBox.information(self, "No notes", "Add notes before generating a glossary.")
            return
        if not has_openai_key():
            QtWidgets.QMessageBox.warning(
                self,
                "Missing OpenAI key",
                "Set OPENAI_API_KEY in your environment to use ChatGPT glossary generation.",
            )
            return
        try:
            self.data.dictionary = dictionary_from_notes(self.data.notes)
        except requests.RequestException:
            QtWidgets.QMessageBox.warning(
                self, "Connection error", "ChatGPT request failed. Check your internet connection."
            )
            return
        except (RuntimeError, ValueError, KeyError, json.JSONDecodeError):
            QtWidgets.QMessageBox.warning(
                self, "Parse error", "ChatGPT response could not be processed."
            )
            return
        save_data(self.data)
        self._refresh_dictionary_list()

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

    def _generate_quiz(self) -> None:
        self.data.quizzes = build_quiz_from_notes(self.data.notes)
        save_data(self.data)
        self._refresh_quiz_list()

    def _generate_ai_quiz(self) -> None:
        if not self.data.notes:
            QtWidgets.QMessageBox.information(self, "No notes", "Add notes before generating a quiz.")
            return
        if not has_openai_key():
            QtWidgets.QMessageBox.warning(
                self,
                "Missing OpenAI key",
                "Set OPENAI_API_KEY in your environment to use ChatGPT quizzes.",
            )
            return
        try:
            self.data.quizzes = quiz_from_notes(self.data.notes)
        except requests.RequestException:
            QtWidgets.QMessageBox.warning(
                self, "Connection error", "ChatGPT request failed. Check your internet connection."
            )
            return
        except (RuntimeError, ValueError, KeyError, json.JSONDecodeError):
            QtWidgets.QMessageBox.warning(
                self, "Parse error", "ChatGPT response could not be processed."
            )
            return
        save_data(self.data)
        self._refresh_quiz_list()

    def _search_web(self) -> None:
        query = self.web_query.text().strip()
        if not query:
            return
        self.web_result.setPlainText("Searching…")
        self.web_status.setText("Checking connectivity…")
        try:
            requests.get("https://www.google.com", timeout=DEFAULT_REQUEST_TIMEOUT)
            self.web_status.setText("Online. Fetching Wikipedia summary.")
        except requests.RequestException:
            self.web_status.setText(
                "Network check failed. Wikipedia lookup may not work. Check firewall/proxy."
            )
        try:
            result = search_wikipedia(query)
        except requests.RequestException:
            result = "Connection error. Check your internet connection."
        self.web_result.setPlainText(result)
        self.web_status.setText("Done.")

    def _refresh_notes_list(self) -> None:
        self.notes_list.clear()
        for note in self.data.notes:
            preview = shorten(note.summary or note.content, width=80, placeholder="…")
            self.notes_list.addItem(f"{note.title}: {preview}")

    def _refresh_dictionary_list(self) -> None:
        self.dict_list.clear()
        for entry in self.data.dictionary:
            preview = shorten(entry.definition, width=80, placeholder="…")
            self.dict_list.addItem(f"{entry.term}: {preview}")

        if not self.data.dictionary:
            self.dict_list.addItem("No glossary terms yet.")

    def _refresh_calendar_list(self) -> None:
        self.calendar_list.clear()
        for entry in sorted(self.data.calendar, key=lambda item: item.day):
            preview = shorten(entry.details, width=60, placeholder="…")
            self.calendar_list.addItem(f"{entry.day.isoformat()} · {entry.title} — {preview}")

    def _refresh_quiz_list(self) -> None:
        self.quiz_list.clear()
        for quiz in self.data.quizzes:
            self.quiz_list.addItem(f"{quiz.prompt} | Answer: {quiz.answer}")

        if not self.data.quizzes:
            self.quiz_list.addItem("No quiz questions yet.")


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = CyberStudyApp()
    window.show()
    app.exec()
