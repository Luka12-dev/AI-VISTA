import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import json
import socket
import shutil
import threading
import queue
import traceback
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from contextlib import nullcontext
import uvicorn

# ML libs (optional)
try:
    import torch
    from diffusers import StableDiffusionXLPipeline
    ML_AVAILABLE = True
except Exception as e:
    ML_AVAILABLE = False
    ML_IMPORT_ERROR = str(e)

PROJECT_ROOT = Path(__file__).parent.resolve()
STATIC_DIR = PROJECT_ROOT  # expects index.html, styles.css, app.js in project root
CACHE_DIR = PROJECT_ROOT / "model_cache"
IMAGE_DIR = PROJECT_ROOT / "generated_images"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()

# mount /static -> project root (so /static/styles.css, /static/app.js work)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Serve index at root
@app.get("/", response_class=FileResponse)
async def index():
    idx = STATIC_DIR / "index.html"
    if not idx.exists():
        return JSONResponse({"ok": False, "msg": "index.html missing"}, status_code=500)
    return FileResponse(idx)

# convenience: also serve top-level static paths so mobile/other devices requesting '/app.js' work
@app.get("/styles.css", response_class=FileResponse)
async def styles_css():
    f = STATIC_DIR / "styles.css"
    if not f.exists():
        return JSONResponse({"ok": False, "msg": "styles.css missing"}, status_code=404)
    return FileResponse(f)

@app.get("/app.js", response_class=FileResponse)
async def app_js():
    f = STATIC_DIR / "app.js"
    if not f.exists():
        return JSONResponse({"ok": False, "msg": "app.js missing"}, status_code=404)
    return FileResponse(f)

@app.get("/index.html", response_class=FileResponse)
async def index_html():
    f = STATIC_DIR / "index.html"
    if not f.exists():
        return JSONResponse({"ok": False, "msg": "index.html missing"}, status_code=404)
    return FileResponse(f)

@app.get("/favicon.ico")
async def favicon():
    f = STATIC_DIR / "favicon.ico"
    if not f.exists():
        return Response(status_code=204)
    return FileResponse(f)

# rest of server

SUPPORTED_MODELS = [
    "stabilityai/stable-diffusion-xl-base-1.0",
    "stabilityai/stable-diffusion-xl-refiner-1.0",
    "stabilityai/stable-diffusion-2-1",
    "stabilityai/stable-diffusion-2-1-base",
    "stabilityai/stable-diffusion-2-1-unclip-small",
    "stabilityai/stable-diffusion-2",
    "stabilityai/stable-diffusion-x4-upscaler",
    "stabilityai/sdxl-vae",
    "stabilityai/sd-vae-ft-mse-original",
    "stabilityai/stable-diffusion-3-medium",
    "stabilityai/stable-diffusion-3.5-large",
    "CompVis/stable-diffusion-v1-4",
    "CompVis/stable-diffusion-v-1-4-original",
    "stable-diffusion-v1-5/stable-diffusion-v1-5",
    "dreamlike-art/dreamlike-photoreal-2.0",
    "gsdf/Counterfeit-V2.5",
    "gsdf/Counterfeit-V3.0",
    "prompthero/openjourney-v4",
    "prompthero/openjourney",
    "andite/anything-v4.0",
    "xyn-ai/anything-v4.0",
    "hakurei/waifu-diffusion-v1-4",
    "hakurei/waifu-diffusion",
    "SG161222/Realistic_Vision_V2.0",
    "SG161222/Realistic_Vision_V5.0_noVAE",
    "SG161222/RealVisXL_V1.0",
    "SG161222/RealVisXL_V2.0",
    "SG161222/Realistic_Vision_V6.0_B1_noVAE",
    "kandinsky-community/kandinsky-2-2",
    "kandinsky-community/kandinsky-2-2-decoder",
    "timbrooks/instruct-pix2pix",
    "nitrosocke/classic-anim-diffusion",
    "lambdalabs/sd-image-variations-diffusers",
    "madebyollin/taesd-x4-upscaler",
    "stablediffusionapi/realistic-vision-v20-2047",
    "stablediffusionapi/realistic-vision-2",
    "rupeshs/LCM-runwayml-stable-diffusion-v1-5",
    "prompthero-diffusion-models/openjourney-v4",
    "h94/IP-Adapter",
    "WarriorMama777/OrangeMixs",
    "Kwai-Kolors/Kolors",
    "neta-art/Neta-Lumina",
    "duongve/NetaYume-Lumina-Image-2.0",
    "SG161222/Realistic_Vision_V3.0_VAE",
    "SG161222/Realistic_Vision_V4.0_noVAE",
    "SG161222/RealVisXL_V3.0",
    "shibal1/anything-v4.5-clone",
    "x90/enterprise-demo-model"
]

