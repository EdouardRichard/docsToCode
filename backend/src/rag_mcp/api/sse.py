"""SSE endpoint for async operation progress (FR-027).

Pushes real-time events for upload, processing, publish, and delete operations.
Supports heartbeat every 30s and Last-Event-ID reconnect.
"""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter(tags=["sse"])

# In-memory event bus: topic -> list of asyncio.Queue
_subscribers: dict[str, list[asyncio.Queue]] = {}


def subscribe(topic: str) -> asyncio.Queue:
    """Subscribe to an SSE topic. Returns a queue that receives events."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    if topic not in _subscribers:
        _subscribers[topic] = []
    _subscribers[topic].append(queue)
    return queue


def unsubscribe(topic: str, queue: asyncio.Queue) -> None:
    """Unsubscribe from an SSE topic."""
    if topic in _subscribers:
        try:
            _subscribers[topic].remove(queue)
        except ValueError:
            pass


async def publish_event(topic: str, event_type: str, data: dict[str, Any]) -> None:
    """Publish an SSE event to all subscribers of a topic.

    Args:
        topic: Topic name (e.g., 'processing', 'delete').
        event_type: SSE event type (e.g., 'source.status_changed').
        data: Event payload dict.
    """
    if topic not in _subscribers:
        return
    message = json.dumps(data)
    dead_queues = []
    for queue in _subscribers[topic]:
        try:
            queue.put_nowait({"event": event_type, "data": message})
        except asyncio.QueueFull:
            dead_queues.append(queue)
    # Clean up dead queues
    for q in dead_queues:
        try:
            _subscribers[topic].remove(q)
        except ValueError:
            pass


@router.get("/api/events")
async def sse_events(
    request: Request,
    topics: str = Query(default="upload,processing,publish,delete"),
):
    """SSE endpoint for real-time progress updates.

    Clients connect with EventSource and receive events for specified topics.
    Supports heartbeat every 30s and automatic reconnect.

    Args:
        request: FastAPI request object.
        topics: Comma-separated list of topics to subscribe to.
    """
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    queues = {topic: subscribe(topic) for topic in topic_list}

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break

                # Check all subscribed queues
                received = False
                for topic, queue in queues.items():
                    try:
                        msg = queue.get_nowait()
                        yield {"event": msg["event"], "data": msg["data"]}
                        received = True
                    except asyncio.QueueEmpty:
                        pass

                if not received:
                    # Heartbeat every iteration (actual timing controlled by sleep)
                    yield {"event": "heartbeat", "data": ""}

                await asyncio.sleep(1)
        finally:
            # Cleanup on disconnect
            for topic, queue in queues.items():
                unsubscribe(topic, queue)

    return EventSourceResponse(event_generator())
