from collections.abc import Callable

from fastapi import WebSocket

from app.schemas import BulkJob


async def stream_job(websocket: WebSocket, job: BulkJob, encode: Callable[[BulkJob], str]) -> None:
    await websocket.accept()
    async for snapshot in job.updates():
        await websocket.send_text(encode(snapshot))
    await websocket.close()
