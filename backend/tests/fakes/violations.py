"""Deliberately invalid extension fakes for sync-boundary tests in later modules."""

from __future__ import annotations


class AwaitableAgent:
    def run(self, context):
        async def result():
            return {"operation": "hold"}

        return result()


class AwaitableTool:
    def invoke(self, context, arguments):
        async def result():
            return {"ok": True}

        return result()


__all__ = ["AwaitableAgent", "AwaitableTool"]

