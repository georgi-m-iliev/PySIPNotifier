from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, status

from pysip_notifier.config import Settings
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

    @app.get("/api/v1/jobs/{job_id}", response_model=Job)
    async def get_job(job_id: UUID, request: Request) -> Job:
        current_service: NotificationService = request.app.state.service
        job = current_service.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return job

    return app


app = create_app()
