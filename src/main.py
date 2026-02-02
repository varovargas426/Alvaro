from __future__ import annotations

import re
from collections import Counter
from datetime import date
from textwrap import shorten
from typing import List

import requests
from PySide6 import QtCore, QtWidgets

from .models import CalendarEntry, DictionaryEntry, Note, QuizQuestion, StudyData
from .storage import load_data, save_data

SUMMARY_MAX_SENTENCES = 3


def summarize_text(text: str) -> str:
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence.strip()]
    if not sentences:
        return ""
    if len(sentences) <= SUMMARY_MAX_SENTENCES:
        return " ".join(sentences)
    return " ".join(sentences[:SUMMARY_MAX_SENTENCES])


def extract_keywords(text: str, limit: int = 8) -> List[str]:
    tokens = re.findall(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9_-]{4,}", text.lower())
    stopwords = {"para", "como", "pero", "este", "esta", "este", "esta", "porque", "sobre", "entre"}
    filtered = [token for token in tokens if token not in stopwords]
    return [word for word, _ in Counter(filtered).most_common(limit)]


def build_quiz_from_notes(notes: List[Note]) -> List[QuizQuestion]:
    questions: List[QuizQuestion] = []
    for note in notes:
        keywords = extract_keywords(note.content, limit=4)
        for keyword in keywords:
            prompt = f"¿Qué significa '{keyword}' en el contexto de {note.title}?"
            answer = f"Revisa tus notas sobre {note.title} para definir '{keyword}'."
            questions.append(QuizQuestion(prompt=prompt, answer=answer))
    return questions


def search_wikipedia(query: str) -> str:
    if not query.strip():
        return ""
    response = requests.get(
        "https://es.wikipedia.org/api/rest_v1/page/summary/" + requests.utils.quote(query),
        timeout=10,
    )
    if response.status_code != 200:
        return "No se encontró información en Wikipedia."
    data = response.json()
    return data.get("extract", "No se encontró información.")


class CyberStudyApp(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CyberStudy Coach")
        self.resize(1000, 700)
        self.data = load_data()

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_notes_tab(), "Notas")
        tabs.addTab(self._build_dictionary_tab(), "Diccionario")
        tabs.addTab(self._build_calendar_tab(), "Calendario")
        tabs.addTab(self._build_quiz_tab(), "Quizzes")
        tabs.addTab(self._build_web_tab(), "Web")
        self.setCentralWidget(tabs)

    def _build_notes_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        form = QtWidgets.QFormLayout()
        self.note_title = QtWidgets.QLineEdit()
        self.note_content = QtWidgets.QTextEdit()
        form.addRow("Título:", self.note_title)
        form.addRow("Contenido:", self.note_content)
        layout.addLayout(form)

        button_row = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton("Guardar nota")
        save_btn.clicked.connect(self._save_note)
        summary_btn = QtWidgets.QPushButton("Generar resumen")
        summary_btn.clicked.connect(self._generate_summary)
        button_row.addWidget(save_btn)
        button_row.addWidget(summary_btn)
        layout.addLayout(button_row)

        self.summary_label = QtWidgets.QLabel("Resumen: (vacío)")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.notes_list = QtWidgets.QListWidget()
        self._refresh_notes_list()
        layout.addWidget(self.notes_list)

        return widget

    def _build_dictionary_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        form = QtWidgets.QFormLayout()
        self.dict_term = QtWidgets.QLineEdit()
        self.dict_definition = QtWidgets.QTextEdit()
        form.addRow("Término:", self.dict_term)
        form.addRow("Definición:", self.dict_definition)
        layout.addLayout(form)

        save_btn = QtWidgets.QPushButton("Guardar término")
        save_btn.clicked.connect(self._save_dictionary_entry)
        layout.addWidget(save_btn)

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
        form.addRow("Título:", self.calendar_title)
        form.addRow("Fecha:", self.calendar_date)
        form.addRow("Detalles:", self.calendar_details)
        layout.addLayout(form)

        save_btn = QtWidgets.QPushButton("Guardar evento")
        save_btn.clicked.connect(self._save_calendar_entry)
        layout.addWidget(save_btn)

        self.calendar_list = QtWidgets.QListWidget()
        self._refresh_calendar_list()
        layout.addWidget(self.calendar_list)

        return widget

    def _build_quiz_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        generate_btn = QtWidgets.QPushButton("Generar quiz desde notas")
        generate_btn.clicked.connect(self._generate_quiz)
        layout.addWidget(generate_btn)

        self.quiz_list = QtWidgets.QListWidget()
        self._refresh_quiz_list()
        layout.addWidget(self.quiz_list)

        return widget

    def _build_web_tab(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)

        form = QtWidgets.QHBoxLayout()
        self.web_query = QtWidgets.QLineEdit()
        self.web_query.setPlaceholderText("Buscar en Wikipedia…")
        search_btn = QtWidgets.QPushButton("Buscar")
        search_btn.clicked.connect(self._search_web)
        form.addWidget(self.web_query)
        form.addWidget(search_btn)
        layout.addLayout(form)

        self.web_result = QtWidgets.QTextEdit()
        self.web_result.setReadOnly(True)
        layout.addWidget(self.web_result)

        return widget

    def _save_note(self) -> None:
        title = self.note_title.text().strip()
        content = self.note_content.toPlainText().strip()
        if not title or not content:
            QtWidgets.QMessageBox.warning(self, "Falta información", "Completa título y contenido.")
            return
        summary = summarize_text(content)
        self.data.notes.append(Note(title=title, content=content, summary=summary))
        save_data(self.data)
        self.note_title.clear()
        self.note_content.clear()
        self._refresh_notes_list()
        self.summary_label.setText(f"Resumen: {summary}")

    def _generate_summary(self) -> None:
        content = self.note_content.toPlainText().strip()
        if not content:
            QtWidgets.QMessageBox.information(self, "Sin contenido", "Agrega texto para resumir.")
            return
        summary = summarize_text(content)
        self.summary_label.setText(f"Resumen: {summary}")

    def _save_dictionary_entry(self) -> None:
        term = self.dict_term.text().strip()
        definition = self.dict_definition.toPlainText().strip()
        if not term or not definition:
            QtWidgets.QMessageBox.warning(self, "Falta información", "Completa término y definición.")
            return
        self.data.dictionary.append(DictionaryEntry(term=term, definition=definition))
        save_data(self.data)
        self.dict_term.clear()
        self.dict_definition.clear()
        self._refresh_dictionary_list()

    def _save_calendar_entry(self) -> None:
        title = self.calendar_title.text().strip()
        if not title:
            QtWidgets.QMessageBox.warning(self, "Falta información", "Agrega un título.")
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

    def _search_web(self) -> None:
        query = self.web_query.text().strip()
        if not query:
            return
        self.web_result.setPlainText("Buscando…")
        try:
            result = search_wikipedia(query)
        except requests.RequestException:
            result = "Error de conexión. Revisa tu conexión a internet."
        self.web_result.setPlainText(result)

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

    def _refresh_calendar_list(self) -> None:
        self.calendar_list.clear()
        for entry in sorted(self.data.calendar, key=lambda item: item.day):
            preview = shorten(entry.details, width=60, placeholder="…")
            self.calendar_list.addItem(f"{entry.day.isoformat()} · {entry.title} — {preview}")

    def _refresh_quiz_list(self) -> None:
        self.quiz_list.clear()
        for quiz in self.data.quizzes:
            self.quiz_list.addItem(f"{quiz.prompt} | Respuesta: {quiz.answer}")


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = CyberStudyApp()
    window.show()
    app.exec()
