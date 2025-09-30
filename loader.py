from PyQt6.QtCore import QThread, pyqtSignal
from pathlib import Path
import requests

class ModelDownloaderThread(QThread):
    progress_changed = pyqtSignal(int, str)  # percent, msg
    finished = pyqtSignal(bool, str)        # ok, message

    def __init__(self, model_id: str, cache_dir: str, token: str | None = None):
        super().__init__()
        self.model_id = model_id
        self.cache_dir = Path(cache_dir)
        self.token = token
        self.files = [
            'model.safetensors',
            'config.json',
            'scheduler_config.json',
            'tokenizer_config.json'
        ]
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def run(self):
        try:
            # calculate total size
            total = 0
            sizes = {}
            headers = {}
            if self.token:
                headers['Authorization'] = f'Bearer {self.token}'

            for f in self.files:
                url = f'https://huggingface.co/{self.model_id}/resolve/main/{f}'
                r = requests.head(url, headers=headers, allow_redirects=True, timeout=15)
                sizes[f] = int(r.headers.get('content-length', 0) or 0)
                total += sizes[f]

            downloaded = 0
            for f in self.files:
                dest = self.cache_dir / f
                if dest.exists():
                    downloaded += sizes[f]
                    pct = int(downloaded / total * 100) if total else 100
                    self.progress_changed.emit(pct, f'skipping {f} (exists)')
                    continue

                url = f'https://huggingface.co/{self.model_id}/resolve/main/{f}'
                with requests.get(url, stream=True, headers=headers, timeout=30) as r:
                    r.raise_for_status()
                    with open(dest, 'wb') as fh:
                        for chunk in r.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                fh.write(chunk)
                                downloaded += len(chunk)
                                pct = int(downloaded / total * 100) if total else 100
                                self.progress_changed.emit(pct, '')
            self.finished.emit(True, 'All files downloaded')
        except Exception as e:
            self.finished.emit(False, str(e))