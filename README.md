# AuralMind Mastering Backend

High-fidelity, reference-aware audio mastering service exposed through FastAPI and designed for containerized production.

## Project Overview

This repository provides:

- A mastering DSP engine (`auralmind_match_maestro_v7_3_expert1.py`)
- A FastAPI backend (`backend/`) for upload, async job execution, status, report, and download APIs
- Dockerized deployment with production-safe defaults and health checks
- Upload controls with Demucs stem separation modes (`Auto`, `On`, `Off`) in Advanced settings

The engine is optimized for transparent loudness control, stereo integrity, and transient preservation while supporting reference matching and configurable mastering presets.

## Runtime Artifact Policy

- `backend/data/jobs/` stores runtime job inputs, logs, reports, and rendered outputs.
- These artifacts are intentionally not tracked in git and should be treated as local/runtime data only.

## Ollama Cloud (Default) with Local Fallback

AI routes (`/api/ai/recommend`, `/api/ai/mastering-director`, preflight advisor) use a cloud-first Ollama strategy aligned to the official API:

- Chat endpoint: `POST /api/chat`
- Cloud docs: https://docs.ollama.com/cloud
- Chat docs: https://docs.ollama.com/api/chat
- Auth docs: https://docs.ollama.com/api/authentication
- Error/status docs: https://docs.ollama.com/api/errors

Request order:

1. If `AURALMIND_AI_BASE_URL` is set, only that endpoint is used (no fallback).
2. Otherwise, cloud is attempted first (`OLLAMA_BASE_URL_CLOUD`, default `https://ollama.com`).
3. Local Ollama is used as fallback (`OLLAMA_BASE_URL_LOCAL`, default `http://localhost:11434`).

Fallback triggers:

- Network/connectivity errors (including timeouts)
- HTTP `401`, `403`, `429`
- HTTP `5xx`

No fallback is attempted for other `4xx` request-shape/validation errors (for example, malformed payloads).

Model selection:

- Cloud uses `AURALMIND_AI_MODEL` (default: `glm-5:cloud`)
- Local fallback uses `AURALMIND_AI_MODEL_LOCAL` when set, otherwise it reuses `AURALMIND_AI_MODEL`

Quick validation:

```bash
# 1) Cloud-first success (no fallback expected)
curl -sS http://localhost:8000/api/health

# 2) Force cloud failure then verify local fallback:
#    set an invalid cloud URL and keep local Ollama running.
export OLLAMA_BASE_URL_CLOUD=http://127.0.0.1:1
```

Expected behavior:

- If local Ollama is healthy and model is available, AI responses still succeed.
- If local model is missing, backend errors include guidance to set `AURALMIND_AI_MODEL_LOCAL`.

## Audio Engine Architecture Summary

Processing flow (master bus):

1. Input decode + stereo normalization + sample-rate unification (48 kHz target).
2. Safety high-pass (DC/rumble protection).
3. Optional stem pre-pass (Demucs-based separation, if enabled).
4. Note-aware mono-sub stabilization.
5. Match-EQ curve design + linear-phase FIR convolution.
6. Tone and control stages:
 - Dynamic masking EQ (MID-focused)
 - De-esser
 - Harmonic glow
 - Stereo realism + microshift + microdetail
 - Section-aware movement/hook lift
 - Transient sculpt
7. Loudness governor loop:
 - LUFS target search
 - Soft clip + true-peak limiter
8. Export with optional TPDF dithering and report generation.

## Optimization Strategy Summary

### Sound-quality Enhancements

1. **Phase-coherent MID dynamic masking EQ**

- Replaced static full-track masking behavior with short-term masking detection and a time-varying dip envelope.
- Processing is MID-only to protect side-image coherence.
- Zero-phase filtering (`filtfilt` with safety fallback) avoids additional phase rotation in critical mids.

1. **Lookahead soft-knee split-band de-esser**

- Upgraded de-essing from simple static envelope reduction to lookahead-aware, soft-knee, attack/release-smoothed gain control.
- Split-band attenuation keeps harmonic body untouched while reducing sibilant spikes.
- Uses phase-coherent filtering path for cleaner recombination.

### Performance Enhancements

1. **Batched vectorized spectral analysis**

- `windowed_fft_mag` moved from frame-by-frame Python loops to stride-view batching with vectorized FFT.
- Reduces Python overhead and improves cache locality.

1. **Governor loudness reuse + DSP coefficient caching**

- LUFS baseline is now computed once before governor binary search and reused for each target iteration.
- Added cached filter coefficient builders for repeated Butterworth designs.
- Retains exact numerical behavior for equivalent inputs.

