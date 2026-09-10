"""
Lightweight Event Bus
======================
Simple in-process event routing for local development.
In production, replace with Cloud Pub/Sub or Cloud Tasks.

The coordinator knows WHAT happened, WHO needs to react,
and WHICH guardrails apply — it does NOT contain forecasting logic.

Event flow:
  transactions.imported  →  D1
  D1.completed           →  D2, D3
  promotion.created      →  D3
  local_event.created    →  D3
  D1 + D2 + D3 completed →  D4
  D4.completed           →  D5
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    TRANSACTIONS_IMPORTED = "transactions.imported"
    PROMOTIONS_UPDATED = "promotion.created"
    LOCAL_EVENT_CREATED = "local_event.created"
    D1_COMPLETED = "D1.completed"
    D2_COMPLETED = "D2.completed"
    D3_COMPLETED = "D3.completed"
    D4_COMPLETED = "D4.completed"
    D5_COMPLETED = "D5.completed"
    PIPELINE_COMPLETED = "pipeline.completed"
    AGENT_FAILED = "agent.failed"


@dataclass
class DomainEvent:
    event_type: EventType
    product_id: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime
            self.timestamp = datetime.utcnow().isoformat()


Handler = Callable[[DomainEvent], None]


class EventBus:
    """
    In-process publish/subscribe event bus.
    Handlers are registered per event type and called synchronously.
    """

    def __init__(self):
        self._handlers: Dict[EventType, List[Handler]] = {}

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def publish(self, event: DomainEvent) -> None:
        handlers = self._handlers.get(event.event_type, [])
        logger.info(
            "[EventBus] Publishing %s (product=%s) to %d handlers",
            event.event_type, event.product_id, len(handlers)
        )
        for h in handlers:
            try:
                h(event)
            except Exception as e:
                logger.error(
                    "[EventBus] Handler %s failed for event %s: %s",
                    h.__name__, event.event_type, e, exc_info=True
                )


# Module-level singleton for use within the backend
_bus = EventBus()


def get_event_bus() -> EventBus:
    return _bus
