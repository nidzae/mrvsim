# Hosting MRVSim online

The repository contains a `Dockerfile` that serves the API and the built front end from one container. It is written for Google Cloud Run (free tier covers light use) but runs on any container host.

## What the hosted version does and does not do

- **Does:** everything in the app except the items below. Two demo runs are included so the first visitor sees a map; new runs are saved on the mounted volume.
- **Run size is capped** (`MRVSIM_ONLINE=1`): quick-mode size only (30 sites per stratum, 10 estimated, 3 replications, 2,000 draws; optimizer at most 8 trials). Full-resolution runs and validation re-runs are refused with a message; the Validation panel shows the committed results.
- **Ask-AI gap analysis is switched off** unless `ANTHROPIC_API_KEY` is set; the tab is greyed out.
- **Password:** set `MRVSIM_PASSWORD` and every request needs it (HTTP Basic; any user name). Leave it unset for a public demo.
- **First run after a restart recomputes the satellite-overpass cache** (about two extra minutes) unless the cache directory is on a persistent volume.

## Environment variables

| Variable | Meaning | Default in the image |
|---|---|---|
| `PORT` | listening port | 8080 |
| `MRVSIM_RUNS_DIR` | where runs are saved; mount a volume here to keep them | `/data/runs` |
| `MRVSIM_CACHE_DIR` | satellite-overpass cache; same volume | `/data/cache` |
| `MRVSIM_ONLINE` | `1` caps run size and refuses long jobs | `1` |
| `MRVSIM_PASSWORD` | if set, required for every request | unset |
| `ANTHROPIC_API_KEY` | enables the Ask-AI tab | unset |

## Google Cloud Run, step by step (no local Docker needed)

Done once by the account owner, in the browser:

1. Create a Google Cloud project and attach a billing account (required even inside the free tier). New accounts get a trial credit.
2. **Cloud Run → Create service → "Continuously deploy from a repository"** → connect GitHub → pick `nidzae/mrvsim`, branch `main`, build type **Dockerfile** (`/Dockerfile`).
3. Settings: region near you; **CPU allocation: always allocated** (runs continue in a background thread after the request returns); **2 vCPU, 4 GiB**; minimum instances 0, maximum 1; **concurrency 20**; request timeout 3600 s; allow unauthenticated invocations (the app has its own password).
4. **Volumes (optional but recommended):** add a Cloud Storage bucket volume mounted at `/data` so runs and the overpass cache survive restarts. Create the bucket first (same region).
5. **Variables and secrets:** `MRVSIM_PASSWORD` (as a secret or variable). Leave `ANTHROPIC_API_KEY` unset to keep Ask-AI off.
6. Deploy. Every push to `main` rebuilds and redeploys. Open the service URL; the browser asks for the password.

Equivalent command line, if `gcloud` is installed and logged in:

```bash
gcloud run deploy mrvsim --source . --region us-central1 --cpu 2 --memory 4Gi --no-cpu-throttling \
  --max-instances 1 --concurrency 20 --timeout 3600 --allow-unauthenticated \
  --set-env-vars MRVSIM_ONLINE=1 --set-env-vars MRVSIM_PASSWORD=choose-one \
  --add-volume name=data,type=cloud-storage,bucket=YOUR_BUCKET --add-volume-mount volume=data,mount-path=/data
```

## Cost and limits

- Idle: scales to zero, no charge. Active: about 2 vCPU-hours per hour of use; the free tier covers roughly 25 hours a month at this size, after which about $0.10 per hour of use. A bucket volume costs cents per month at these sizes.
- One instance: a run started by one visitor occupies the server for two to three minutes; others see it in the job status. Raise `--max-instances` for more capacity, but each instance has its own in-memory job list and (without the volume) its own runs.
- Cold start after idle: about 15 seconds.

## Other hosts

Render, Railway and Fly.io all accept the same Dockerfile; set the environment variables above and attach a persistent disk at `/data`. Their free tiers are too small (memory) for this image; expect about $15–35 per month.

## Testing the hosted configuration locally

```bash
MRVSIM_ONLINE=1 MRVSIM_PASSWORD=secret MRVSIM_RUNS_DIR=/tmp/mrv/runs MRVSIM_CACHE_DIR=/tmp/mrv/cache \
  .venv/bin/uvicorn mrvsim.api.server:app --port 8080
```
