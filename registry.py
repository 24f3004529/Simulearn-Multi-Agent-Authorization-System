"""
Agent card registry with capability-based discovery. Agents register a card
once; every caller resolves peers by capability, never by a hardcoded name or
endpoint.

DIY Part 1: cards are persisted in AgentCardRegistryTable (partition key
`agent_name`), so a fresh Orchestrator process can discover agents that an
earlier process registered. The table is the source of truth for *what is
registered*; the in-process `_invokers` dict only caches the Python callable
used to invoke each agent, because a callable cannot be stored in DynamoDB.

When a card is read from the table and this process has no invoker cached
for it, `invoke_fn_resolver(card)` (if supplied) builds one — e.g. a real
InvokeAgentRuntime call for a deployed Runtime. Without a resolver, the
returned RegisteredAgent carries a placeholder invoke_fn that raises a clear
error if called, so discovery still works in a fresh process.

Set PA_STATE_BACKEND=memory to keep the original in-memory-only behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from dynamo_config import (StateBackendError, agent_card_registry_table_name,
                           get_table, use_dynamodb)
from pa_types import AgentCard


class AgentNotFoundError(Exception):
    pass


class AgentVersionMismatchError(Exception):
    pass


@dataclass
class RegisteredAgent:
    card: AgentCard
    invoke_fn: Callable


InvokeFnResolver = Callable[[AgentCard], Callable]


class AgentCardRegistry:
    def __init__(
        self,
        *,
        invoke_fn_resolver: InvokeFnResolver | None = None,
        table: Any | None = None,
        use_table: bool | None = None,
    ) -> None:
        # Cache of invoke functions bound in *this* process, keyed by agent_name.
        self._invokers: dict[str, Callable] = {}
        # Only used when the DynamoDB backend is disabled (PA_STATE_BACKEND=memory).
        self._cards: dict[str, AgentCard] = {}
        self._invoke_fn_resolver = invoke_fn_resolver
        self._use_table = use_dynamodb() if use_table is None else use_table
        self._table = table

    # -- public API (signatures unchanged) ---------------------------------

    def register(self, card: AgentCard, invoke_fn: Callable) -> None:
        if self._use_table:
            self._put_card(card)
        else:
            self._cards[card.agent_name] = card
        self._invokers[card.agent_name] = invoke_fn

    def discover_by_capability(
        self, capability: str, min_version: str = "1.0"
    ) -> RegisteredAgent:
        # Resolves the first agent advertising `capability` at or above
        # `min_version`; raises if none found or only a lower version matches.
        candidates = self._cards_with_capability(capability)
        if not candidates:
            raise AgentNotFoundError(
                f"No registered agent advertises capability '{capability}'"
            )

        for card in candidates:
            if _version_at_least(card.version, min_version):
                return self._bind(card)

        raise AgentVersionMismatchError(
            f"Agent(s) advertising '{capability}' found, but none satisfy min_version={min_version} "
            f"(found versions: {[c.version for c in candidates]})"
        )

    def is_available(self, agent_name: str) -> bool:
        return self._load_card(agent_name) is not None

    def get_card(self, agent_name: str) -> AgentCard:
        card = self._load_card(agent_name)
        if card is None:
            raise AgentNotFoundError(f"No registered agent named '{agent_name}'")
        return card

    # -- internals ---------------------------------------------------------

    def _get_table(self) -> Any:
        if self._table is None:
            self._table = get_table(agent_card_registry_table_name())
        return self._table

    def _put_card(self, card: AgentCard) -> None:
        try:
            self._get_table().put_item(Item=_card_to_item(card))
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise StateBackendError(
                f"PutItem to {agent_card_registry_table_name()} failed for "
                f"agent '{card.agent_name}': {exc}"
            ) from exc

    def _load_card(self, agent_name: str) -> AgentCard | None:
        if not self._use_table:
            return self._cards.get(agent_name)
        try:
            response = self._get_table().get_item(
                Key={"agent_name": agent_name}, ConsistentRead=True
            )
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise StateBackendError(
                f"GetItem on {agent_card_registry_table_name()} failed for "
                f"agent '{agent_name}': {exc}"
            ) from exc
        item = response.get("Item")
        return _item_to_card(item) if item else None

    def _cards_with_capability(self, capability: str) -> list[AgentCard]:
        if not self._use_table:
            cards = [c for c in self._cards.values() if capability in c.capabilities]
        else:
            cards = [
                _item_to_card(item)
                for item in self._scan_for_capability(capability)
            ]
            # contains() is a substring match on strings; keep only exact
            # list-membership matches.
            cards = [c for c in cards if capability in c.capabilities]
        # A Scan has no guaranteed order, so make "first match" deterministic:
        # agents bound in this process first (preserves the original
        # registration-order semantics within a process), then by name.
        bound_order = {name: i for i, name in enumerate(self._invokers)}
        return sorted(
            cards,
            key=lambda c: (
                0 if c.agent_name in bound_order else 1,
                bound_order.get(c.agent_name, 0),
                c.agent_name,
            ),
        )

    def _scan_for_capability(self, capability: str) -> list[dict]:
        from boto3.dynamodb.conditions import Attr

        table = self._get_table()
        kwargs: dict[str, Any] = {
            "FilterExpression": Attr("capabilities").contains(capability),
            "ConsistentRead": True,
        }
        items: list[dict] = []
        try:
            while True:
                response = table.scan(**kwargs)
                items.extend(response.get("Items", []))
                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise StateBackendError(
                f"Scan on {agent_card_registry_table_name()} failed for "
                f"capability '{capability}': {exc}"
            ) from exc
        return items

    def _bind(self, card: AgentCard) -> RegisteredAgent:
        invoke_fn = self._invokers.get(card.agent_name)
        if invoke_fn is None and self._invoke_fn_resolver is not None:
            invoke_fn = self._invoke_fn_resolver(card)
            self._invokers[card.agent_name] = invoke_fn
        if invoke_fn is None:
            invoke_fn = _unbound_invoker(card.agent_name)
        return RegisteredAgent(card=card, invoke_fn=invoke_fn)


def _card_to_item(card: AgentCard) -> dict[str, Any]:
    return {
        "agent_name": card.agent_name,
        "capabilities": list(card.capabilities),
        "input_schema": dict(card.input_schema),
        "output_schema": dict(card.output_schema),
        "version": card.version,
    }


def _item_to_card(item: dict[str, Any]) -> AgentCard:
    return AgentCard(
        agent_name=str(item["agent_name"]),
        capabilities=[str(c) for c in item.get("capabilities", [])],
        input_schema={str(k): str(v) for k, v in item.get("input_schema", {}).items()},
        output_schema={str(k): str(v) for k, v in item.get("output_schema", {}).items()},
        version=str(item.get("version", "1.0")),
    )


def _unbound_invoker(agent_name: str) -> Callable:
    def invoke(*_args: Any, **_kwargs: Any) -> Any:
        raise AgentNotFoundError(
            f"Agent '{agent_name}' is registered in {agent_card_registry_table_name()}, "
            f"but no invoke function is bound in this process. Pass an "
            f"invoke_fn_resolver to AgentCardRegistry (see "
            f"runtime_client.make_runtime_invoke_fn_resolver) or call register() "
            f"in this process."
        )

    return invoke


def _version_at_least(actual: str, minimum: str) -> bool:
    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.split("."))

    return parts(actual) >= parts(minimum)
