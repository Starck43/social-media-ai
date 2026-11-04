"""Verify oversized WebSocket frames are rejected before ASGI delivery."""

import asyncio
import socket

import pytest
import uvicorn
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError


@pytest.mark.asyncio
async def test_oversized_websocket_message_never_reaches_asgi():
    received_events = []
    disconnected = asyncio.Event()

    async def instrumented_asgi(scope, receive, send):
        assert scope["type"] == "websocket"
        event = await receive()
        received_events.append(event)
        await send({"type": "websocket.accept"})
        while True:
            event = await receive()
            received_events.append(event)
            if event["type"] == "websocket.disconnect":
                disconnected.set()
                return

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    host, port = listener.getsockname()

    server = uvicorn.Server(
        uvicorn.Config(
            instrumented_asgi,
            host=host,
            port=port,
            ws="websockets",
            ws_max_size=65_536,
            ws_max_queue=1,
            ws_per_message_deflate=False,
            lifespan="off",
            access_log=False,
            log_level="critical",
        )
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))

    try:
        for _ in range(500):
            if server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)
        assert server.started, "Uvicorn did not start within five seconds"

        async with connect(f"ws://{host}:{port}/", max_size=None, open_timeout=5, close_timeout=2) as websocket:
            await websocket.send(b"x" * 65_537)
            with pytest.raises(ConnectionClosedError) as closed:
                await websocket.recv()
            assert closed.value.code == 1009

        await asyncio.wait_for(disconnected.wait(), timeout=2)
        assert [event["type"] for event in received_events] == [
            "websocket.connect",
            "websocket.disconnect",
        ]
        assert all("bytes" not in event and "text" not in event for event in received_events)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=5)
        finally:
            listener.close()
