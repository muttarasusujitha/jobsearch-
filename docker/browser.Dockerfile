# Image for the browser service: Chromium (Playwright) on a virtual screen you can watch
# and control from your own browser at http://localhost:6080 (noVNC).
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    JOBAGENT_DATA_DIR=/data \
    JOBAGENT_CONFIG=/app/config/profile.toml \
    DISPLAY=:99

RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb x11vnc fluxbox novnc websockify \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# Keep the pip package in step with the browsers bundled in the base image.
RUN pip install "playwright==1.48.0" "redis>=5.0"

COPY jobagent ./jobagent
COPY services ./services
COPY config/profile.example.toml config/startups.toml ./config/
COPY docker/browser-start.sh /usr/local/bin/browser-start.sh

RUN chmod +x /usr/local/bin/browser-start.sh && mkdir -p /data && chown -R pwuser:pwuser /data /app
USER pwuser
EXPOSE 6080
CMD ["browser-start.sh"]
