import random
from typing import List


RANKS = ["Ace", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King"]
SUITS = ["Spades", "Hearts", "Diamonds", "Clubs"]
SUIT_SYMBOLS = {"Spades": "♠", "Hearts": "♥", "Diamonds": "♦", "Clubs": "♣"}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _build_deck(include_jokers: bool) -> List[dict]:
    cards = []
    for suit in SUITS:
        for rank in RANKS:
            short_rank = rank[0] if rank not in {"10"} else "10"
            short = f"{short_rank}{SUIT_SYMBOLS[suit]}"
            cards.append(
                {
                    "rank": rank,
                    "suit": suit,
                    "card": f"{rank} of {suit}",
                    "short": short,
                }
            )
    if include_jokers:
        cards.append({"rank": "Joker", "suit": "Joker", "card": "Joker", "short": "Joker"})
        cards.append({"rank": "Joker", "suit": "Joker", "card": "Joker", "short": "Joker"})
    return cards


def _riffle_shuffle(cards: List[dict]) -> List[dict]:
    cut = len(cards) // 2
    left = cards[:cut]
    right = cards[cut:]
    shuffled = []
    while left or right:
        if left and (not right or random.random() < 0.5):
            shuffled.append(left.pop(0))
        if right and (not left or random.random() < 0.5):
            shuffled.append(right.pop(0))
    return shuffled


def run(params: dict) -> dict:
    params = params or {}
    decks = params.get("decks", 1)
    jokers = bool(params.get("jokers", False))
    method = str(params.get("method", "fisher-yates")).lower()

    try:
        decks = int(decks)
    except (TypeError, ValueError):
        return _error("Invalid decks: must be an integer between 1 and 10")

    if decks < 1 or decks > 10:
        return _error("Invalid decks: must be between 1 and 10")

    if method not in {"fisher-yates", "riffle"}:
        return _error("Invalid method: must be fisher-yates or riffle")

    cards = []
    for _ in range(decks):
        cards.extend(_build_deck(jokers))

    if method == "riffle":
        cards = _riffle_shuffle(cards)
    else:
        random.shuffle(cards)

    top_card = cards[0] if cards else None
    bottom_card = cards[-1] if cards else None

    return {
        "status": "ok",
        "error": None,
        "data": {
            "total_cards": len(cards),
            "decks_used": decks,
            "includes_jokers": jokers,
            "shuffle_method": method,
            "cards": cards,
            "top_card": top_card,
            "bottom_card": bottom_card,
            "sample_hand": cards[:5],
        },
    }
