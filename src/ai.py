from __future__ import annotations

import json
import os
from typing import List, Optional

from openai import OpenAI

from .models import DictionaryEntry, Note, QuizQuestion

# Modelo por defecto (puedes cambiarlo por env var si quieres)
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-nano")
MAX_NOTE_CHARS = 12000
CHUNK_MAX_CHARS = 4000
MAX_NOTES_FOR_AI = 20

# Cliente único (lee OPENAI_API_KEY automáticamente del entorno)
client = OpenAI()


class AiJsonParseError(ValueError):
    def __init__(self, message: str, raw_content: str) -> None:
        super().__init__(message)
        self.raw_content = raw_content


def has_openai_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def _model_supports_temperature(model: str) -> bool:
    """
    Algunos modelos (p. ej. gpt-5-*) no aceptan 'temperature' en Responses API.
    Mantén la regla simple y segura: no enviar temperature en gpt-5-*.
    """
    return not model.startswith("gpt-5")


def _response_text(system_prompt: str, user_prompt: str, temperature: Optional[float] = None) -> str:
    params = {
        "model": DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    # Solo manda temperature si el modelo lo soporta
    if temperature is not None and _model_supports_temperature(DEFAULT_MODEL):
        params["temperature"] = temperature

    response = client.responses.create(**params)
    return response.output_text


def _truncate_for_log(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]..."


def normalize_tags(tags: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for tag in tags:
        cleaned = tag.strip().lower().replace(" ", "-")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return sorted(normalized)


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> List[str]:
    paragraphs = [para.strip() for para in text.splitlines() if para.strip()]
    if not paragraphs:
        return []
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for paragraph in paragraphs:
        para_len = len(paragraph)
        if current and current_len + para_len + 2 > max_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += para_len + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _truncate_text(text: str, limit: int = MAX_NOTE_CHARS) -> str:
    return text if len(text) <= limit else text[:limit]


def _condensed_notes(notes: List[Note]) -> str:
    recent_notes = notes[-MAX_NOTES_FOR_AI:]
    combined = "\n\n".join(f"{note.title}\n{note.content}" for note in recent_notes)
    return _truncate_text(combined)


def summarize_with_ai(text: str) -> str:
    system_prompt = "You are a helpful study assistant. Summarize user notes clearly."
    truncated = _truncate_text(text)
    chunks = chunk_text(truncated)
    if not chunks:
        return ""
    if len(chunks) == 1:
        user_prompt = (
            "Summarize the following study notes in 2-3 sentences. "
            "Keep it concise and focused on cybersecurity learning.\n\n"
            f"NOTES:\n{chunks[0]}"
        )
        return _response_text(system_prompt, user_prompt)
    partial_summaries = []
    for chunk in chunks:
        user_prompt = (
            "Summarize the following study notes in 2-3 sentences. "
            "Keep it concise and focused on cybersecurity learning.\n\n"
            f"NOTES:\n{chunk}"
        )
        partial_summaries.append(_response_text(system_prompt, user_prompt))
    summaries_text = "\n".join(partial_summaries)
    final_prompt = (
        "Combine the following partial summaries into a single concise summary.\n\n"
        f"SUMMARIES:\n{summaries_text}"
    )
    return _response_text(system_prompt, final_prompt)


def dictionary_from_notes(notes: List[Note]) -> List[DictionaryEntry]:
    content = dictionary_response_text(notes)
    return parse_dictionary_response(content)


def dictionary_response_text(notes: List[Note]) -> str:
    system_prompt = "You are a helpful study assistant that extracts glossary terms."
    condensed_notes = _condensed_notes(notes)
    user_prompt = (
        "Extract a glossary from these notes. Return ONLY valid JSON. "
        "The JSON must be an array of objects with fields: term, definition. "
        "Keep definitions short (1-2 sentences).\n\n"
        f"NOTES:\n{condensed_notes}"
    )

    return _response_text(system_prompt, user_prompt)


def parse_dictionary_response(content: str) -> List[DictionaryEntry]:
    # Nota: a veces el modelo devuelve texto extra; si pasa, lo arreglamos luego con parsing robusto.
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AiJsonParseError(
            "AI response JSON parse failed. See logs/app.log.",
            _truncate_for_log(content),
        ) from exc
    return [DictionaryEntry(term=item["term"], definition=item["definition"]) for item in data]


def quiz_from_notes(notes: List[Note], *, max_questions: int = 10) -> List[QuizQuestion]:
    content = quiz_response_text(notes, max_questions=max_questions)
    return parse_quiz_response(content)


def _is_valid_quiz_json(content: str) -> bool:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(data, list)


def quiz_response_text(notes: List[Note], *, max_questions: int = 10) -> str:
    system_prompt = "You are a helpful study assistant that creates quiz questions."
    condensed_notes = _condensed_notes(notes)
    base_prompt = (
        "Create a multiple-choice quiz from these notes. Return ONLY valid JSON. "
        "The JSON must be an array of objects with fields: prompt, choices, correct_index, explanation. "
        "choices must be an array of 4 strings. correct_index must be 0-3. "
        "explanation must be 1-2 short sentences.\n\n"
        f"Limit to {max_questions} questions total.\n\n"
        f"NOTES:\n{condensed_notes}"
    )

    content = _response_text(system_prompt, base_prompt, temperature=0.3)
    if _is_valid_quiz_json(content):
        return content
    strict_prompt = (
        "Return ONLY a valid JSON array of objects. "
        "No prose, no markdown, no code fences. "
        "Each object must have: prompt, choices (4 strings), correct_index (0-3), explanation.\n\n"
        f"Limit to {max_questions} questions total.\n\n"
        f"NOTES:\n{condensed_notes}"
    )
    return _response_text(system_prompt, strict_prompt, temperature=0.3)


def parse_quiz_response(content: str) -> List[QuizQuestion]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AiJsonParseError(
            "AI response JSON parse failed. See logs/app.log.",
            _truncate_for_log(content),
        ) from exc
    questions: List[QuizQuestion] = []
    for item in data:
        choices = [str(choice) for choice in item["choices"]]
        correct_index = int(item["correct_index"])
        explanation = str(item.get("explanation", ""))
        answer = choices[correct_index] if 0 <= correct_index < len(choices) else ""
        questions.append(
            QuizQuestion(
                prompt=str(item["prompt"]),
                answer=answer,
                choices=choices,
                correct_index=correct_index,
                explanation=explanation,
            )
        )
    return questions


def suggest_tags(title: str, content: str) -> List[str]:
    system_prompt = "You are a helpful study assistant that labels notes with concise tags."
    user_prompt = (
        "Generate 3-6 tags for this note. Return ONLY a valid JSON array of strings.\n\n"
        f"TITLE:\n{title}\n\nCONTENT:\n{content}"
    )
    raw = _response_text(system_prompt, user_prompt)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AiJsonParseError(
            "AI response JSON parse failed. See logs/app.log.",
            _truncate_for_log(raw),
        ) from exc
    return normalize_tags([str(tag) for tag in data])
