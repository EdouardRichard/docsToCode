"""Per-process instance identity context (006, US3).

The MCP process registers its instance identity at startup (FR-030); the
retrieval run recorder reads this context to attribute each run record with
instance_id / instance_mode (data-model §4.1) so runtime metrics can group
by instance form. Test code uses instance_scope() to inject a temporary
identity; production entries set it once at startup.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager

_state = threading.local()


def set_instance(instance_id: uuid.UUID | None, instance_mode: str | None, worker_id: int | None = None) -> None:
    _state.instance_id = instance_id
    _state.instance_mode = instance_mode
    _state.worker_id = worker_id


def get_instance() -> tuple[uuid.UUID | None, str | None, int | None]:
    return (
        getattr(_state, "instance_id", None),
        getattr(_state, "instance_mode", None),
        getattr(_state, "worker_id", None),
    )


def clear_instance() -> None:
    set_instance(None, None, None)


@contextmanager
def instance_scope(instance_id, instance_mode, worker_id=None):
    old = get_instance()
    set_instance(instance_id, instance_mode, worker_id)
    try:
        yield
    finally:
        set_instance(*old)