def model_cached_paths(model_id: str) -> List[Path]:
    res = []
    if not CACHE_DIR.exists():
        return res
    candidates = [CACHE_DIR / model_id, CACHE_DIR / model_id.replace("/", "-"), CACHE_DIR / model_id.replace("/", "_")]
    for c in candidates:
        if c.exists() and c.is_dir():
            res.append(c)
    max_depth = 4
    for p in CACHE_DIR.rglob('*'):
        try:
            if not p.is_dir():
                continue
            if len(p.relative_to(CACHE_DIR).parts) > max_depth:
                continue
            s = str(p).lower()
            if model_id.replace("/", "-").lower() in s or model_id.lower() in s:
                res.append(p)
                continue
            for fname in ("model_index.json", "model.safetensors", "pytorch_model.bin", "config.json"):
                if (p / fname).exists():
                    res.append(p)
                    break
        except Exception:
            continue
    seen = set(); uniq = []
    for p in res:
        kp = str(p)
        if kp not in seen:
            seen.add(kp)
            uniq.append(p)
    return uniq

def is_model_cached(model_id: str) -> bool:
    return len(model_cached_paths(model_id)) > 0

def latest_generated_image() -> Optional[Path]:
    imgs = [p for p in IMAGE_DIR.iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp')]
    if not imgs:
        return None
    return max(imgs, key=lambda p: p.stat().st_mtime)

@app.get("/api/ping")
async def api_ping():
    return {"ok": True, "ml_available": ML_AVAILABLE}

@app.get("/api/models")
async def api_models():
    try:
        cached = [m for m in SUPPORTED_MODELS if is_model_cached(m)]
        return {"ok": True, "supported_models": SUPPORTED_MODELS, "cached_models": cached, "server_mode": getattr(app.state, "server_mode", "local")}
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "Failed to list models", "error": str(e)}, status_code=500)

@app.post("/api/download")
async def api_download(req: Request):
    body = await req.json()
    model_id = body.get("model_id")
    token = body.get("token")
    force = bool(body.get("force", False))
    if model_id not in SUPPORTED_MODELS:
        raise HTTPException(status_code=400, detail="Model not supported")
    if is_model_cached(model_id) and not force:
        return {"ok": True, "msg": f"Model '{model_id}' already present - skipping."}
    if token is None:
        raise HTTPException(status_code=400, detail="Provide HF token to download or place model in model_cache.")
    try:
        from huggingface_hub import snapshot_download
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"missing huggingface_hub: {e}"}, status_code=500)
    try:
        out = snapshot_download(repo_id=model_id, cache_dir=str(CACHE_DIR), use_auth_token=token)
        return {"ok": True, "msg": f"Downloaded to {out}"}
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)

@app.get("/api/preview")
async def api_preview():
    latest = latest_generated_image()
    if not latest:
        return {"ok": False, "msg": "No generated images found."}
    return {"ok": True, "url": f"/generated_images/{latest.name}"}

@app.post("/api/clear_cache")
async def api_clear_cache():
    try:
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "msg": "Model cache cleared."}
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)

app.mount("/generated_images", StaticFiles(directory=str(IMAGE_DIR)), name="generated_images")

def _safe_q_put(q: queue.Queue, obj: object):
    try:
        if obj is None:
            q.put(None)
            return
        if isinstance(obj, str):
            # assume it's already a JSON-ish string; ensure it's not empty
            s = obj.strip()
            if s == "":
                return
            q.put(s)
        else:
            q.put(json.dumps(obj))
    except Exception:
        try:
            q.put(json.dumps({"type":"error","text":"Internal serialization error"}))
        except Exception:
            try:
                q.put('{"type":"error","text":"serialization failure"}')
            except Exception:
                pass

def _load_pipeline_safe(model_id: str, dtype: torch.dtype):
    # Try float16 torch_dtype when appropriate
    load_errors = []
    if dtype == torch.float16 and torch.cuda.is_available():
        try:
            return StableDiffusionXLPipeline.from_pretrained(model_id, torch_dtype=dtype, cache_dir=str(CACHE_DIR), use_safetensors=True)
        except Exception as e:
            load_errors.append(f"torch_dtype load failed: {e}")

    # For float32 use the no-dtype call to avoid "dtype ignored" warnings
    try:
        return StableDiffusionXLPipeline.from_pretrained(model_id, cache_dir=str(CACHE_DIR), use_safetensors=True)
    except Exception as e:
        load_errors.append(f"no-dtype load failed: {e}")

    # Last-resort try (attempt passing dtype, some versions accept it)
    try:
        return StableDiffusionXLPipeline.from_pretrained(model_id, dtype=dtype, cache_dir=str(CACHE_DIR), use_safetensors=True)
    except Exception as e:
        load_errors.append(f"dtype param load failed: {e}")
        raise RuntimeError("Failed to load pipeline. Attempts:\n" + "\n".join(load_errors))