## Performance Benchmarks (Measured + Modeled)

Local microbenchmarks on this codebase (Python 3.11, NumPy/SciPy, CPU-only):

- `windowed_fft_mag` (180 s mono, 8192 FFT / 2048 hop): **2.19x faster** (3.4837 s -> 1.5881 s)
- Governor gain-prep loop (`apply_lufs_gain`, 11 targets): **12.96x faster** (3.1737 s -> 0.2449 s) by LUFS reuse
- Repeated identical Butterworth design (`butter_bandpass`, 20k calls): **~208x faster** with coefficient cache

Modeled end-to-end impact (4 min stereo master, no Demucs):

- Overall wall-time reduction: **~12% to 24%** depending limiter oversampling and governor steps.

## Docker Deployment Instructions

### 1. Build

```bash
docker build -t auralmind-mastering-backend:1.0.0 .
```

### 2. Run (single container)

```bash
docker run --rm -d \
--name auralmind-mastering-backend \
-p 8000:8000 \
--cpus="4.0" \
--memory="8g" \
--ulimit nofile=65536:65536 \
--read-only \
--tmpfs /tmp:size=512m,noexec,nosuid \
-e MAX_UPLOAD_MB=300 \
-e JOB_TIMEOUT_SEC=7200 \
-e ALLOWED_ORIGINS=http://localhost:5173 \
-e OMP_NUM_THREADS=2 \
-e OPENBLAS_NUM_THREADS=2 \
-e MKL_NUM_THREADS=2 \
-e NUMEXPR_NUM_THREADS=2 \
-v auralmind_jobs:/data/jobs \
auralmind-mastering-backend:1.0.0
```

### 3. Compose

```bash
docker compose up -d --build
```

`docker-compose.yml` already includes:

- Resource limits (`cpus`, `mem_limit`)
- Persistent audio volume (`backend_jobs:/data/jobs`)
- Read-only rootfs + tmpfs scratch space
- Healthcheck to `/api/health`
- Security hardening (`no-new-privileges`)

### 4. Verify Health

```bash
curl http://localhost:8000/api/health
```

## Technical Defense of Enhancements

- **Dynamic range safety:** all new dynamics controls are bounded and softly smoothed; no hard clipping paths were introduced.
- **Stereo safety:** masking control is MID-domain, preserving SIDE phase relationships and width cues.
- **Transient safety:** de-esser uses controlled attack/release with lookahead to catch sibilants without flattening drum onsets.
- **Harmonic integrity:** split-band processing attenuates only targeted bands; full-band core remains intact.
- **SNR safety:** no lossy resampling or reduced bit depth/sample rate was introduced.
- **Determinism in deployment:** pinned requirements + multi-stage build + fixed runtime flags reduce environment drift.

## Future Roadmap

1. Add objective regression suite (LUFS, true-peak, crest, L/R correlation deltas) per commit.
2. Add optional GPU path for Demucs and configurable worker queue isolation.
3. Add persistent metadata store for job state recovery across backend restarts.
4. Add standardized ABX export package for listening tests and engineer review.

## Heroku + Netlify Deployment

### Backend on Heroku

This repository includes:

- `Procfile` with `gunicorn` + `uvicorn` worker for production serving
- `runtime.txt` pinned to Python 3.11 for SciPy compatibility

Set Heroku Config Vars:

- `ALLOWED_ORIGINS=https://auralmind.netlify.app,http://localhost:5173`
- `ALLOWED_ORIGIN_REGEX=https://.*--auralmind\.netlify\.app` (optional, for Netlify deploy previews)
- `MAX_UPLOAD_MB=200`
- `JOB_TIMEOUT_SEC=3600`

Heroku provides `PORT` automatically.

Optional overrides (only if you need custom locations):

- `AURALMIND_SCRIPT_PATH`
- `DATA_DIR`

### Frontend on Netlify

This repository includes `netlify.toml` configured for:

- Build base: `frontend`
- Build command: `npm run build`
- Publish directory: `dist`
- SPA redirect: `/* -> /index.html`

Set Netlify Environment Variable:

- `VITE_API_BASE_URL=https://auralmind-master-778194383141.herokuapp.com`

The frontend fetch layer reads `VITE_API_BASE_URL` first. If missing:

- On localhost, it falls back to `http://localhost:8000`
- In production, it falls back to same-origin `/api` (for proxy/rewrite setups)

`netlify.toml` already contains an `/api/*` redirect to Heroku as a production fallback proxy.
