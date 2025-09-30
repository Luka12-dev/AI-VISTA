import json
import os
import threading
import time
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional

import psutil

try:
    import torch
except Exception:
    torch = None

HERE = os.path.dirname(__file__)
SETTINGS_FILE = os.path.join(HERE, "settings.json")
# Path to the Rust helper executable (expected to be built separately)
RUST_HELPER = os.path.join(HERE, "..", "bin", "jobctl.exe")

@dataclass
class Settings:
    cpu_limit_percent: int = 70
    gpu_limit_percent: int = 95
    ram_limit_percent: int = 95
    # how often monitor checks (seconds)
    monitor_interval: float = 1.0

    def clamp(self):
        # Prevent user from setting absurdly low values (you requested minimums).
        if self.cpu_limit_percent < 70:
            self.cpu_limit_percent = 70
        if self.ram_limit_percent < 95:
            self.ram_limit_percent = 95
        if self.gpu_limit_percent < 1:
            self.gpu_limit_percent = 1

class ResourceController:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or self.load()
        self.settings.clamp()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = threading.Event()
        # work_event: worker threads should call work_event.wait() before heavy work
        self.work_event = threading.Event()
        self.work_event.set()

    def save(self):
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(self.settings), f, indent=4)

    @staticmethod
    def load() -> Settings:
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return Settings(**data)
            except Exception:
                pass
        return Settings()

    def apply_soft_limits(self):
        try:
            total_cpus = psutil.cpu_count(logical=True) or 1
            # compute threads proportional to CPU limit
            threads = max(1, int(total_cpus * self.settings.cpu_limit_percent / 100.0))
            # set environment/threading hints
            try:
                import torch as _t
                _t.set_num_threads(threads)
            except Exception:
                # torch may not be present, ignore silently
                pass
        except Exception:
            pass

    def _get_gpu_percent(self) -> float:
        # try torch first
        try:
            if torch is not None and torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                used = torch.cuda.memory_allocated(0)
                return float(used) / float(props.total_memory) * 100.0
        except Exception:
            pass

        # fallback to nvidia-smi if available
        try:
            cmd = ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]
            out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True, timeout=2)
            return float(out.strip().splitlines()[0])
        except Exception:
            return 0.0

    def start_monitor(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop_monitor(self):
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)

    def _monitor_loop(self):
        while not self._stop_monitor.is_set():
            try:
                cpu_percent = psutil.cpu_percent(interval=None)
                ram_percent = psutil.virtual_memory().percent
                gpu_percent = self._get_gpu_percent()

                # Debug prints (comment out in production)
                # print(f"[monitor] cpu={cpu_percent} ram={ram_percent} gpu={gpu_percent}")

                if (
                    cpu_percent >= self.settings.cpu_limit_percent
                    or ram_percent >= self.settings.ram_limit_percent
                    or gpu_percent >= self.settings.gpu_limit_percent
                ):
                    # too busy -> pause heavy workers
                    self.work_event.clear()
                else:
                    self.work_event.set()
            except Exception:
                # avoid killing monitor on transient errors
                self.work_event.set()
            time.sleep(self.settings.monitor_interval)

    def enforce_hard_limits(self, pid: int, cpu_percent: Optional[int] = None, mem_mb: Optional[int] = None) -> bool:
        cpu = cpu_percent if cpu_percent is not None else self.settings.cpu_limit_percent
        mem = mem_mb if mem_mb is not None else None

        # If jobctl binary not present, return False
        if not os.path.exists(RUST_HELPER):
            return False

        cmd = [RUST_HELPER, "--attach", str(pid), "--cpu", str(cpu)]
        if mem is not None:
            cmd.extend(["--mem-mb", str(mem)])
        try:
            subprocess.check_call(cmd, timeout=15)
            return True
        except subprocess.CalledProcessError:
            return False
        except Exception:
            return False