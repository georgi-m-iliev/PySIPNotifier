from __future__ import annotations

import pytest
from pydantic import ValidationError

from pysip_notifier.config import Settings


def test_settings_normalize_values() -> None:
    settings = Settings(
        _env_file=None,
        sip_connection_type="tlsv1",
        log_level="debug",
    )

    assert settings.sip_connection_type == "TLSv1"
    assert settings.log_level == "DEBUG"


def test_settings_validate_numeric_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, call_timeout_seconds=0)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, call_pause_seconds=-1)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_port=70000)


def test_settings_load_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIP_USERNAME", "999")
    monkeypatch.setenv("SIP_PASSWORD", "secret")
    monkeypatch.setenv("SIP_SERVER", "pbx.local:5060")
    monkeypatch.setenv("CALL_REPETITIONS", "2")

    settings = Settings(_env_file=None)

    assert settings.sip_configured is True
    assert settings.call_repetitions == 2
