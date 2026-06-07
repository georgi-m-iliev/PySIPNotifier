FROM eswardudi/python-ffmpeg:latest

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8080

WORKDIR /app

RUN pip install uv

COPY pyproject.toml ./
COPY pysip_notifier ./pysip_notifier

EXPOSE 8080/tcp

CMD ["uv", "run", "pysip_notifier/main.py"]