def run_generation_thread(payload: dict, q: queue.Queue):
    try:
        if not ML_AVAILABLE:
            _safe_q_put(q, {"type":"error","text":"ML libs not available", "detail": ML_IMPORT_ERROR})
            return

        model_id = payload.get("model") or payload.get("model_id") or ""
        prompt = payload.get("prompt", "")
        filename = payload.get("filename", "output.png")
        # sanitize filename (avoid path traversal)
        filename = Path(filename).name or "output.png"
        width = int(payload.get("width", 1024))
        height = int(payload.get("height", 1024))
        steps = int(payload.get("steps", 30))
        guidance = float(payload.get("guidance", 7.5))
        device_req = payload.get("device", "auto")
        precision_req = payload.get("precision", "auto")

        if model_id == "" or model_id not in SUPPORTED_MODELS:
            _safe_q_put(q, {"type":"error","text":"Model not supported or not specified"})
            return

        _safe_q_put(q, {"type":"log","text":f"[INFO] Preparing generation model={model_id} size={width}x{height} steps={steps}"})

        # decide device/dtype
        if device_req == "auto":
            use_cuda = torch.cuda.is_available()
            device = "cuda" if use_cuda else "cpu"
        else:
            device = "cuda" if device_req == "cuda" else "cpu"

        if device == "cuda":
            dtype = torch.float16 if (precision_req == "float16" or precision_req == "auto") else torch.float32
        else:
            dtype = torch.float32

        _safe_q_put(q, {"type":"log","text":f"[INFO] Using device={device} dtype={str(dtype)}"})

        if not is_model_cached(model_id):
            _safe_q_put(q, {"type":"log","text":f"[WARN] Model {model_id} not found in model_cache. from_pretrained may download."})

        # Load pipeline robustly with helper
        try:
            pipe = _load_pipeline_safe(model_id, dtype)
        except Exception as e:
            _safe_q_put(q, {"type":"error","text":"Failed to load pipeline","trace": str(e)})
            return

        # move to device safest-effort
        try:
            pipe.to(device)
        except Exception as e:
            _safe_q_put(q, {"type":"log","text":f"[WARN] pipe.to(device) failed: {e}. Continuing."})

        # memory optimizations best-effort
        try:
            if hasattr(pipe, "enable_attention_slicing"):
                try:
                    pipe.enable_attention_slicing()
                    _safe_q_put(q, {"type":"log","text":"[INFO] attention slicing enabled"})
                except Exception:
                    pass
            if hasattr(pipe, "enable_xformers_memory_efficient_attention"):
                try:
                    pipe.enable_xformers_memory_efficient_attention()
                    _safe_q_put(q, {"type":"log","text":"[INFO] xformers enabled"})
                except Exception:
                    _safe_q_put(q, {"type":"log","text":"[WARN] xformers not available"})
            if hasattr(pipe, "enable_model_cpu_offload"):
                try:
                    pipe.enable_model_cpu_offload()
                    _safe_q_put(q, {"type":"log","text":"[INFO] model CPU offload enabled"})
                except Exception:
                    pass
        except Exception:
            _safe_q_put(q, {"type":"log","text":"[WARN] memory optimization calls failed"})

        def _callback(*cb_args, **cb_kwargs):
            # robust step extraction and safe enqueue of progress
            try:
                step = None
                if len(cb_args) >= 1:
                    step = cb_args[0]
                elif 'step' in cb_kwargs:
                    step = cb_kwargs.get('step')
                try:
                    si = int(step)
                except Exception:
                    si = 0
                pct = int((si + 1) / max(1, steps) * 100)
                if device == "cuda":
                    try:
                        torch.cuda.synchronize()
                    except Exception:
                        pass
                _safe_q_put(q, {"type":"progress","value":pct})
            except Exception:
                pass
            # IMPORTANT: always return a dict to avoid diffusers pop(None) bug
            return {}

        _safe_q_put(q, {"type":"log","text":"[INFO] Starting generation..."})

        # generation with safe autocast & robust callback usage
        try:
            with torch.no_grad():
                autocast_cm = torch.cuda.amp.autocast() if (device == "cuda" and dtype == torch.float16) else nullcontext()
                with autocast_cm:
                    try:
                        # prefer callback_on_step_end (newer diffusers)
                        result = pipe(prompt, width=width, height=height, num_inference_steps=steps, guidance_scale=guidance, callback_on_step_end=_callback)
                    except TypeError:
                        try:
                            result = pipe(prompt, width=width, height=height, num_inference_steps=steps, guidance_scale=guidance, callback=_callback)
                        except TypeError:
                            _safe_q_put(q, {"type":"log","text":"[WARN] Pipeline didn't accept callback args; calling without callback"})
                            result = pipe(prompt, width=width, height=height, num_inference_steps=steps, guidance_scale=guidance)
        except Exception as e:
            tb = traceback.format_exc()
            emsg = str(e)
            if "out of memory" in emsg.lower() or "cuda out of memory" in emsg.lower():
                tb += "\n\n--- Detected CUDA OOM. Suggestions: reduce width/height/steps, use CPU, enable CPU offload or use smaller model. ---"
            _safe_q_put(q, {"type":"error","text":"Generation failed","trace": tb})
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            return

        # extract image
        image = None
        try:
            if hasattr(result, "images"):
                image = result.images[0]
            elif isinstance(result, (list, tuple)):
                image = result[0]
            else:
                image = result if hasattr(result, "save") else None
        except Exception:
            image = None

        if image is None:
            _safe_q_put(q, {"type":"error","text":"Pipeline returned no image", "trace": "No 'images' attribute and result not an image."})
            return

        # Save output (ensure unique name to avoid overwrite)
        out_path = IMAGE_DIR / filename
        if out_path.exists():
            from time import time
            out_path = IMAGE_DIR / f"{out_path.stem}_{int(time())}{out_path.suffix}"
        try:
            image.save(out_path)
        except Exception as e:
            tb = traceback.format_exc()
            _safe_q_put(q, {"type":"error","text":f"Failed to save image: {e}", "trace": tb})
            return

        _safe_q_put(q, {"type":"done","path":str(out_path)})

    except Exception as e:
        tb = traceback.format_exc()
        _safe_q_put(q, {"type":"error","text":str(e), "trace": tb})
    finally:
        # signal stream end
        try:
            _safe_q_put(q, None)
        except Exception:
            try:
                q.put(None)
            except Exception:
                pass
        # try freeing memory & pipeline
        try:
            if 'pipe' in locals() and pipe is not None:
                try:
                    pipe.to("cpu")
                except Exception:
                    pass
                try:
                    del pipe
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if ML_AVAILABLE and torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
        except Exception:
            pass

