# PySIP Notifier

PySIP Notifier is a local HTTP service that turns notification text into a
phone call. It uses:

- [PySIPio](https://pypi.org/project/PySIPio/) for SIP registration, calls, and
  RTP audio.
- [edge-tts](https://github.com/rany2/edge-tts) for speech synthesis.
- FastAPI for a small local API.

Calls are processed one at a time. The API immediately returns a job ID, while
a background worker synthesizes the message, calls the destination, plays the
message, and hangs up.

## Requirements

- Python 3.11 or newer
- A SIP account that supports outbound calls
- Internet access for the online edge-tts service

## Setup

Edit `.env` and provide at least:

```dotenv
SIP_USERNAME=...
SIP_PASSWORD=...
SIP_SERVER=sip.provider.example:5060
SIP_CONNECTION_TYPE=UDP
SIP_DESTINATION=...
```

`SIP_DESTINATION` is the default number or SIP extension. A request can
override it with `destination` or `to`.

Outbound calls use `SIP_USERNAME` as the SIP caller identity. `SIP_CALLER_ID`
is currently ignored because many Asterisk endpoints reject authenticated
INVITEs when the `From` user differs from the account username.

edge-tts audio is converted in-process to an 8 kHz mono WAV before playback,
so a separate `ffmpeg` installation is not required.

Start the service:

```bash
uv run .\pysip_notifier\main.py
```

The default address is `http://127.0.0.1:8080`. Interactive API documentation
is at `http://127.0.0.1:8080/docs`.

## API

Queue a call:

```bash
curl -X POST "http://127.0.0.1:8080/api/v1/notify" \
  -H "Content-Type: application/json" \
  -d '{"message":"The tracker has left the home geofence."}'
```

Optional fields:

```json
{
  "message": "Engine started",
  "destination": "359123456789",
  "voice": "en-US-GuyNeural",
  "repetitions": 2
}
```

The response has a job ID:

```json
{"id":"2be3a716-829c-4ea5-a02f-842cfcd4d6fe","status":"queued"}
```

Check it with:

```text
GET /api/v1/jobs/{id}
```

Health and SIP caller readiness are available at:

```text
GET /health
```

## Configuration

See [.env.example](.env.example) for all options. Useful defaults include:

- `TTS_VOICE=en-US-AriaNeural`
- `CALL_TIMEOUT_SECONDS=60` (answer timeout only)
- `CALL_REPETITIONS=1`
- `CALL_PAUSE_SECONDS=1`
- `API_HOST=127.0.0.1`
- `API_PORT=8080`

List available edge-tts voices with:

```bash
python -m edge_tts --list-voices
```

## Notes

- The service intentionally has no authentication and binds to loopback by
  default. Do not expose it publicly without adding access controls.
- Job state is held in memory and resets when the process restarts.
- SIP and NAT behavior varies by provider. If calls connect without audio,
  verify UDP/RTP firewall rules and the provider's supported codecs.
