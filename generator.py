import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from PyQt6.QtCore import QThread, pyqtSignal
from diffusers import StableDiffusionXLPipeline
import torch
from pathlib import Path
import time
import contextlib
from typing import Tuple
import traceback

class ImageGeneratorThread(QThread):
    progress_changed = pyqtSignal(int)
    finished = pyqtSignal(bool, str)
    log = pyqtSignal(str)

    def __init__(self,
                 prompt: str,
                 model_id: str,
                 filename: str,
                 width: int,
                 height: int,
                 steps: int,
                 guidance: float,
                 cache_dir: str,
                 image_dir: str,
                 device: str = 'auto',
                 precision: str = 'auto',
                 scheduler: str = 'DDIM'):
        super().__init__()
        self.prompt = prompt or ""
        self.model_id = model_id or ""
        self.filename = filename or "output.png"
        self.width = max(256, int(width or 1024))
        self.height = max(256, int(height or 1024))
        self.steps = max(1, int(steps or 30))
        self.guidance = float(guidance or 7.5)
        self.cache_dir = Path(cache_dir)
        self.image_dir = Path(image_dir)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.device_preference = device or 'auto'
        self.precision_preference = precision or 'auto'
        self.scheduler = scheduler or 'DDIM'

    def _resolve_device_and_dtype(self, prefer_cpu=False) -> Tuple[torch.device, torch.dtype]:
        if prefer_cpu:
            device_str = 'cpu'
        else:
            if self.device_preference == 'auto':
                device_str = 'cuda' if torch.cuda.is_available() else 'cpu'
            else:
                device_str = self.device_preference
            if device_str == 'cuda' and not torch.cuda.is_available():
                device_str = 'cpu'

        if device_str == 'cuda':
            dtype = torch.float16 if (self.precision_preference == 'float16' or self.precision_preference == 'auto') else torch.float32
        else:
            dtype = torch.float32

        return torch.device(device_str), dtype

    def _unique_out_path(self, out_dir: Path, filename: str) -> Path:
        safe_name = Path(filename).name or "output.png"
        out_path = out_dir / safe_name
        if not out_path.exists():
            return out_path
        stem = out_path.stem
        suffix = out_path.suffix or ".png"
        ts = int(time.time())
        new_name = f"{stem}_{ts}{suffix}"
        return out_dir / new_name

    def _emit_log_trace(self, prefix: str, exc: Exception):
        tb = traceback.format_exc()
        try:
            # emit a short message + full traceback in log
            self.log.emit(f"{prefix}: {str(exc)}")
            for line in tb.splitlines():
                self.log.emit(line)
        except Exception:
            # best-effort: still send the exception text
            try:
                self.log.emit(f"{prefix}: {str(exc)}")
            except Exception:
                pass

    def _try_generation(self, pipe, device, dtype, width, height, steps):
        autocast_ctx = torch.cuda.amp.autocast() if (device.type == 'cuda' and dtype == torch.float16) else contextlib.nullcontext()
        # robust callback
        def _callback(*cb_args, **cb_kwargs):
            try:
                step = None
                if len(cb_args) >= 1:
                    step = cb_args[0]
                elif 'step' in cb_kwargs:
                    step = cb_kwargs.get('step')
                elif 'i' in cb_kwargs:
                    step = cb_kwargs.get('i')
                try:
                    si = int(step)
                except Exception:
                    si = 0
                pct = int((si + 1) / max(1, steps) * 100)
                if device.type == 'cuda':
                    try:
                        torch.cuda.synchronize()
                    except Exception:
                        pass
                self.progress_changed.emit(min(max(pct, 0), 100))
            except Exception:
                pass

        with torch.no_grad():
            with autocast_ctx:
                # prefer new callback_on_step_end, fallback to callback, else no callback
                try:
                    result = pipe(self.prompt,
                                  width=width,
                                  height=height,
                                  num_inference_steps=steps,
                                  guidance_scale=self.guidance,
                                  callback_on_step_end=_callback)
                except TypeError:
                    try:
                        result = pipe(self.prompt,
                                      width=width,
                                      height=height,
                                      num_inference_steps=steps,
                                      guidance_scale=self.guidance,
                                      callback=_callback)
                    except TypeError:
                        self.log.emit("[WARN] Pipeline did not accept callback kwargs - running without callback.")
                        result = pipe(self.prompt,
                                      width=width,
                                      height=height,
                                      num_inference_steps=steps,
                                      guidance_scale=self.guidance)
        # extract image
        image = None
        if hasattr(result, "images"):
            image = result.images[0]
        elif isinstance(result, (list, tuple)):
            image = result[0]
        else:
            image = result if (hasattr(result, "save") or getattr(result, "mode", None)) else None
        return image

    def run(self):
        pipe = None
        tried_cpu_fallback = False
        current_width = self.width
        current_height = self.height
        current_steps = self.steps

        try:
            # Resolve initial device/dtype (prefer GPU if available)
            device, dtype = self._resolve_device_and_dtype(prefer_cpu=False)
            self.log.emit(f"[INFO] Resolved device={device}, dtype={dtype}, scheduler={self.scheduler}")

            # LOAD PIPELINE (prefer dtype param)
            load_errors = []
            try:
                pipe = StableDiffusionXLPipeline.from_pretrained(
                    self.model_id,
                    dtype=dtype,
                    cache_dir=str(self.cache_dir),
                    use_safetensors=True,
                )
                self.log.emit("[INFO] Pipeline loaded with dtype param.")
            except Exception as e:
                load_errors.append(f"dtype-load: {e}")
                try:
                    pipe = StableDiffusionXLPipeline.from_pretrained(
                        self.model_id,
                        cache_dir=str(self.cache_dir),
                        use_safetensors=True,
                    )
                    self.log.emit("[INFO] Pipeline loaded without dtype param (fallback).")
                except Exception as e2:
                    load_errors.append(f"no-dtype-load: {e2}")
                    # emit full trace and abort
                    self.log.emit("[ERROR] Failed to load pipeline. Attempts:")
                    for ln in load_errors:
                        self.log.emit(ln)
                    self.finished.emit(False, "Failed to load pipeline")
                    return

            # try to move to requested device
            try:
                pipe.to(device)
            except Exception as e:
                self.log.emit(f"[WARN] pipe.to(device) failed: {e} - continuing; some components may be on CPU")

            # memory optimizations best-effort
            try:
                if hasattr(pipe, "enable_attention_slicing"):
                    try:
                        pipe.enable_attention_slicing()
                        self.log.emit("[INFO] attention slicing enabled")
                    except Exception:
                        pass
                if hasattr(pipe, "enable_xformers_memory_efficient_attention"):
                    try:
                        pipe.enable_xformers_memory_efficient_attention()
                        self.log.emit("[INFO] xformers enabled")
                    except Exception as e:
                        self.log.emit(f"[WARN] xformers enable failed: {e}")
                if hasattr(pipe, "enable_model_cpu_offload"):
                    try:
                        pipe.enable_model_cpu_offload()
                        self.log.emit("[INFO] model CPU offload enabled")
                    except Exception:
                        pass
            except Exception as e:
                self.log.emit(f"[WARN] Memory optimization calls failed: {e}")

            max_downscales = 2
            downscale_attempt = 0
            last_exc = None

            while True:
                try:
                    self.log.emit(f"[INFO] Attempt generation: device={device}, dtype={dtype}, size={current_width}x{current_height}, steps={current_steps}")
                    image = self._try_generation(pipe, device, dtype, current_width, current_height, current_steps)
                    if image is None:
                        raise RuntimeError("Pipeline returned no image")
                    # save and return success
                    out_path = self._unique_out_path(self.image_dir, self.filename)
                    try:
                        image.save(out_path)
                    except Exception as save_e:
                        self._emit_log_trace("Failed to save image", save_e)
                        self.finished.emit(False, f"Failed to save image: {save_e}")
                        return
                    self.progress_changed.emit(100)
                    self.finished.emit(True, str(out_path))
                    self.log.emit(f"[DONE] Saved image to {out_path}")
                    return

                except Exception as e:
                    tb = traceback.format_exc()
                    last_exc = e
                    self.log.emit("[ERROR] Generation attempt failed:")
                    for ln in tb.splitlines():
                        self.log.emit(ln)

                    emsg = str(e).lower()
                    is_oom = ("out of memory" in emsg) or ("cuda out of memory" in emsg) or ("cuda error" in emsg and "out" in emsg)

                    # if OOM on GPU, first try clearing cache and move to CPU once
                    if is_oom and device.type == 'cuda' and not tried_cpu_fallback:
                        self.log.emit("[INFO] Detected CUDA OOM -> trying CPU fallback (moving model to CPU and retrying).")
                        try:
                            try:
                                torch.cuda.empty_cache()
                            except Exception:
                                pass
                            pipe.to("cpu")
                            device, dtype = self._resolve_device_and_dtype(prefer_cpu=True)
                            tried_cpu_fallback = True
                            # loop will retry with same resolution but on CPU
                            continue
                        except Exception as move_cpu_e:
                            self._emit_log_trace("Failed to move pipeline to CPU after OOM", move_cpu_e)
                            # fallthrough to downscale

                    # If not OOM or CPU fallback failed, try downscale on CPU (reduce size & steps)
                    if downscale_attempt < max_downscales:
                        self.log.emit("[INFO] Trying downscale fallback: reduce resolution and steps, then retry on CPU.")
                        # reduce by half but not below 256
                        new_w = max(256, current_width // 2)
                        new_h = max(256, current_height // 2)
                        new_s = max(1, current_steps // 2)
                        if new_w == current_width and new_h == current_height and new_s == current_steps:
                            # cannot downscale further
                            self.log.emit("[WARN] Cannot downscale further.")
                            break
                        current_width, current_height, current_steps = new_w, new_h, new_s
                        downscale_attempt += 1
                        # ensure pipe is on CPU for reduced memory usage
                        try:
                            pipe.to("cpu")
                            device, dtype = self._resolve_device_and_dtype(prefer_cpu=True)
                        except Exception as pex:
                            self._emit_log_trace("Failed to move pipeline to CPU for downscale attempt", pex)
                        continue

                    # all fallbacks exhausted
                    self.log.emit("[ERROR] All fallbacks exhausted. Generation failed permanently.")
                    self._emit_log_trace("Final generation error", e)
                    self.finished.emit(False, f"Generation failed: {e}")
                    return

        except Exception as exc_outer:
            # unexpected top-level error
            self._emit_log_trace("Unexpected failure in generation thread", exc_outer)
            self.finished.emit(False, str(exc_outer))

        finally:
            # cleanup
            try:
                if pipe is not None:
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
                if torch.cuda.is_available():
                    try:
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
            except Exception:
                pass