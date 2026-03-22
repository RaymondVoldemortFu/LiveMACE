from collections import Counter


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _clean_text(text: str, ignorecase: bool, ignorespaces: bool) -> str:
    if ignorecase:
        text = text.lower()
    if ignorespaces:
        text = "".join(text.split())
    return text


def run(params: dict) -> dict:
    params = params or {}
    text1 = params.get("text1")
    text2 = params.get("text2")
    if not text1 or not isinstance(text1, str):
        return _error("Missing required parameter: text1")
    if not text2 or not isinstance(text2, str):
        return _error("Missing required parameter: text2")

    ignorecase = params.get("ignorecase", True)
    ignorespaces = params.get("ignorespaces", True)

    cleaned_text1 = _clean_text(text1, bool(ignorecase), bool(ignorespaces))
    cleaned_text2 = _clean_text(text2, bool(ignorecase), bool(ignorespaces))

    sorted_text1 = "".join(sorted(cleaned_text1))
    sorted_text2 = "".join(sorted(cleaned_text2))

    is_anagram = len(cleaned_text1) == len(cleaned_text2) and sorted_text1 == sorted_text2

    counter1 = Counter(cleaned_text1)
    counter2 = Counter(cleaned_text2)
    common = sum((counter1 & counter2).values())
    max_len = max(len(cleaned_text1), len(cleaned_text2))
    similarity_percentage = 100 if max_len == 0 else round(common / max_len * 100)

    return {
        "status": "ok",
        "error": None,
        "data": {
            "text1": text1,
            "text2": text2,
            "is_anagram": is_anagram,
            "cleaned_text1": cleaned_text1,
            "cleaned_text2": cleaned_text2,
            "sorted_text1": sorted_text1,
            "sorted_text2": sorted_text2,
            "length_text1": len(cleaned_text1),
            "length_text2": len(cleaned_text2),
            "similarity_percentage": similarity_percentage,
        },
    }
