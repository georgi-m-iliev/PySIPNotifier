from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from pysip_notifier.sip import PySipCaller, _contact_uri
from tests.test_api import make_settings


@pytest.mark.asyncio
async def test_start_marks_configured_direct_caller_ready() -> None:
    caller = PySipCaller(make_settings(), call_factory=lambda *args, **kwargs: None)

    await caller.start()

    assert caller.registered is True


def test_direct_call_uses_sip_username_as_caller_identity() -> None:
    captured: dict[str, object] = {}

    def factory(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return object()

    settings = make_settings(sip_username="999", sip_caller_id="042955392")
    caller = PySipCaller(settings, call_factory=factory)
    caller._create_call("105")

    assert captured["args"] == (
        "999",
        "password",
        "sip.example.com:5060",
        "105",
    )
    assert captured["kwargs"] == {
        "connection_type": "UDP",
        "caller_id": "999",
    }


def test_network_address_overrides_are_applied() -> None:
    sip_core = SimpleNamespace(
        get_public_ip=lambda: "198.51.100.10",
        get_local_ip=lambda: "172.17.0.2",
    )
    call = SimpleNamespace(sip_core=sip_core)
    settings = make_settings(
        sip_advertised_ip="10.1.0.6",
        sip_media_ip="10.1.0.6",
    )

    PySipCaller(settings)._configure_network_addresses(call)

    assert sip_core.get_public_ip() == "10.1.0.6"
    assert sip_core.get_local_ip() == "10.1.0.6"


def test_playback_timeout_scales_with_audio_duration() -> None:
    short_stream = SimpleNamespace(audio_length=4)
    long_stream = SimpleNamespace(audio_length=75)

    assert PySipCaller._playback_timeout(short_stream) == 19
    assert PySipCaller._playback_timeout(long_stream) == 90


@pytest.mark.parametrize(
    ("contact", "expected"),
    [
        ("<sip:105@192.168.1.195:5060;transport=udp>", "sip:105@192.168.1.195:5060;transport=udp"),
        ("sip:105@192.168.1.195:5060;expires=60", "sip:105@192.168.1.195:5060"),
        (None, None),
    ],
)
def test_contact_uri(contact: str | None, expected: str | None) -> None:
    assert _contact_uri(contact) == expected


def test_digest_auth_compat_supports_qop_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCall:
        username = "999"
        password = "secret"

    call = FakeCall()
    PySipCaller._enable_digest_auth_compat(call)
    monkeypatch.setattr("pysip_notifier.sip.secrets.token_hex", lambda _: "abcdef")
    challenge = SimpleNamespace(
        nonce="nonce-value",
        realm="pbx",
        public_ip="192.168.1.10",
        rport="5060",
        get_header=lambda name: (
            'Digest realm="pbx", nonce="nonce-value", algorithm=MD5, qop="auth"'
            if name == "WWW-Authenticate"
            else None
        ),
    )

    nonce, realm, _, _ = call.extract_auth_details(challenge)
    header = call.generate_auth_header(
        "INVITE",
        "sip:123@192.168.1.195:5060;transport=UDP",
        nonce,
        realm,
    )

    ha1 = hashlib.md5(b"999:pbx:secret").hexdigest()
    ha2 = hashlib.md5(
        b"INVITE:sip:123@192.168.1.195:5060;transport=UDP"
    ).hexdigest()
    expected = hashlib.md5(
        f"{ha1}:nonce-value:00000001:abcdef:auth:{ha2}".encode()
    ).hexdigest()
    assert f'response="{expected}"' in header
    assert 'qop=auth, nc=00000001, cnonce="abcdef"' in header


@pytest.mark.asyncio
async def test_call_failure_before_answer_is_reported() -> None:
    class FakeCallHandler:
        async def hangup(self) -> None:
            return None

        async def play(self, *args, **kwargs):
            raise AssertionError("Playback must not begin before answer")

    class FakeCall:
        def __init__(self) -> None:
            self.username = "999"
            self.password = "password"
            self.call_handler = FakeCallHandler()
            self._is_call_stopped = False
            self._state_callbacks = []
            self._hangup_callbacks = []

        def on_call_state_changed(self, callback):
            self._state_callbacks.append(callback)
            return callback

        def on_call_hanged_up(self, callback):
            self._hangup_callbacks.append(callback)
            return callback

        async def start(self) -> None:
            for callback in self._state_callbacks:
                await callback(SimpleNamespace(value="FAILED"))
            for callback in self._hangup_callbacks:
                await callback("Service Unavailable")
            self._is_call_stopped = True

        def extract_auth_details(self, message):
            return None

        def generate_auth_header(self, *args):
            return ""

    call = FakeCall()
    caller = PySipCaller(make_settings(), call_factory=lambda *args, **kwargs: call)

    with pytest.raises(RuntimeError, match="Service Unavailable"):
        await caller.call_and_play(
            "105",
            Path("unused.wav"),
            repetitions=1,
            pause_seconds=0,
            timeout_seconds=1,
        )
