#!/usr/bin/env python3
"""Bounded real-provider probes using only isolated Wave3 credentials."""

import argparse
import json
import time

from wave3_runtime import configure, ROOT


def model_probe():
    import os
    from services.agent.llm_client import LLMClient
    from benchmark.infrastructure.adapters.llm import LegacyLLMClientAdapter
    from benchmark.providers import LLMRequest
    from benchmark.contracts import to_jsonable

    model = os.environ["WAVE3_MODEL"]
    client = LLMClient(model, os.environ["API_KEY"], os.environ["BASE_URL"])
    client.max_retries = 0
    adapter = LegacyLLMClientAdapter(client)
    report = {"model": model, "base_url": os.environ["BASE_URL"], "started_at": time.time(), "passed": False}
    try:
        response = adapter.complete(
            LLMRequest(
                messages=({"role": "user", "content": "Reply with exactly OK."},),
                max_tokens=128,
                metadata={"timeout_seconds": 60},
            )
        )
        report["text"] = response.content
        assert response.content.strip() == "OK", "unexpected text response"
        schema = {
            "type": "function",
            "function": {
                "name": "echo_probe",
                "description": "Return a harmless echo.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            },
        }
        messages = [
            {
                "role": "user",
                "content": "Call echo_probe once with value hello. After its result, reply with OK.",
            }
        ]
        response = adapter.complete(
            LLMRequest(
                messages=tuple(messages),
                tools=(schema,),
                max_tokens=256,
                metadata={"timeout_seconds": 60},
            )
        )
        assert (
            len(response.tool_calls) == 1
            and response.tool_calls[0].name == "echo_probe"
        ), "expected echo tool call"
        assert dict(response.tool_calls[0].arguments) == {"value": "hello"}, (
            "invalid echo arguments"
        )
        report["tool_calls"] = to_jsonable(response.tool_calls)
        messages.append(to_jsonable(response.raw))
        messages.append(
            {
                "role": "tool",
                "tool_call_id": response.tool_calls[0].id,
                "content": str(response.tool_calls[0].arguments["value"]),
            }
        )
        response = adapter.complete(
            LLMRequest(
                messages=tuple(messages),
                tools=(schema,),
                max_tokens=128,
                metadata={"timeout_seconds": 60},
            )
        )
        assert response.content.strip() == "OK" and not response.tool_calls, (
            "tool roundtrip did not finish"
        )
        report.update(
            passed=True, tool_roundtrip=response.content, final_usage=client.last_usage
        )
    except Exception as exc:
        root = exc
        while root.__cause__ is not None:
            root = root.__cause__
        report.update(
            error_type=type(root).__name__,
            status_code=getattr(root, "status_code", None),
            code=getattr(exc, "code", None),
            insufficient_quota="insufficient_quota" in str(root),
        )
    finally:
        adapter.close()
    return report


def iex_probe():
    from services.alpaca_market_data import (
        get_price_result_from_alpaca,
        get_kline_data_from_alpaca,
        get_market_status_from_alpaca,
    )

    bars = get_kline_data_from_alpaca("AAPL", "1d", 10)
    from benchmark.contracts import to_jsonable
    from benchmark.providers import Freshness

    quote = get_price_result_from_alpaca("AAPL")
    price = quote.value
    status = get_market_status_from_alpaca("AAPL")
    return {
        "provider": "alpaca",
        "feed": "iex",
        "symbol": "AAPL",
        "last_price": price,
        "quote": to_jsonable(quote),
        "bars": bars,
        "market_status": status,
        "passed": bool(bars and price and price > 0 and (
            not status.get("is_trading") or quote.freshness is Freshness.FRESH
        )),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=["model", "iex"])
    args = parser.parse_args()
    configure()
    report = model_probe() if args.provider == "model" else iex_probe()
    target = ROOT / ".wave3/evidence" / f"{args.provider}-probe-final.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, default=str))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
