import requests


COCKTAIL_BASE_URL = "https://www.thecocktaildb.com/api/json/v1/1"


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def _extract_ingredients(drink: dict) -> list:
    ingredients = []
    for idx in range(1, 16):
        ingredient = drink.get(f"strIngredient{idx}")
        measure = drink.get(f"strMeasure{idx}")
        if not ingredient:
            continue
        if measure:
            ingredients.append({"unit": "", "amount": "", "ingredient": ingredient.strip(), "label": measure.strip()})
        else:
            ingredients.append({"special": ingredient.strip()})
    return ingredients


def _format_drink(drink: dict) -> dict:
    return {
        "name": drink.get("strDrink"),
        "glass": drink.get("strGlass"),
        "category": drink.get("strCategory"),
        "ingredients": _extract_ingredients(drink),
        "garnish": drink.get("strDrinkAlternate"),
        "preparation": drink.get("strInstructions"),
    }


def _lookup_by_id(drink_id: str) -> dict:
    response = requests.get(f"{COCKTAIL_BASE_URL}/lookup.php", params={"i": drink_id}, timeout=15)
    response.raise_for_status()
    payload = response.json()
    drinks = payload.get("drinks") or []
    return drinks[0] if drinks else {}


def run(params: dict) -> dict:
    params = params or {}
    name = params.get("name")
    ingredient = params.get("ingredient")
    random_flag = params.get("random")

    try:
        if random_flag:
            response = requests.get(f"{COCKTAIL_BASE_URL}/random.php", timeout=15)
            response.raise_for_status()
            payload = response.json()
            drinks = payload.get("drinks") or []
            cocktails = [_format_drink(drinks[0])] if drinks else []
            filtered_on = "random"
        elif ingredient:
            response = requests.get(f"{COCKTAIL_BASE_URL}/filter.php", params={"i": ingredient}, timeout=15)
            response.raise_for_status()
            payload = response.json()
            drinks = payload.get("drinks") or []
            cocktails = []
            for drink in drinks[:10]:
                detail = _lookup_by_id(drink.get("idDrink"))
                if detail:
                    cocktails.append(_format_drink(detail))
            filtered_on = "ingredient"
        else:
            response = requests.get(f"{COCKTAIL_BASE_URL}/search.php", params={"s": name or ""}, timeout=15)
            response.raise_for_status()
            payload = response.json()
            drinks = payload.get("drinks") or []
            cocktails = [_format_drink(drink) for drink in drinks]
            filtered_on = "name"
    except requests.RequestException as exc:
        return _error(f"Cocktail service error: {exc}")
    except ValueError:
        return _error("Cocktail service returned invalid JSON")

    return {
        "status": "ok",
        "error": None,
        "data": {
            "count": len(cocktails),
            "filteredOn": filtered_on,
            "cocktails": cocktails,
        },
    }
