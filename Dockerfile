# LinguaHaru Web (FastAPI) — document/text translation, ready to serve.
#
#   docker build -t linguaharu .
#   docker run -p 8080:8080 -e LINGUAHARU_ADMIN_PASSWORD=change-me \
#     -v linguaharu-data:/app/data linguaharu
#
# Then open http://<host>:8080 . The container binds 0.0.0.0 (HOST below), so a
# published port works out of the box — no lan_mode/server_mode needed.
#
# Admin (changing model/key/settings from a remote browser) is guarded because
# the bind is external: set LINGUAHARU_ADMIN_PASSWORD and enter it when the UI
# prompts. Configuration:
#   HOST / PORT                bind address + port (both env-overridable)
#   LINGUAHARU_ADMIN_PASSWORD  remote-admin password (else remote admin refused)
#   LINGUAHARU_API_KEY         server-side translation key (for public deploy)
#   RENDER=1 / server_mode     public-deploy lockdown (hide admin UI, server key)
#
# The optional engines (image/manga OCR, scanned-PDF, video) are large
# (torch/paddle/onnxruntime) and GPU needs the NVIDIA container toolkit — install
# requirements/ocr.txt etc. in a derived image if you need them.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080

# Install deps first (layer-cached across code changes).
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/base.txt -r requirements/web.txt

COPY . .

# Mutable runtime state (models, results, history, uploads) — mount a volume here
# so it survives container recreation.
VOLUME ["/app/data"]

EXPOSE 8080
CMD ["python", "-m", "webapp.server"]
