from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict

from .models import CalendarEntry, DictionaryEntry, Note, QuizQuestion, StudyData

DATA_DIR = Path.home() / ".cyberstudy"
DATA_FILE = DATA_DIR / "data.json"


def get_data_path() -> Path:
    return DATA_FILE


def _serialize_date(value: date) -> str:
    return value.isoformat()


def _deserialize_date(value: str) -> date:
    return date.fromisoformat(value)


def load_data() -> StudyData:
    if not DATA_FILE.exists():
        return StudyData()
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return StudyData(
        notes=[Note(**note) for note in payload.get("notes", [])],
        dictionary=[DictionaryEntry(**entry) for entry in payload.get("dictionary", [])],
        calendar=[
            CalendarEntry(
                title=entry["title"],
                day=_deserialize_date(entry["day"]),
                details=entry.get("details", ""),
            )
            for entry in payload.get("calendar", [])
        ],
        quizzes=[QuizQuestion(**quiz) for quiz in payload.get("quizzes", [])],
    )


def save_data(data: StudyData) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "notes": [asdict(note) for note in data.notes],
        "dictionary": [asdict(entry) for entry in data.dictionary],
        "calendar": [
            {
                "title": entry.title,
                "day": _serialize_date(entry.day),
                "details": entry.details,
            }
            for entry in data.calendar
        ],
        "quizzes": [asdict(quiz) for quiz in data.quizzes],
    }
    DATA_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
