# Minimal periodic table (symbol, name, number, mass). Source: public data.
ELEMENTS = [
    {"symbol": "H", "name": "Hydrogen", "number": 1, "atomic_mass": 1.008},
    {"symbol": "He", "name": "Helium", "number": 2, "atomic_mass": 4.003},
    {"symbol": "Li", "name": "Lithium", "number": 3, "atomic_mass": 6.941},
    {"symbol": "Be", "name": "Beryllium", "number": 4, "atomic_mass": 9.012},
    {"symbol": "B", "name": "Boron", "number": 5, "atomic_mass": 10.81},
    {"symbol": "C", "name": "Carbon", "number": 6, "atomic_mass": 12.01},
    {"symbol": "N", "name": "Nitrogen", "number": 7, "atomic_mass": 14.01},
    {"symbol": "O", "name": "Oxygen", "number": 8, "atomic_mass": 16.00},
    {"symbol": "F", "name": "Fluorine", "number": 9, "atomic_mass": 19.00},
    {"symbol": "Ne", "name": "Neon", "number": 10, "atomic_mass": 20.18},
    {"symbol": "Na", "name": "Sodium", "number": 11, "atomic_mass": 22.99},
    {"symbol": "Mg", "name": "Magnesium", "number": 12, "atomic_mass": 24.31},
    {"symbol": "Al", "name": "Aluminum", "number": 13, "atomic_mass": 26.98},
    {"symbol": "Si", "name": "Silicon", "number": 14, "atomic_mass": 28.09},
    {"symbol": "P", "name": "Phosphorus", "number": 15, "atomic_mass": 30.97},
    {"symbol": "S", "name": "Sulfur", "number": 16, "atomic_mass": 32.07},
    {"symbol": "Cl", "name": "Chlorine", "number": 17, "atomic_mass": 35.45},
    {"symbol": "Ar", "name": "Argon", "number": 18, "atomic_mass": 39.95},
    {"symbol": "K", "name": "Potassium", "number": 19, "atomic_mass": 39.10},
    {"symbol": "Fe", "name": "Iron", "number": 26, "atomic_mass": 55.85},
    {"symbol": "Cu", "name": "Copper", "number": 29, "atomic_mass": 63.55},
    {"symbol": "Zn", "name": "Zinc", "number": 30, "atomic_mass": 65.38},
    {"symbol": "Ag", "name": "Silver", "number": 47, "atomic_mass": 107.9},
    {"symbol": "Au", "name": "Gold", "number": 79, "atomic_mass": 197.0},
    {"symbol": "Pb", "name": "Lead", "number": 82, "atomic_mass": 207.2},
]
# Fill 1-18 for lookup by number
BY_NUMBER = {e["number"]: e for e in ELEMENTS}
BY_SYMBOL = {e["symbol"]: e for e in ELEMENTS}
BY_NAME = {e["name"].lower(): e for e in ELEMENTS}


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    element = params.get("element")
    if element is None or element == "":
        data = {"elements": ELEMENTS, "count": len(ELEMENTS)}
        return {"status": "ok", "error": None, "data": data}
    key = str(element).strip()
    if key.isdigit():
        e = BY_NUMBER.get(int(key))
    else:
        e = BY_SYMBOL.get(key) or BY_NAME.get(key.lower())
    if not e:
        return _error("Element not found")
    return {"status": "ok", "error": None, "data": e}
