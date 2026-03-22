def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _murmur3_32(key: bytes, seed: int = 0) -> int:
    length = len(key)
    nblocks = length // 4
    h1 = seed
    c1 = 0xCC9E2D51
    c2 = 0x1B873593
    for i in range(nblocks):
        k1 = int.from_bytes(key[i * 4 : (i + 1) * 4], "little") & 0xFFFFFFFF
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
        h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
        h1 = (h1 * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = key[nblocks * 4 :]
    k1 = 0
    if len(tail) >= 3:
        k1 ^= tail[2] << 16
    if len(tail) >= 2:
        k1 ^= tail[1] << 8
    if len(tail) >= 1:
        k1 ^= tail[0]
        k1 = (k1 * c1) & 0xFFFFFFFF
        k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
        k1 = (k1 * c2) & 0xFFFFFFFF
        h1 ^= k1
    h1 ^= length
    h1 ^= h1 >> 16
    h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
    h1 ^= h1 >> 13
    h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
    h1 ^= h1 >> 16
    return h1


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    seed = params.get("seed", 0)
    if text is None:
        return _error("Missing required parameter: text")
    try:
        seed = int(seed) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return _error("Invalid seed")
    key = str(text).encode("utf-8")
    h = _murmur3_32(key, seed)
    data = {"hash_hex": f"{h:08x}", "hash_int": h, "seed": seed}
    return {"status": "ok", "error": None, "data": data}
