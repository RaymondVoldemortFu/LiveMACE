import json
import requests


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    query = params.get("query") or params.get("q") or params.get("ingredient") or "pasta"
    limit = params.get("limit") or 1
    try:
        limit = max(1, min(5, int(limit)))
    except (TypeError, ValueError):
        limit = 1
    try:
        r = requests.get(
            "https://www.themealdb.com/api/json/v1/1/search.php",
            params={"s": str(query).strip()},
            timeout=15,
        )
        r.raise_for_status()
        j = r.json()
    except requests.RequestException as e:
        return _error(f"Recipe API error: {e}")
    meals = j.get("meals") or []
    results = []
    for m in meals[:limit]:
        ingredients = []
        for i in range(1, 21):
            ing = m.get(f"strIngredient{i}")
            measure = m.get(f"strMeasure{i}")
            if ing and ing.strip():
                ingredients.append(f"{measure or ''} {ing}".strip())
        results.append({
            "name": m.get("strMeal"),
            "category": m.get("strCategory"),
            "area": m.get("strArea"),
            "instructions": m.get("strInstructions"),
            "ingredients": ingredients,
            "thumbnail": m.get("strMealThumb"),
        })
    data = {"query": query, "recipes": results, "count": len(results)}
    return {"status": "ok", "error": None, "data": data}
