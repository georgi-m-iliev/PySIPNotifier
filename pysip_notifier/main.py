import uvicorn

from pysip_notifier.config import Settings


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "pysip_notifier.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()

