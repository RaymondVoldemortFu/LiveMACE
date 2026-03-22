import random
import time
import uuid
from urllib.parse import quote


def _generate_solution(length: int = 5) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def run(params: dict) -> dict:
    solution = _generate_solution()
    captcha_id = str(uuid.uuid4())
    expires = int((time.time() + 300) * 1000)
    text = quote(solution)
    download_url = f"https://dummyimage.com/200x80/ffffff/000000.png&text={text}"

    return {
        "status": "ok",
        "error": None,
        "data": {
            "id": captcha_id,
            "expires": expires,
            "solution": solution,
            "downloadURL": download_url,
        },
    }
