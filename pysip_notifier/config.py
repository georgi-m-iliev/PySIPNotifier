from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    sip_username: str = ""
    sip_password: str = ""
    sip_server: str = ""
    sip_connection_type: str = "UDP"
    sip_caller_id: str | None = None
    sip_destination: str | None = None

    tts_voice: str = "en-US-AriaNeural"
    tts_rate: str = "+0%"
    tts_volume: str = "+0%"
    tts_pitch: str = "+0Hz"

    call_timeout_seconds: int = Field(default=60, ge=1)
    call_repetitions: int = Field(default=1, ge=1)
    call_pause_seconds: float = Field(default=1, ge=0)

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8080, ge=1, le=65535)
    log_level: str = "INFO"

    @field_validator("sip_connection_type", mode="before")
    @classmethod
    def normalize_connection_type(cls, value: Any) -> str:
        connection_type = str(value).upper()
        if connection_type not in {"AUTO", "TCP", "UDP", "TLS", "TLSV1"}:
            raise ValueError(
                "SIP_CONNECTION_TYPE must be AUTO, TCP, UDP, TLS, or TLSv1"
            )
        return "TLSv1" if connection_type == "TLSV1" else connection_type

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: Any) -> str:
        return str(value).upper()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

    @property
    def sip_configured(self) -> bool:
        return bool(self.sip_username and self.sip_password and self.sip_server)
