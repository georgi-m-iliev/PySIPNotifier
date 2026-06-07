import asyncio
import logging
import threading
from collections import deque
from typing import Deque

from fastapi import WebSocket, WebSocketDisconnect


class LogBroadcaster:
    def __init__(self, max_lines: int = 500) -> None:
        self.lines: Deque[str] = deque(maxlen=max_lines)
        self.websockets: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.handler: logging.Handler | None = None
        self._lines_lock = threading.Lock()

    def install(self, level: str) -> None:
        self.loop = asyncio.get_running_loop()
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

        if self.handler is None:
            handler = WebSocketLogHandler(self)
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
                )
            )
            self.handler = handler
            root_logger.addHandler(handler)

        for logger_name in ("PySIP.utils.logger", "uvicorn", "uvicorn.error", "uvicorn.access"):
            named_logger = logging.getLogger(logger_name)
            if not named_logger.propagate and self.handler not in named_logger.handlers:
                named_logger.addHandler(self.handler)

    def publish(self, message: str) -> None:
        with self._lines_lock:
            self.lines.append(message)
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self._broadcast, message)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        with self._lines_lock:
            history = list(self.lines.copy())
        for line in history:
            await websocket.send_text(line)

        self.websockets.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            self.websockets.discard(websocket)
        except RuntimeError:
            self.websockets.discard(websocket)

    def _broadcast(self, message: str) -> None:
        for websocket in tuple(self.websockets):
            asyncio.create_task(self._send(websocket, message))

    async def _send(self, websocket: WebSocket, message: str) -> None:
        try:
            await websocket.send_text(message)
        except Exception:
            self.websockets.discard(websocket)


class WebSocketLogHandler(logging.Handler):
    def __init__(self, broadcaster: LogBroadcaster) -> None:
        super().__init__()
        self.broadcaster = broadcaster

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.broadcaster.publish(self.format(record))
        except Exception:
            self.handleError(record)


log_broadcaster = LogBroadcaster()
