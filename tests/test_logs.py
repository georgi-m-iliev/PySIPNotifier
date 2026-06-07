import logging

from fastapi.testclient import TestClient

from pysip_notifier.app import create_app
from pysip_notifier.log_stream import LogBroadcaster, log_broadcaster
from pysip_notifier.service import NotificationService
from tests.test_api import FakeCaller, FakeSynthesizer, make_settings


def test_root_serves_log_viewer() -> None:
    settings = make_settings()
    service = NotificationService(settings, FakeSynthesizer(), FakeCaller())

    with TestClient(create_app(settings, service)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "/ws/logs" in response.text
    assert "PySIP Notifier Logs" in response.text


def test_log_websocket_replays_buffered_lines() -> None:
    settings = make_settings()
    service = NotificationService(settings, FakeSynthesizer(), FakeCaller())

    with TestClient(create_app(settings, service)) as client:
        logging.getLogger("pysip_notifier.test").warning("hello websocket logs")
        with client.websocket_connect("/ws/logs") as websocket:
            lines = [websocket.receive_text() for _ in range(len(log_broadcaster.lines))]

    assert any("hello websocket logs" in line for line in lines)
    log_broadcaster.websockets.clear()


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.receive_calls = 0

    async def accept(self) -> None:
        return None

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def receive_text(self) -> str:
        self.receive_calls += 1
        if self.receive_calls > 1:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect()
        return "ping"


async def test_log_replay_uses_snapshot_when_buffer_mutates() -> None:
    broadcaster = LogBroadcaster()
    broadcaster.lines.append("first")
    websocket = FakeWebSocket()

    with broadcaster._lines_lock:
        broadcaster.lines.append("mutated before replay")
    await broadcaster.connect(websocket)

    assert websocket.sent == ["first", "mutated before replay"]
