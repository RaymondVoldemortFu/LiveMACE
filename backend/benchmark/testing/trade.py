"""A persistence-free fake for the public trade gateway protocol."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from benchmark.contracts import TradeCommand, TradeCommandResult


class FakeTradeCommandGateway:
    """Deterministic ``TradeCommandGateway`` test double.

    ``execute`` records commands and returns an in-memory result.  It never
    opens a database session or imports an ORM model.  Idempotency keys are
    honoured so an extension can exercise retry handling with the same fake.
    """

    def __init__(
        self,
        responses: Iterable[TradeCommandResult] | Mapping[str, TradeCommandResult] = (),
        *,
        default_executed: bool = True,
        reject_code: str | None = None,
        result_factory: Callable[[TradeCommand], TradeCommandResult] | None = None,
        starting_order_id: int = 1,
        starting_trade_id: int = 1,
    ) -> None:
        if not isinstance(default_executed, bool):
            raise TypeError("default_executed must be bool")
        if reject_code is not None and (
            not isinstance(reject_code, str) or not reject_code.strip()
        ):
            raise ValueError("reject_code must be a non-empty string or None")
        if result_factory is not None and not callable(result_factory):
            raise TypeError("result_factory must be callable or None")
        if not isinstance(starting_order_id, int) or starting_order_id <= 0:
            raise ValueError("starting_order_id must be positive")
        if not isinstance(starting_trade_id, int) or starting_trade_id <= 0:
            raise ValueError("starting_trade_id must be positive")

        self.commands: list[TradeCommand] = []
        self._queued: list[TradeCommandResult] = []
        self._by_key: dict[tuple[int, str], TradeCommandResult] = {}
        self._mapped: dict[str, TradeCommandResult] = (
            dict(responses) if isinstance(responses, Mapping) else {}
        )
        if not isinstance(responses, Mapping):
            self._queued.extend(tuple(responses))
        self.default_executed = default_executed
        self.reject_code = reject_code
        self.result_factory = result_factory
        self._next_order_id = starting_order_id
        self._next_trade_id = starting_trade_id

    @property
    def calls(self) -> list[TradeCommand]:
        """Alias used by tests that call recorded gateway invocations calls."""

        return self.commands

    @property
    def last_command(self) -> TradeCommand | None:
        return self.commands[-1] if self.commands else None

    def execute(self, command: TradeCommand) -> TradeCommandResult:
        if not isinstance(command, TradeCommand):
            raise TypeError("command must be TradeCommand")
        self.commands.append(command)
        key = (command.account_id, command.idempotency_key)
        previous = self._by_key.get(key)
        if previous is not None:
            return previous

        result = self._next_result(command)
        self._by_key[key] = result
        return result

    def reset(self) -> None:
        self.commands.clear()
        self._by_key.clear()

    def _next_result(self, command: TradeCommand) -> TradeCommandResult:
        supplied: TradeCommandResult | None = None
        if command.idempotency_key in self._mapped:
            supplied = self._mapped[command.idempotency_key]
        elif self._queued:
            supplied = self._queued.pop(0)
        elif self.result_factory is not None:
            supplied = self.result_factory(command)
        if supplied is not None:
            if not isinstance(supplied, TradeCommandResult):
                raise TypeError("fake gateway responses must be TradeCommandResult")
            # A queued result from another command is unsafe to replay.  Keep
            # its outcome fields but bind the public result to this command.
            return TradeCommandResult(
                accepted=supplied.accepted,
                executed=supplied.executed,
                reject_code=supplied.reject_code,
                reject_message=supplied.reject_message,
                order_id=supplied.order_id,
                trade_id=supplied.trade_id,
                normalized_command=command,
                raw_result=dict(supplied.raw_result),
            )

        if self.reject_code is not None:
            return TradeCommandResult(
                accepted=False,
                executed=False,
                reject_code=self.reject_code,
                reject_message="rejected by fake trade gateway",
                order_id=None,
                trade_id=None,
                normalized_command=command,
                raw_result={"fake": True, "accepted": False},
            )

        executed = self.default_executed and command.operation not in {
            "hold",
        }
        order_id = self._allocate_order_id() if executed else None
        trade_id = self._allocate_trade_id() if executed else None
        return TradeCommandResult(
            accepted=True,
            executed=executed,
            reject_code=None,
            reject_message=None,
            order_id=order_id,
            trade_id=trade_id,
            normalized_command=command,
            raw_result={
                "fake": True,
                "accepted": True,
                "executed": executed,
                "operation": command.operation,
            },
        )

    def _allocate_order_id(self) -> int:
        value = self._next_order_id
        self._next_order_id += 1
        return value

    def _allocate_trade_id(self) -> int:
        value = self._next_trade_id
        self._next_trade_id += 1
        return value


# Both spellings are used in extension examples and downstream tests.
FakeTradeGateway = FakeTradeCommandGateway


__all__ = ["FakeTradeCommandGateway", "FakeTradeGateway"]
