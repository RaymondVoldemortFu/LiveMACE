import re

import requests


BIBLE_API_URL = "https://bible-api.com/"
RANDOM_VERSE_URL = "https://labs.bible.org/api/"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _normalize_book(book: str) -> str:
    return " ".join(book.strip().split())


def run(params: dict) -> dict:
    params = params or {}
    book = params.get("book")
    if not book or not isinstance(book, str):
        return _error("Missing required parameter: book")
    book = _normalize_book(book)

    if book.lower() == "random":
        try:
            response = requests.get(
                RANDOM_VERSE_URL,
                params={"passage": "random", "type": "json"},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            return _error(f"Bible service error: {exc}")
        except ValueError:
            return _error("Bible service returned invalid JSON")

        if not payload:
            return _error("Bible service returned empty response")
        verse_data = payload[0]
        return {
            "status": "ok",
            "error": None,
            "data": {
                "text": verse_data.get("text", "").strip(),
                "book": verse_data.get("bookname", ""),
                "abbr": verse_data.get("bookname", "")[:2].lower(),
                "chapter": int(verse_data.get("chapter", 0)) if verse_data.get("chapter") else 0,
                "verses": [int(verse_data.get("verse", 0))] if verse_data.get("verse") else [],
                "version": "NET",
            },
        }

    chapter = params.get("chapter")
    verse = params.get("verse")
    if chapter is None:
        return _error("Missing required parameter: chapter")

    try:
        chapter = int(chapter)
        if verse is not None:
            verse = int(verse)
    except (TypeError, ValueError):
        return _error("Invalid chapter or verse")

    reference = f"{book} {chapter}"
    if verse:
        reference = f"{reference}:{verse}"

    try:
        response = requests.get(f"{BIBLE_API_URL}{reference}", timeout=10)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        return _error(f"Bible service error: {exc}")
    except ValueError:
        return _error("Bible service returned invalid JSON")

    verses = payload.get("verses") or []
    verse_numbers = [v.get("verse") for v in verses if v.get("verse") is not None]
    book_id = verses[0].get("book_id") if verses else ""
    abbr = book_id.lower() if isinstance(book_id, str) else ""

    return {
        "status": "ok",
        "error": None,
        "data": {
            "text": payload.get("text", "").strip(),
            "book": payload.get("book_name") or book,
            "abbr": abbr or book[:2].lower(),
            "chapter": payload.get("chapter") or chapter,
            "verses": verse_numbers or ([verse] if verse else []),
            "version": payload.get("translation_name", "KJV"),
        },
    }
