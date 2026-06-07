from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, WebSocket, status
from fastapi.responses import HTMLResponse

from pysip_notifier.config import Settings
from pysip_notifier.log_stream import log_broadcaster
from pysip_notifier.models import (
    HealthResponse,
    Job,
    JobAccepted,
    NotifyRequest,
)
from pysip_notifier.service import NotificationService
from pysip_notifier.sip import PySipCaller
from pysip_notifier.tts import EdgeTtsSynthesizer


def create_app(
    settings: Settings | None = None,
    service: NotificationService | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    service = service or NotificationService(
        settings,
        EdgeTtsSynthesizer(
            rate=settings.tts_rate,
            volume=settings.tts_volume,
            pitch=settings.tts_pitch,
        ),
        PySipCaller(settings),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log_broadcaster.install(settings.log_level)
        await service.start()
        yield
        await service.stop()

    app = FastAPI(
        title="PySIP Notifier",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.service = service

    @app.get("/", response_class=HTMLResponse)
    async def logs_page() -> str:
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PySIP Notifier Logs</title>
  <style>
    :root { color-scheme: dark; }
    body {
      margin: 0;
      background: #0b1020;
      color: #d6e2ff;
      font: 14px/1.45 ui-monospace, SFMono-Regular, Consolas, monospace;
    }
    header {
      position: sticky;
      top: 0;
      padding: 12px 16px;
      background: #111936;
      border-bottom: 1px solid #26345f;
      display: flex;
      justify-content: space-between;
      gap: 16px;
    }
    main { padding: 12px 16px; white-space: pre-wrap; }
    .muted { color: #91a1c7; }
    .ok { color: #91f7b2; }
    .bad { color: #ff9d9d; }
  </style>
</head>
<body>
  <header>
    <strong>PySIP Notifier Logs</strong>
    <span id="state" class="muted">connecting...</span>
  </header>
  <main id="logs"></main>
  <script>
    const logs = document.getElementById("logs");
    const state = document.getElementById("state");
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${protocol}://${location.host}/ws/logs`);

    function append(line) {
      logs.textContent += line + "\\n";
      window.scrollTo(0, document.body.scrollHeight);
    }

    socket.onopen = () => {
      state.textContent = "connected";
      state.className = "ok";
      setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, 30000);
    };
    socket.onmessage = event => append(event.data);
    socket.onclose = () => {
      state.textContent = "disconnected";
      state.className = "bad";
    };
    socket.onerror = () => {
      state.textContent = "error";
      state.className = "bad";
    };
  </script>
</body>
</html>"""

    @app.websocket("/ws/logs")
    async def logs_socket(websocket: WebSocket) -> None:
        await log_broadcaster.connect(websocket)

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        current_service: NotificationService = request.app.state.service
        current_settings: Settings = request.app.state.settings
        return HealthResponse(
            status="ok" if current_settings.sip_configured else "unconfigured",
            sip_configured=current_settings.sip_configured,
            sip_ready=current_service.caller.registered,
            queued_calls=current_service.queue.qsize(),
        )

    @app.post("/api/v1/notify", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
    async def submit(payload: NotifyRequest, request: Request) -> JobAccepted:
        current_service: NotificationService = request.app.state.service
        try:
            job = current_service.enqueue(payload)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
        return JobAccepted(id=job.id, status=job.status)

    app.post(
        "/api/v1/traccar",
        response_model=JobAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )(submit)

    @app.get("/api/v1/jobs/{job_id}", response_model=Job)
    async def get_job(job_id: UUID, request: Request) -> Job:
        current_service: NotificationService = request.app.state.service
        job = current_service.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return job

    return app


app = create_app()
