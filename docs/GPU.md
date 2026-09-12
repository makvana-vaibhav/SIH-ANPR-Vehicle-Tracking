# GPU acceleration

Short version: **on this development Mac, inside Docker, there is no GPU and no
setting that creates one.** Everything below explains what is real where, so
nobody spends an afternoon on a switch that cannot exist.

---

## What onnxruntime actually reports

Not inferred — read from the running worker:

```
$ docker exec nagarnetra-ai-worker python -c \
    "import onnxruntime as ort; print(ort.get_available_providers())"
['AzureExecutionProvider', 'CPUExecutionProvider']
```

`AzureExecutionProvider` is a remote-inference client, not local hardware. So
the only compute available in the container is `CPUExecutionProvider`.

## Why, and why it is not fixable here

Docker Desktop on Apple Silicon runs Linux in **Virtualization.framework**,
which passes no GPU into the guest. There is no Metal device, no CoreML, and no
CUDA to forward — Apple Silicon has no NVIDIA hardware at all. This is a
property of the virtualisation layer:

* `--gpus all` is a no-op: it needs the NVIDIA container toolkit and an NVIDIA card.
* `onnxruntime-gpu` will not help — it is a CUDA build, and there is no CUDA.
* There is no "enable GPU" switch in Docker Desktop that changes this.

The CPU-only constraint is recorded in CLAUDE.md §6 and is why every GPU figure
in the docs is labelled **extrapolated**, never *measured*.

---

## The four real paths

| Where | Provider | Status | Use |
|---|---|---|---|
| Docker on Apple Silicon | `CPUExecutionProvider` | **What the demo runs.** | Development, the demo laptop |
| macOS natively (no Docker) | `CoreMLExecutionProvider` | Supported by the code; needs a host install | Getting more cameras out of this Mac |
| Linux + NVIDIA | `CUDAExecutionProvider` | Supported by the code; not testable here | Production / the city deployment |
| **Windows, any DirectX12 GPU** | `DmlExecutionProvider` | Supported by the code; not testable here | A Windows demo/judge laptop, including one with only integrated graphics |

`device` is set per model in the pipeline config and accepts
`auto` (default), `cpu`, `cuda`, `coreml`, `dml`.

`auto` takes the best provider actually present. An explicit `cuda`, `coreml`
or `dml` **raises at startup** when that provider is missing, rather than
silently running on CPU — a GPU deployment that quietly falls back is a
capacity plan wrong by an order of magnitude that reports nothing.

---

## Path 4 — DirectML, for a Windows demo machine

DirectML is Microsoft's DirectX12-based ML acceleration layer. The reason it
belongs in this list even though CUDA already covers "GPU deployment" is that
it is **not NVIDIA-only** — it reaches any DirectX12 device, including the
integrated Intel/AMD graphics most laptops actually have. CUDA needs a
discrete NVIDIA card; DirectML needs Windows and nothing else.

```
pip uninstall onnxruntime
pip install onnxruntime-directml
python -c "import onnxruntime as ort; print(ort.get_available_providers())"
# expect DmlExecutionProvider in the list
```

Then set `device: dml` on the detector(s) in the pipeline config.

**Two cautions before trusting the result, same spirit as the CoreML section
above:**

* DirectML is not automatically faster for models this small. Its own docs
  note that a naive pipeline pays CPU↔GPU transfer on every call, which can
  cost more than these YOLOv8n/YOLO11n-scale models take to run outright.
  Whether it pays has to be **measured on the actual demo hardware**, and
  that measurement has not been run here — this project has no Windows+GPU
  box to run it on today.
* Like the CUDA path, `onnxruntime-directml` and plain `onnxruntime` cannot
  both be installed — swapping one for the other is a `requirements.txt`
  change for whichever image runs on Windows, not something to bake into the
  shared default (`docs/GPU.md`'s CPU-only image is what ships everywhere
  else).

`scripts/capacity_model.py --gpu-speedup` models the fleet-sizing consequence
of a GPU deployment; its speedup factor is an assumption for DirectML exactly
as it already is for CUDA, until measured.

---

## Path 2 — CoreML, by running the worker on the host

The Apple GPU and Neural Engine are reachable from macOS, just not from a
container. So the worker runs on the host and talks to the containerised stack
over published ports. Everything else — Postgres, Redis, MediaMTX, the API —
stays in Docker.

```bash
make up                      # the platform, without the AI profile

python3 -m venv .venv-worker && source .venv-worker/bin/activate
pip install -r services/ai-worker/requirements.txt
pip install -e ai-lab

python3 -c "import onnxruntime as ort; print(ort.get_available_providers())"
# expect CoreMLExecutionProvider in the list
```

Point it at the published ports rather than the compose hostnames:

```bash
REDIS_URL=redis://localhost:6379/0 \
MEDIAMTX_HOST=localhost \
MEDIAMTX_API_URL=http://localhost:9997 \
MINIO_ENDPOINT=localhost:9000 \
AI_MODELS_DIR=./ai-lab/models \
AI_WORKER_MAX_CAMERAS=6 \
PYTHONPATH=services/ai-worker \
python3 -m ai_worker
```

**Two cautions before trusting the result.**

CoreML is not automatically faster. It compiles a subgraph to ANE/GPU and runs
the rest on CPU, and for models this small the partition overhead can exceed
the win — YOLOv8n at 640px is a few hundred milliseconds of CPU work, not
seconds. Whether it pays has to be **measured on the actual models**, and
that measurement has not been run here.

And this path is not the demo path. It needs a Python environment on the host,
which contradicts the "one laptop, `docker compose up`" constraint in
CLAUDE.md §3. Treat it as a way to get more cameras out of this Mac during
development, never as what a judge runs.

---

## Path 3 — CUDA, for a real deployment

On a Linux host with an NVIDIA card and the container toolkit installed:

1. Swap `onnxruntime` for `onnxruntime-gpu` in `services/ai-worker/requirements.txt`.
2. Give the service a device reservation in `docker-compose.yml`:

   ```yaml
   ai-worker:
     deploy:
       resources:
         reservations:
           devices:
             - driver: nvidia
               count: 1
               capabilities: [gpu]
   ```

3. Set `device: cuda` in the detector and plate sections of the pipeline
   config, so a missing provider fails loudly instead of falling back.
4. Raise `AI_WORKER_MAX_CAMERAS` — the CPU thread budget stops being the
   constraint once inference leaves the CPU, and decode becomes the limit.

`scripts/capacity_model.py --gpu-speedup` models the fleet-sizing consequence.
Its speedup factor is an **assumption** until someone runs the harness on real
GPU hardware, and the script labels it as one.

---

## What to do instead, on this machine

The CPU path had far more headroom than the missing GPU would have provided,
and it was being wasted rather than used. Before tuning, the worker ran **168
OS threads on 10 cores at 866% CPU** because every ONNX session sized its
thread pool as though it were alone on the machine, and the worker runs one
pipeline per camera.

`ailab/runtime.py` now divides one process-wide budget across the cameras. See
[PERFORMANCE.md](PERFORMANCE.md) for the measurements and the knobs.
