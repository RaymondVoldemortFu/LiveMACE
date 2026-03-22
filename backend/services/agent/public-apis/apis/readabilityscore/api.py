import re


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _count_syllables(word: str) -> int:
    word = word.lower()
    if not word or not word.isalpha():
        return 0
    vowels = "aeiouy"
    count = 0
    prev_v = False
    for c in word:
        v = c in vowels
        if v and not prev_v:
            count += 1
        prev_v = v
    if word.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None or not str(text).strip():
        return _error("Missing required parameter: text")
    text = str(text).strip()
    words = re.findall(r"[A-Za-z]+", text)
    sentences = max(1, len(re.findall(r"[.!?]+", text)) or 1)
    word_count = len(words)
    syllable_count = sum(_count_syllables(w) for w in words)
    if word_count == 0:
        data = {"flesch_reading_ease": 0, "flesch_kincaid_grade": 0, "word_count": 0, "sentence_count": 0, "syllable_count": 0}
        return {"status": "ok", "error": None, "data": data}
    # Flesch Reading Ease: 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
    flesch_ease = 206.835 - 1.015 * (word_count / sentences) - 84.6 * (syllable_count / word_count)
    flesch_ease = max(0, min(100, round(flesch_ease, 2)))
    # Flesch-Kincaid Grade Level
    fk_grade = 0.39 * (word_count / sentences) + 11.8 * (syllable_count / word_count) - 15.59
    fk_grade = max(0, round(fk_grade, 2))
    data = {
        "flesch_reading_ease": flesch_ease,
        "flesch_kincaid_grade": fk_grade,
        "word_count": word_count,
        "sentence_count": sentences,
        "syllable_count": syllable_count,
    }
    return {"status": "ok", "error": None, "data": data}
