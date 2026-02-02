from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List


@dataclass
class Note:
    title: str
    content: str
    summary: str = ""


@dataclass
class DictionaryEntry:
    term: str
    definition: str


@dataclass
class CalendarEntry:
    title: str
    day: date
    details: str = ""


@dataclass
class QuizQuestion:
    prompt: str
    answer: str


@dataclass
class StudyData:
    notes: List[Note] = field(default_factory=list)
    dictionary: List[DictionaryEntry] = field(default_factory=list)
    calendar: List[CalendarEntry] = field(default_factory=list)
    quizzes: List[QuizQuestion] = field(default_factory=list)
