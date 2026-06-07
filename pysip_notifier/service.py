import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pysip_notifier.config import Settings
from pysip_notifier.models import Job, JobStatus, NotifyRequest

logger = logging.getLogger(__name__)


class Synthesizer(Protocol):
    async def synthesize(self, message: str, voice: str) -> Path: ...


class Caller(Protocol):
    registered: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def call_and_play(
        self,
        destination: str,
        audio_path: Path,
        repetitions: int,
        pause_seconds: float,
        timeout_seconds: int,
    ) -> None: ...


class NotificationService:
    def __init__(
        self,
        settings: Settings,
        synthesizer: Synthesizer,
        caller: Caller,
    ) -> None:
        self.settings = settings
        self.synthesizer = synthesizer
        self.caller = caller
        self.jobs: dict[UUID, Job] = {}
        self.queue: asyncio.Queue[UUID] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._worker(), name="notification-worker")
        if self.settings.sip_configured:
            try:
                await self.caller.start()
            except Exception:
                logger.exception("SIP caller initialization failed; calls will retry")

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        await self.caller.stop()

    def enqueue(self, request: NotifyRequest) -> Job:
        destination = request.destination or self.settings.sip_destination
        if not destination:
            raise ValueError("No destination supplied and SIP_DESTINATION is not configured")
        if not self.settings.sip_configured:
            raise RuntimeError(
                "SIP is not configured; set SIP_USERNAME, SIP_PASSWORD, and SIP_SERVER"
            )

        job = Job(
            message=request.message.strip(),
            destination=destination,
            voice=request.voice or self.settings.tts_voice,
            repetitions=request.repetitions or self.settings.call_repetitions,
        )
        self.jobs[job.id] = job
        self.queue.put_nowait(job.id)
        return job

    def get(self, job_id: UUID) -> Job | None:
        return self.jobs.get(job_id)

    async def _worker(self) -> None:
        while True:
            job_id = await self.queue.get()
            job = self.jobs[job_id]
            audio_path: Path | None = None
            try:
                job.status = JobStatus.SYNTHESIZING
                job.started_at = datetime.now(timezone.utc)
                audio_path = await self.synthesizer.synthesize(job.message, job.voice)

                job.status = JobStatus.CALLING
                await self.caller.call_and_play(
                    job.destination,
                    audio_path,
                    job.repetitions,
                    self.settings.call_pause_seconds,
                    self.settings.call_timeout_seconds,
                )
                job.status = JobStatus.COMPLETED
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Notification job %s failed", job.id)
                job.status = JobStatus.FAILED
                job.error = str(exc)
            finally:
                if audio_path is not None:
                    audio_path.unlink(missing_ok=True)
                job.finished_at = datetime.now(timezone.utc)
                self.queue.task_done()
