from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Callable

EventHandler = Callable[[dict[str, Any]], None]


class InMemoryEventBus:
    def __init__(self):
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._queue: deque[dict[str, Any]] = deque()
        self._dispatching = False

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        self._handlers[event_name].append(handler)

    def publish(self, event: dict[str, Any]) -> None:
        self._queue.append(event)
        if self._dispatching:
            return
        self._dispatching = True
        try:
            while self._queue:
                queued = self._queue.popleft()
                event_name = queued.get("event")
                for handler in list(self._handlers.get(event_name, [])):
                    handler(queued)
        finally:
            self._dispatching = False
