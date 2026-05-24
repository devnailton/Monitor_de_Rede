import platform
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

from storage import Storage

MAX_SAMPLES = 300
IS_WINDOWS = platform.system().lower() == "windows"


def ping_once(host: str, timeout_ms: int = 1000) -> Optional[float]:
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000)), host]

    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=(timeout_ms / 1000) + 1.0,
            creationflags=subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if out.returncode != 0:
        return None

    match = re.search(r"(?:time|tempo)[=<]\s*([\d\.,]+)\s*ms", out.stdout, re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


@dataclass
class Device:
    name: str
    host: str
    samples: Deque[Tuple[datetime, Optional[float]]] = field(
        default_factory=lambda: deque(maxlen=MAX_SAMPLES)
    )


class PingMonitor:
    def __init__(self, interval: float = 5.0, timeout_ms: int = 1000, storage: Optional[Storage] = None):
        self.interval = interval
        self.timeout_ms = timeout_ms
        self.storage = storage
        self._devices: Dict[str, Device] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if self.storage is not None:
            for name, host in self.storage.list_devices():
                self._devices[name] = Device(name=name, host=host)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def add_device(self, name: str, host: str) -> None:
        with self._lock:
            self._devices[name] = self._devices.get(name) or Device(name=name, host=host)
            self._devices[name].host = host
        if self.storage is not None:
            self.storage.upsert_device(name, host)

    def edit_device(self, old_name: str, new_name: str, new_host: str) -> None:
        if self.storage is not None:
            self.storage.edit_device(old_name, new_name, new_host)
        with self._lock:
            dev = self._devices.pop(old_name, None)
            if dev is None:
                dev = Device(name=new_name, host=new_host)
            else:
                dev.name = new_name
                dev.host = new_host
            self._devices[new_name] = dev

    def remove_device(self, name: str) -> None:
        with self._lock:
            self._devices.pop(name, None)
        if self.storage is not None:
            self.storage.delete_device(name)

    def list_devices(self) -> List[Device]:
        with self._lock:
            return list(self._devices.values())

    def snapshot(self) -> Dict[str, List[Tuple[datetime, Optional[float]]]]:
        with self._lock:
            return {name: list(d.samples) for name, d in self._devices.items()}

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                targets = [(d.name, d.host) for d in self._devices.values()]

            threads: List[threading.Thread] = []
            results: Dict[str, Optional[float]] = {}
            lock = threading.Lock()

            def worker(n: str, h: str) -> None:
                latency = ping_once(h, self.timeout_ms)
                with lock:
                    results[n] = latency

            for name, host in targets:
                t = threading.Thread(target=worker, args=(name, host), daemon=True)
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=self.timeout_ms / 1000 + 2.0)

            now = datetime.now()
            with self._lock:
                for name, latency in results.items():
                    if name in self._devices:
                        self._devices[name].samples.append((now, latency))
                        if self.storage is not None:
                            self.storage.insert_sample(
                                name, self._devices[name].host, now, latency
                            )

            self._stop.wait(self.interval)
