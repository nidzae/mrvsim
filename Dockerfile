# MRVSim hosted image (docs/DEPLOY.md). Serves the API and the built front end on $PORT.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# dependencies first, so code changes do not reinstall them
COPY pyproject.toml README.md ./
COPY docs/PRD.md docs/PRD.md
RUN mkdir -p mrvsim && touch mrvsim/__init__.py && pip install . && rm -rf mrvsim

# the application: code, configs, fitted data (5 MB site table, orbit elements), docs for the in-app guide, built front end
COPY mrvsim ./mrvsim
COPY configs ./configs
COPY data/fitted ./data/fitted
COPY data/README.md ./data/README.md
COPY docs ./docs
COPY web/dist ./web/dist
COPY runs/demo ./runs/demo
# the committed validation results (V1-V7) are shown in the Validation panel; the hosted server cannot re-run them
RUN pip install --no-deps . && mkdir -p runs/validation && cp runs/demo/validation/*.json runs/validation/

# runs and the satellite-overpass cache live outside the image; mount a volume at /data to keep them across restarts
ENV MRVSIM_RUNS_DIR=/data/runs MRVSIM_CACHE_DIR=/data/cache MRVSIM_ONLINE=1 PORT=8080
EXPOSE 8080
# seed an empty runs volume with the two demo runs so the first visitor sees something
CMD ["sh", "-c", "mkdir -p $MRVSIM_RUNS_DIR $MRVSIM_CACHE_DIR; for d in /app/runs/demo/2*; do [ -e $MRVSIM_RUNS_DIR/$(basename $d) ] || cp -r $d $MRVSIM_RUNS_DIR/; done; exec uvicorn mrvsim.api.server:app --host 0.0.0.0 --port $PORT --workers 1"]
