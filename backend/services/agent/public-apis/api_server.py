"""
API Server: dynamically loads all apis/*/api.py (excluding UNSUPPORTED_APIS)
and exposes them via POST /v1/<name> with JSON body as params.
"""
import builtins
import importlib.util
import json
import os
import sys
from functools import lru_cache

# Ensure project root is on path and load unsupported list
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from unsupported_apis import UNSUPPORTED_APIS
except ImportError:
    UNSUPPORTED_APIS = []

APIS_DIR = os.path.join(ROOT, "apis")


def _wave3_api_unavailable(name):
    # The validated Wave3 provider supports chat completions, not image generation.
    return (
        os.getenv("WAVE3_PRODUCTION", "false").lower() == "true"
        and name in {"markdowntoimage", "imagetotext"}
    )


@lru_cache(maxsize=1)
def _load_config():
    """Keep the generated tools' config distinct from the host's config package."""
    spec = importlib.util.spec_from_file_location(
        "public_apis_runtime_config", os.path.join(ROOT, "config.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tool_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "config" and level == 0:
        return _load_config()
    return builtins.__import__(name, globals, locals, fromlist, level)


def _discover_available():
    """Return list of API names that have api.py and are not unsupported."""
    out = []
    for name in sorted(os.listdir(APIS_DIR)):
        if name.startswith("."):
            continue
        path = os.path.join(APIS_DIR, name, "api.py")
        if os.path.isfile(path) and name not in UNSUPPORTED_APIS and not _wave3_api_unavailable(name):
            out.append(name)
    return out


def _load_run(name):
    """Load api module for name and return its run function, or None on failure."""
    if _wave3_api_unavailable(name):
        return None
    path = os.path.join(APIS_DIR, name, "api.py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location(f"api_{name}", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Override only this generated module's import resolution, never sys.modules['config'].
    mod.__dict__["__builtins__"] = {**vars(builtins), "__import__": _tool_import}
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        return None
    return getattr(mod, "run", None)


def _run_api(name, params):
    """Call run(params) for the given API name. Returns dict with status/error/data."""
    if _wave3_api_unavailable(name):
        return {"status": "error", "error": "API is unavailable for the Wave3 model provider", "data": None}
    run_fn = _load_run(name)
    if run_fn is None:
        return {"status": "error", "error": "API not found or failed to load", "data": None}
    try:
        return run_fn(params or {})
    except Exception as e:
        return {"status": "error", "error": str(e), "data": None}


# Lazy Flask app so we don't require Flask at import time
_app = None


def get_app():
    global _app
    if _app is None:
        try:
            from flask import Flask, request, jsonify
        except ImportError:
            raise RuntimeError("Flask is required. Install with: pip install flask")

        app = Flask(__name__)

        @app.route("/v1", methods=["GET"])
        @app.route("/v1/", methods=["GET"])
        def list_apis():
            """List all available API names."""
            return jsonify({"apis": _discover_available()})

        @app.route("/v1/tools", methods=["GET"])
        def tools_schema():
            """OpenAI-compatible tools schema for all available APIs."""
            return jsonify(get_openai_tools())

        @app.route("/v1/<name>", methods=["POST", "GET"])
        def call_api(name):
            """Invoke API by name. POST: JSON body = params. GET: query string as params."""
            if name in UNSUPPORTED_APIS:
                return jsonify({"status": "error", "error": "API is not available", "data": None}), 400
            if request.method == "POST":
                try:
                    params = request.get_json(silent=True) or {}
                except Exception:
                    params = {}
            else:
                params = dict(request.args)
            result = _run_api(name, params)
            status_code = 200
            if result.get("status") == "error":
                status_code = 400
            return jsonify(result), status_code

        _app = app
    return _app


def get_openai_tools():
    """Return list of OpenAI-style tool schemas for all available APIs."""
    available = _discover_available()
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": _get_description(name),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": True,
                },
            },
        }
        for name in available
    ]


def build_tool_registry(Tool, ToolRegistry):
    """
    Build a ToolRegistry with one Tool per available API.
    Compatible with::
        class Tool: name, description, parameters, func  (func(**kwargs) -> result)
        class ToolRegistry: register(tool), get(name), openai_tools
    Usage::
        from services.agent.tools import Tool, ToolRegistry
        from api_server import build_tool_registry
        registry = build_tool_registry(Tool, ToolRegistry)
        registry.get("moonposition")(lat=40, lon=-74)
    """
    registry = ToolRegistry()
    for name in _discover_available():
        run_fn = _load_run(name)
        if run_fn is None:
            continue

        def _make_func(n, f):
            def _call(**kwargs):
                return f(kwargs)
            return _call

        tool = Tool(
            name=name,
            description=_get_description(name),
            parameters={"type": "object", "properties": {}, "additionalProperties": True},
            func=_make_func(name, run_fn),
        )
        registry.register(tool)
    return registry


def _get_description(name):
    """Read description from openapi.json or fallback."""
    path = os.path.join(APIS_DIR, name, "openapi.json")
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            desc = data.get("info", {}).get("description") or data.get("info", {}).get("title") or name
            return desc.strip()
        except Exception:
            pass
    return f"Call the {name} API with optional parameters."


if __name__ == "__main__":
    app = get_app()
    app.run(host="0.0.0.0", port=5000, debug=False)
