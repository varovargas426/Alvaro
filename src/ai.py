from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import List

import requests

from .models import DictionaryEntry, Note, QuizQuestion

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


def has_openai_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _call_openai(system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    response = requests.post(
        OPENAI_API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def summarize_with_ai(text: str) -> str:
    system_prompt = "You are a helpful study assistant. Summarize user notes clearly."
    user_prompt = (
        "Summarize the following study notes in 2-3 sentences."
        " Keep it concise and focused on cybersecurity learning.\n\n"
        f"NOTES:\n{text}"
    )
    return _call_openai(system_prompt, user_prompt)


def dictionary_from_notes(notes: List[Note]) -> List[DictionaryEntry]:
    system_prompt = "You are a helpful study assistant that extracts glossary terms."
    note_payload = [asdict(note) for note in notes]
    user_prompt = (
        "Extract a glossary from these notes. Return ONLY valid JSON."
        " The JSON must be an array of objects with fields: term, definition."
        " Keep definitions short (1-2 sentences).\n\n"
        f"NOTES_JSON:\n{json.dumps(note_payload)}"
    )
    content = _call_openai(system_prompt, user_prompt)
    data = json.loads(content)
    return [DictionaryEntry(term=item["term"], definition=item["definition"]) for item in data]


def quiz_from_notes(notes: List[Note], *, max_questions: int = 10) -> List[QuizQuestion]:
    system_prompt = "You are a helpful study assistant that creates quiz questions."
    note_payload = [asdict(note) for note in notes]
    user_prompt = (
        "Create a quiz from these notes. Return ONLY valid JSON."
        " The JSON must be an array of objects with fields: prompt, answer."
        f" Limit to {max_questions} questions total.\n\n"
        f"NOTES_JSON:\n{json.dumps(note_payload)}"
    )
    content = _call_openai(system_prompt, user_prompt, temperature=0.3)
    data = json.loads(content)
    return [QuizQuestion(prompt=item["prompt"], answer=item["answer"]) for item in data]
