import random


RACES = ["Human", "Elf", "Dwarf", "Orc", "Halfling", "Gnome", "Tiefling"]
CLASSES = ["Warrior", "Mage", "Rogue", "Ranger", "Cleric", "Paladin", "Bard"]
TRAITS = ["Brave", "Cunning", "Loyal", "Curious", "Stoic", "Reckless", "Wise"]
WEAPONS = ["Sword", "Bow", "Staff", "Dagger", "Axe", "Spear"]


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    name = params.get("name")

    character = {
        "name": name or random.choice(["Arin", "Lysa", "Korin", "Mira", "Talen", "Sera"]),
        "race": random.choice(RACES),
        "class": random.choice(CLASSES),
        "trait": random.choice(TRAITS),
        "weapon": random.choice(WEAPONS),
        "level": random.randint(1, 20),
    }
    return {"status": "ok", "error": None, "data": character}
