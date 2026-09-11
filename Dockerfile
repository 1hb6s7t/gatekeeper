# Deployable image for a hosted (long-lived) instance of the demo.
#
# Defaults to the offline cache channel, so a hosted instance needs no model key
# at all: the bundled cache serves the entire sample walkthrough, which is the
# path a reviewer takes.  Set GATEKEEPER_PROVIDER=openai and put a key in the
# host's secret store only if you also want strangers' manuscripts answered by a
# model - and read the note on that in README.md first.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    GATEKEEPER_PROVIDER=cache \
    GATEKEEPER_CACHE_DIR=data/cache_demo \
    GATEKEEPER_CACHE_FALLBACK_DIR=data/cache \
    GATEKEEPER_LIMIT_STATE=data/limits.json \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# static/, samples/ and the shipped data/cache/ are all part of the demo; the
# recorded walkthrough rides along so /report.mp4 works on the hosted instance.
COPY app/ app/
COPY static/ static/
COPY samples/ samples/
COPY data/cache/ data/cache/
COPY docs/report.mp4 docs/report.mp4

# Written at runtime: projects, the visitor cache, the budget counter.
RUN mkdir -p data/projects data/cache_demo

EXPOSE 8080

# One worker on purpose.  The concurrency slot and the per-visitor window live in
# process memory, so a second worker would double every limit, and the daily
# counter is a read-modify-write on a file that separate processes would race.
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT}"]