def start_generation_and_stream(payload: dict):
    q = queue.Queue(maxsize=200)
    t = threading.Thread(target=run_generation_thread, args=(payload, q), daemon=True)
    t.start()

    def event_generator():
        yield "data: " + json.dumps({"type":"sse_open"}) + "\n\n"
        while True:
            try:
                item = q.get(timeout=15)
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            # handle None sentinel
            if item is None:
                break
            # item is expected to be a JSON string or dict
            try:
                if isinstance(item, str):
                    s = item.strip()
                    if s == "" or s.lower() in ("none",):
                        continue
                    yield f"data: {s}\n\n"
                else:
                    yield "data: " + json.dumps(item) + "\n\n"
            except Exception:
                try:
                    yield "data: " + json.dumps({"type":"error","text":"Internal serialization error"}) + "\n\n"
                except Exception:
                    pass
        yield "data: " + json.dumps({"type":"sse_closed"}) + "\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_generator(), media_type="text/event-stream", headers=headers)

@app.get("/api/generate/stream")
async def api_generate_stream_get(payload: Optional[str] = None):
    if not payload:
        return JSONResponse({"ok": False, "msg": "provide 'payload' url-encoded JSON string"}, status_code=400)
    try:
        payload_json = json.loads(payload)
    except Exception:
        return JSONResponse({"ok": False, "msg": "Invalid payload (must be JSON string)"}, status_code=400)
    return start_generation_and_stream(payload_json)

@app.post("/api/generate/stream")
async def api_generate_stream_post(req: Request):
    try:
        payload_json = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "msg": "Invalid JSON body"}, status_code=400)
    return start_generation_and_stream(payload_json)

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]; s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def find_free_port(preferred=8000):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
        # bind ephemeral
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(("0.0.0.0", 0))
        port = s2.getsockname()[1]
        s2.close()
        return port

def main():
    print("Choose server mode: 1 = local-only (127.0.0.1), 2 = multi (0.0.0.0)")
    choice = input("Mode [1/2]: ").strip()
    if choice == "2":
        host = "0.0.0.0"
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
        app.state.server_mode = "multi"
    else:
        host = "127.0.0.1"
        app.state.server_mode = "local"

    desired_port = 8000
    port = find_free_port(desired_port)
    local_url = f"http://127.0.0.1:{port}"
    lan_ip = get_local_ip()
    lan_url = f"http://{lan_ip}:{port}"
    print(f"Starting FastAPI server on {host}:{port} (mode={app.state.server_mode})")
    print("Open in browser:")
    print(f" - Local: {local_url}")
    if host == "0.0.0.0":
        print(f" - LAN:   {lan_url}")
    if not ML_AVAILABLE:
        print("[WARN] ML libraries missing. Generation endpoints will return errors.")
        print("ML import error:", ML_IMPORT_ERROR)
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    main()