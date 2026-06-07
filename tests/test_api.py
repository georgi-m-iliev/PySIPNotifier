import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from pysip_notifier.app import create_app
from pysip_notifier.config import Settings
from pysip_notifier.service import NotificationService


class FakeSynthesizer:
    async def synthesize(self, message: str, voice: str) -> Path:
        path = Path("test-notification.mp3")
        path.write_bytes(f"{voice}:{message}".encode())
        return path


class FakeCaller:
    def __init__(self) -> None:
        self.registered = False
        self.calls: list[tuple[str, int]] = []

    async def start(self) -> None:
        self.registered = True

    async def stop(self) -> None:
        self.registered = False

    async def call_and_play(
        self,
        destination: str,
        audio_path: Path,
        repetitions: int,
        pause_seconds: float,
        timeout_seconds: int,
    ) -> None:
        assert audio_path.exists()
        self.calls.append((destination, repetitions))


def make_settings(**overrides) -> Settings:
    values = {
        "sip_username": "user",
        "sip_password": "password",
        "sip_server": "sip.example.com:5060",
        "sip_connection_type": "UDP",
        "sip_caller_id": None,
        "sip_destination": "100",
        "sip_advertised_ip": None,
        "sip_media_ip": None,
        "tts_voice": "en-US-AriaNeural",
        "tts_rate": "+0%",
        "tts_volume": "+0%",
        "tts_pitch": "+0Hz",
        "call_timeout_seconds": 30,
        "call_repetitions": 1,
        "call_pause_seconds": 0,
        "api_host": "127.0.0.1",
        "api_port": 8080,
        "log_level": "INFO",
    }
    values.update(overrides)
    return Settings(**values)


def test_notify_queues_and_completes_call() -> None:
    settings = make_settings()
    caller = FakeCaller()
    service = NotificationService(settings, FakeSynthesizer(), caller)

    with TestClient(create_app(settings, service)) as client:
        response = client.post(
            "/api/v1/notify",
            json={"message": "Tracker alarm", "to": "359123", "repetitions": 2},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        for _ in range(50):
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] == "completed":
                break
            asyncio.run(asyncio.sleep(0.01))

        assert job["status"] == "completed"
        assert caller.calls == [("359123", 2)]


def test_traccar_endpoint_uses_sms_payload_shape() -> None:
    settings = make_settings()
    service = NotificationService(settings, FakeSynthesizer(), FakeCaller())

    with TestClient(create_app(settings, service)) as client:
        response = client.post(
            "/api/v1/traccar",
            json={"to": "359123", "message": "Device entered geofence"},
        )

    assert response.status_code == 202


def test_missing_sip_configuration_returns_service_unavailable() -> None:
    settings = make_settings(sip_username="", sip_password="", sip_server="")
    service = NotificationService(settings, FakeSynthesizer(), FakeCaller())

    with TestClient(create_app(settings, service)) as client:
        response = client.post("/api/v1/notify", json={"message": "Hello"})

    assert response.status_code == 503
