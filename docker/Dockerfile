# Image for: api, discovery, matcher, tracker, startups, scheduler, dashboard
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JOBAGENT_DATA_DIR=/data \
    JOBAGENT_CONFIG=/app/config/profile.toml

WORKDIR /app
COPY requirements-services.txt ./
RUN pip install -r requirements-services.txt

COPY jobagent ./jobagent
COPY services ./services
COPY dashboard ./dashboard
COPY .streamlit ./.streamlit
COPY config/profile.example.toml config/startups.toml ./config/

RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown -R app:app /data /app
USER app
