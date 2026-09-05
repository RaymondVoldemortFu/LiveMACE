"""Built-in market snapshot and kline Tools."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from benchmark.contracts import (
    MARKET_READ,
    SANDBOX_WRITE,
    JsonValue,
    SideEffect,
    ToolContext,
    ToolResult,
    to_jsonable,
)
from benchmark.builtin.tools._support import (
    BoundCallableTool,
    LazyContainerSandbox,
    spec,
    tool_result_error,
)

KLINE_MODE_PRESETS = {
    "short_term_high_precision": {"interval": "5m", "lookback_days": 3},
    "mid_term_medium_precision": {"interval": "1h", "lookback_days": 14},
    "long_term_low_precision": {"interval": "1d", "lookback_days": 180},
}

_SNAPSHOT_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "symbol": {"type": "string"},
        "market": {"type": "string", "description": "Market type: CRYPTO or US"},
    },
    "required": ["symbol", "market"],
}

_KLINE_PARAMETERS: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "symbol": {
            "type": "string",
            "description": "Trading symbol, e.g. BTC or AAPL",
        },
        "market": {
            "type": "string",
            "description": "Market identifier: CRYPTO or US",
        },
        "mode": {
            "type": "string",
            "enum": [
                "short_term_high_precision",
                "mid_term_medium_precision",
                "long_term_low_precision",
            ],
            "description": "Preset kline mode with default interval and lookback window.",
            "default": "mid_term_medium_precision",
        },
        "end_time": {
            "type": "string",
            "description": "Optional end time (ISO 8601). Defaults to now.",
        },
    },
    "required": ["symbol", "market"],
}

SNAPSHOT_SPEC = spec(
    "core.market_snapshot",
    "Get the latest market snapshot for a crypto symbol or US stock.",
    _SNAPSHOT_PARAMETERS,
    side_effect=SideEffect.EXTERNAL_READ,
    capabilities=(MARKET_READ,),
)

KLINE_SPEC = spec(
    "core.kline_history",
    (
        "Fetch kline (candlestick) history using one of three default modes and save it to a file in the sandbox. "
        "Modes: short_term_high_precision(5m, 3d), mid_term_medium_precision(1h, 14d), "
        "long_term_low_precision(1d, 180d)."
    ),
    _KLINE_PARAMETERS,
    side_effect=SideEffect.SANDBOX_WRITE,
    capabilities=(MARKET_READ, SANDBOX_WRITE),
    timeout_seconds=60.0,
)


def _parse_iso_datetime(time_str: str | None) -> datetime | None:
    if not time_str:
        return None
    try:
        parsed = datetime.fromisoformat(str(time_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_count(data: Any) -> int | None:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return len(data["data"])
    return None


def _default_price_reader(symbol: str, market: str) -> Any:
    from services.market_data import get_last_price

    return get_last_price(symbol, market)


def _default_status_reader(symbol: str, market: str) -> Any:
    from services.market_data import get_market_status

    return get_market_status(symbol, market)


def _default_kline_reader(
    symbol: str,
    market: str,
    period: str,
    start_time: int,
    end_time: int,
) -> Any:
    from services.market_data import get_kline_data

    return get_kline_data(
        symbol,
        market=market,
        period=period,
        count=1000,
        start_time=start_time,
        end_time=end_time,
    )


class MarketSnapshotTool(BoundCallableTool):
    def __init__(
        self,
        *,
        get_price: Callable[[str, str], Any] | None = None,
        get_status: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._get_price = get_price or _default_price_reader
        self._get_status = get_status or _default_status_reader
        super().__init__(SNAPSHOT_SPEC, self._invoke)

    def _invoke(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> dict[str, Any]:
        del context
        symbol = str(arguments["symbol"])
        market = str(arguments["market"])
        return {
            "symbol": symbol,
            "market": market,
            "price": float(self._get_price(symbol, market)),
            "market_status": self._get_status(symbol, market),
        }


class KlineHistoryTool(BoundCallableTool):
    def __init__(
        self,
        *,
        get_klines: Callable[..., Any] | None = None,
        sandbox: Any | None = None,
    ) -> None:
        self._get_klines = get_klines or _default_kline_reader
        self._sandbox = sandbox or LazyContainerSandbox()
        super().__init__(KLINE_SPEC, self._invoke)

    def _invoke(self, context: ToolContext, arguments: Mapping[str, JsonValue]) -> ToolResult:
        import json

        symbol = str(arguments["symbol"])
        market = str(arguments["market"])
        mode_key = str(arguments.get("mode") or "mid_term_medium_precision").strip()
        preset = KLINE_MODE_PRESETS.get(mode_key)
        if not preset:
            return ToolResult(
                ok=True,
                value={
                    "error": (
                        "Invalid mode. Available modes: "
                        "short_term_high_precision, mid_term_medium_precision, long_term_low_precision"
                    )
                },
            )

        end_time = arguments.get("end_time")
        end_dt = _parse_iso_datetime(str(end_time) if end_time else None)
        if end_time and not end_dt:
            return ToolResult(
                ok=True,
                value={"error": "Invalid end_time format. Please use ISO 8601 (e.g. 2023-01-01T00:00:00)."},
            )
        if end_dt is None:
            end_dt = datetime.now(timezone.utc)

        start_dt = end_dt - timedelta(days=int(preset["lookback_days"]))
        interval = str(preset["interval"])
        data = self._get_klines(
            symbol,
            market,
            interval,
            int(start_dt.timestamp() * 1000),
            int(end_dt.timestamp() * 1000),
        )
        if isinstance(data, dict) and "error" in data:
            return ToolResult(ok=True, value=to_jsonable(data))

        content = json.dumps(data, ensure_ascii=False, indent=2)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = f"/workspace/kline_{symbol}_{mode_key}_{timestamp}.json"
        write_res = self._sandbox.write_file(context.account_id, filename, content)
        row_count = _row_count(data)
        metadata: dict[str, JsonValue] = {
            "file_path": filename,
            "source": "market_kline",
        }
        if row_count is not None:
            metadata["row_count"] = row_count
        if write_res != "Success":
            return tool_result_error(
                "KLINE_WRITE_FAILED",
                f"Failed to save K-line data to container: {write_res}",
                metadata=metadata,
            )
        return ToolResult(
            ok=True,
            value={
                "status": "success",
                "mode": mode_key,
                "interval": interval,
                "lookback_days": int(preset["lookback_days"]),
                "file_path": filename,
                "message": (
                    f"K-line data ({mode_key}) saved to {filename}. "
                    "You can use 'read_file' to view it (truncated) or 'run_python_script' to analyze it."
                ),
                "data_preview": str(data)[:200] + "...",
            },
            metadata=metadata,
        )


class MarketToolsProvider:
    def __init__(
        self,
        *,
        get_price: Callable[[str, str], Any] | None = None,
        get_status: Callable[[str, str], Any] | None = None,
        get_klines: Callable[..., Any] | None = None,
        sandbox: Any | None = None,
    ) -> None:
        self._tools = (
            MarketSnapshotTool(get_price=get_price, get_status=get_status),
            KlineHistoryTool(get_klines=get_klines, sandbox=sandbox),
        )

    def list_tools(self) -> tuple[BoundCallableTool, ...]:
        return self._tools


__all__ = [
    "KLINE_MODE_PRESETS",
    "KlineHistoryTool",
    "MarketSnapshotTool",
    "MarketToolsProvider",
]
