# Nutri-AI backend — hosted inference-free API image.
#
# Deliberately mirrors model_api/Dockerfile in shape (slim base, non-root user,
# $PORT from the environment, curl only for the healthcheck) so the two
# services fail in the same ways and can be debugged with the same reflexes.
#
# The difference that matters: this image installs requirements-deploy.txt, so
# there is no torch here. The classifier lives in the Space that CLASSIFIER_URL
# points at; everything else takes the guarded heuristic path. See
# backend/requirements-deploy.txt for why that is a supported state.

FROM python:3.11-slim

# scipy and scikit-learn ship manylinux wheels, so no compiler is needed.
# curl is here only so HEALTHCHECK below can run.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR $HOME/app

# Requirements first so this layer survives code edits.
COPY --chown=user backend/requirements-deploy.txt ./
RUN pip install --no-cache-dir --user -r requirements-deploy.txt

# The application. backend/models/ is gitignored and intentionally not copied:
# this image holds no weights at all.
COPY --chown=user backend/ ./

USER user

# config.py creates UPLOAD_DIR at import time. On Render's free plan this is an
# ephemeral layer — meal photos do not survive a redeploy or a wake-from-sleep,
# and _media_urls_for() returns (None, None) for the missing file so history
# rows render without a thumbnail instead of erroring. Accepted for v1; the fix
# is object storage behind _image_paths()/_media_urls_for().
ENV UPLOAD_DIR=/home/user/app/uploads

# Render injects $PORT and scans for a process bound to it. A hardcoded port
# here is the single most common reason a Render deploy goes live but never
# passes its port scan, so the CMD below reads it at runtime.
ENV PORT=8000
EXPOSE 8000

# Hits the existing /api/health (main.py:281 — note the /api prefix; every
# route in this app carries it except /media/{id}). start-period is generous
# because lifespan() runs init_db(), a stale-draft sweep and warm_models()
# before the app serves — and on a cold Postgres the first connection is not
# instant.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/api/health" || exit 1

# One worker: the box is 512 MB, and a second worker would duplicate the
# numeric stack for no throughput gain on a single shared CPU. Shell form so
# ${PORT} expands at runtime rather than being passed as a literal.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
