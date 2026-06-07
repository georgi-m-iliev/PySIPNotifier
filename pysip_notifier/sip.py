import asyncio
import hashlib
import ipaddress
import logging
import re
import secrets
from pathlib import Path
from types import MethodType
from typing import Any, Callable

from pysip_notifier.config import Settings

logger = logging.getLogger(__name__)

_DIGEST_PARAMETER = re.compile(r'(\w+)=("([^"]*)"|([^,\s]+))')


class PySipCaller:
    def __init__(
        self,
        settings: Settings,
        call_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._call_factory = call_factory
        self.registered = False

    async def start(self) -> None:
        if self.registered:
            return
        if not self.settings.sip_configured:
            raise RuntimeError(
                "SIP is not configured; set SIP_USERNAME, SIP_PASSWORD, and SIP_SERVER"
            )

        if self._call_factory is None:
            # PyPI package PySIPio exposes the import package as PySIP.
            from PySIP.sip_call import SipCall

            self._call_factory = SipCall
        self.registered = True

    async def stop(self) -> None:
        self.registered = False

    def _create_call(self, destination: str) -> Any:
        if self._call_factory is None:
            raise RuntimeError("SIP caller has not been started")

        # Asterisk authenticates the From identity for this endpoint. Using a
        # separate caller ID here causes repeated 401 responses.
        call = self._call_factory(
            self.settings.sip_username,
            self.settings.sip_password,
            self.settings.sip_server,
            destination,
            connection_type=self.settings.sip_connection_type,
            caller_id=self.settings.sip_username,
        )
        self._configure_network_addresses(call)
        return call

    def _configure_network_addresses(self, call: Any) -> None:
        sip_core = getattr(call, "sip_core", None)
        if sip_core is None:
            return

        if self.settings.sip_advertised_ip:
            advertised_ip = self.settings.sip_advertised_ip
            sip_core.get_public_ip = lambda: advertised_ip
        if self.settings.sip_media_ip:
            media_ip = self.settings.sip_media_ip
            sip_core.get_local_ip = lambda: media_ip

    async def call_and_play(
        self,
        destination: str,
        audio_path: Path,
        repetitions: int,
        pause_seconds: float,
        timeout_seconds: int,
    ) -> None:
        await self.start()
        call = self._create_call(destination)
        self._enable_digest_auth_compat(call)
        answered = asyncio.Event()
        finished = asyncio.Event()
        finish_reason = "Call ended before answer"
        responses_received = 0

        async def log_sip_response(message: Any) -> None:
            nonlocal responses_received
            status = getattr(getattr(message, "status", None), "code", None)
            if status is None or getattr(message, "method", None) != "INVITE":
                return
            responses_received += 1
            logger.info("SIP INVITE response from server: %s", status)

        sip_core = getattr(call, "sip_core", None)
        if sip_core is not None:
            sip_core.on_message_callbacks.insert(0, log_sip_response)

        @call.on_call_state_changed
        async def state_changed(state: Any) -> None:
            nonlocal finish_reason
            state_value = getattr(state, "value", str(state))
            if state_value == "DIALING":
                logger.info(
                    "SIP call network: server=%s transport=%s signaling=%s (%s) "
                    "media=%s (%s)",
                    self.settings.sip_server,
                    self.settings.sip_connection_type,
                    getattr(call, "my_public_ip", None),
                    _address_family(getattr(call, "my_public_ip", None)),
                    getattr(call, "my_private_ip", None),
                    _address_family(getattr(call, "my_private_ip", None)),
                )
            elif state_value == "ANSWERED":
                answered.set()
            elif state_value in {"BUSY", "ENDED", "FAILED"}:
                finish_reason = f"Call entered {state_value} state"
                finished.set()

        @call.on_call_hanged_up
        async def call_hanged_up(reason: str) -> None:
            nonlocal finish_reason
            finish_reason = reason or finish_reason
            finished.set()

        call_task = asyncio.create_task(call.start(), name=f"sip-call-{destination}")
        answer_task = asyncio.create_task(answered.wait())
        finish_task = asyncio.create_task(finished.wait())

        try:
            done, _ = await asyncio.wait(
                {call_task, answer_task, finish_task},
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if finish_task in done:
                raise RuntimeError(finish_reason)
            if answer_task not in done or not answered.is_set():
                if not done:
                    details = ""
                    if responses_received == 0:
                        details = (
                            "; no SIP response was received. Check routing/firewall "
                            "to SIP_SERVER and configure SIP_ADVERTISED_IP/"
                            "SIP_MEDIA_IP on multi-interface or container hosts"
                        )
                    raise TimeoutError(
                        f"Call was not answered within {timeout_seconds} seconds"
                        f"{details}"
                    )
                raise RuntimeError(finish_reason)

            for index in range(repetitions):
                audio_format = audio_path.suffix.removeprefix(".") or "wav"
                stream = await call.call_handler.play(
                    str(audio_path),
                    format=audio_format,
                )
                playback_task = asyncio.create_task(stream.wait_finished())
                playback_timeout = self._playback_timeout(stream)
                done, _ = await asyncio.wait(
                    {playback_task, call_task, finish_task},
                    timeout=playback_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    playback_task.cancel()
                    raise TimeoutError(
                        f"Message did not finish within {playback_timeout:.1f} seconds"
                    )
                if not playback_task.done():
                    playback_task.cancel()
                    raise RuntimeError(
                        f"{finish_reason}; notification playback was interrupted"
                    )
                await playback_task

                if index + 1 < repetitions and pause_seconds:
                    await call.call_handler.sleep(pause_seconds)
        finally:
            answer_task.cancel()
            finish_task.cancel()
            if not getattr(call, "_is_call_stopped", False):
                try:
                    await call.call_handler.hangup()
                except Exception:
                    logger.debug("Call cleanup failed", exc_info=True)
            if not call_task.done():
                try:
                    await asyncio.wait_for(call_task, timeout=5)
                except TimeoutError:
                    call_task.cancel()

    @staticmethod
    def _playback_timeout(stream: Any) -> float:
        # CALL_TIMEOUT_SECONDS is only for waiting for an answer. PySIPio
        # exposes the decoded WAV duration, so allow the full message plus
        # enough margin for RTP scheduling and a temporarily busy event loop.
        audio_length = float(getattr(stream, "audio_length", 0))
        return max(15.0, audio_length + 15.0)

    @staticmethod
    def _enable_digest_auth_compat(call: Any) -> None:
        """Add digest and confirmed-dialog fixes missing from PySIPio 1.8.0."""

        async def capture_dialog_response(message: Any) -> None:
            status = getattr(getattr(message, "status", None), "code", None)
            if status == 200 and getattr(message, "method", None) == "INVITE":
                call._pysip_notifier_dialog_response = message

        # This must run before PySIPio's own message handler generates the ACK.
        sip_core = getattr(call, "sip_core", None)
        if sip_core is not None:
            sip_core.on_message_callbacks.insert(0, capture_dialog_response)

        def extract_auth_details(self: Any, received_message: Any):
            header = received_message.get_header("WWW-Authenticate") or ""
            parameters = {
                match.group(1).lower(): match.group(3) or match.group(4) or ""
                for match in _DIGEST_PARAMETER.finditer(header)
            }
            self._pysip_notifier_digest = parameters
            return (
                parameters.get("nonce") or getattr(received_message, "nonce", ""),
                parameters.get("realm") or getattr(received_message, "realm", ""),
                received_message.public_ip,
                received_message.rport,
            )

        def generate_auth_header(
            self: Any,
            method: str,
            uri: str,
            nonce: str,
            realm: str,
        ) -> str:
            parameters = dict(getattr(self, "_pysip_notifier_digest", {}))
            parameters["nonce"] = nonce
            parameters["realm"] = realm
            return _build_digest_authorization(
                self.username,
                self.password,
                method,
                uri,
            parameters,
        )

        def ack_generator(self: Any, transaction: Any) -> str:
            response = getattr(self, "_pysip_notifier_dialog_response", None)
            if response is None:
                return self._pysip_notifier_original_ack_generator(transaction)

            request_uri = _contact_uri(response.get_header("Contact"))
            if not request_uri:
                request_uri = (
                    f"sip:{self.callee}@{self.server}:{self.port};"
                    f"transport={self.CTS}"
                )
            _, local_port = self.sip_core.get_extra_info("sockname")
            branch = self.sip_core.gen_branch()
            message = (
                f"ACK {request_uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/{self.CTS} {self.my_public_ip}:{local_port};"
                f"rport;branch={branch};alias\r\n"
                "Max-Forwards: 70\r\n"
                f"From: {response.get_header('From')}\r\n"
                f"To: {response.get_header('To')}\r\n"
                f"Call-ID: {self.call_id}\r\n"
                f"CSeq: {response.cseq} ACK\r\n"
            )
            record_route = response.get_header("Record-Route")
            if record_route:
                message += f"Route: {record_route}\r\n"
            return message + "Content-Length: 0\r\n\r\n"

        def bye_generator(self: Any) -> str:
            response = getattr(self, "_pysip_notifier_dialog_response", None)
            if response is None:
                return self._pysip_notifier_original_bye_generator()

            request_uri = _contact_uri(response.get_header("Contact"))
            if not request_uri:
                request_uri = (
                    f"sip:{self.callee}@{self.server}:{self.port};"
                    f"transport={self.CTS}"
                )
            _, local_port = self.sip_core.get_extra_info("sockname")
            branch = self.sip_core.gen_branch()
            transaction = self.dialogue.add_transaction(branch, "BYE")
            message = (
                f"BYE {request_uri} SIP/2.0\r\n"
                f"Via: SIP/2.0/{self.CTS} {self.my_public_ip}:{local_port};"
                f"rport;branch={branch};alias\r\n"
                "Max-Forwards: 70\r\n"
                f"From: {response.get_header('From')}\r\n"
                f"To: {response.get_header('To')}\r\n"
                f"Call-ID: {self.call_id}\r\n"
                f"CSeq: {transaction.cseq} BYE\r\n"
                'Reason: Q.850;cause=16;text="normal call clearing"\r\n'
            )
            record_route = response.get_header("Record-Route")
            if record_route:
                message += f"Route: {record_route}\r\n"
            return message + "Content-Length: 0\r\n\r\n"

        call.extract_auth_details = MethodType(extract_auth_details, call)
        call.generate_auth_header = MethodType(generate_auth_header, call)
        if hasattr(call, "ack_generator"):
            call._pysip_notifier_original_ack_generator = call.ack_generator
            call.ack_generator = MethodType(ack_generator, call)
        if hasattr(call, "bye_generator"):
            call._pysip_notifier_original_bye_generator = call.bye_generator
            call.bye_generator = MethodType(bye_generator, call)


def _md5(value: str) -> str:
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


def _parse_digest_challenge(header: str) -> dict[str, str]:
    return {
        match.group(1).lower(): match.group(3) or match.group(4) or ""
        for match in _DIGEST_PARAMETER.finditer(header)
    }


def _contact_uri(contact: str | None) -> str | None:
    if not contact:
        return None
    match = re.search(r"<([^>]+)>", contact)
    if match:
        return match.group(1)
    return contact.split(";", 1)[0].strip()


def _build_digest_authorization(
    username: str,
    password: str,
    method: str,
    uri: str,
    parameters: dict[str, str],
) -> str:
    nonce = parameters.get("nonce", "")
    realm = parameters.get("realm", "")
    algorithm = parameters.get("algorithm", "MD5")
    if algorithm.upper() not in {"MD5", "MD5-SESS"}:
        raise RuntimeError(
            f"Unsupported SIP digest algorithm requested by server: {algorithm}"
        )

    qop_options = {
        item.strip().lower()
        for item in parameters.get("qop", "").split(",")
        if item.strip()
    }
    qop = "auth" if "auth" in qop_options else None
    cnonce = secrets.token_hex(8)
    nonce_count = "00000001"

    ha1 = _md5(f"{username}:{realm}:{password}")
    if algorithm.upper() == "MD5-SESS":
        ha1 = _md5(f"{ha1}:{nonce}:{cnonce}")
    ha2 = _md5(f"{method}:{uri}")
    if qop:
        response = _md5(f"{ha1}:{nonce}:{nonce_count}:{cnonce}:{qop}:{ha2}")
    else:
        response = _md5(f"{ha1}:{nonce}:{ha2}")

    fields = [
        f'username="{username}"',
        f'realm="{realm}"',
        f'nonce="{nonce}"',
        f'uri="{uri}"',
        f'response="{response}"',
        f"algorithm={algorithm}",
    ]
    if parameters.get("opaque"):
        fields.append(f'opaque="{parameters["opaque"]}"')
    if qop:
        fields.extend(
            [f"qop={qop}", f"nc={nonce_count}", f'cnonce="{cnonce}"']
        )
    return "Authorization: Digest " + ", ".join(fields) + "\r\n"


def _address_family(address: str | None) -> str:
    if not address:
        return "unknown"
    try:
        return f"IPv{ipaddress.ip_address(address).version}"
    except ValueError:
        return "invalid"
