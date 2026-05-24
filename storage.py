import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DB_PATH = Path(__file__).parent / "monitor.db"


class Storage:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    device TEXT NOT NULL,
                    host TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    latency_ms REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_samples_device_ts ON samples(device, timestamp)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    name TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    is_primary INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cols = [r[1] for r in conn.execute("PRAGMA table_info(devices)")]
            if "is_primary" not in cols:
                conn.execute("ALTER TABLE devices ADD COLUMN is_primary INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS known_devices (
                    name TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    last_used TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    name TEXT PRIMARY KEY
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS group_members (
                    group_name TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    PRIMARY KEY (group_name, device_name),
                    FOREIGN KEY (group_name) REFERENCES groups(name) ON DELETE CASCADE
                )
                """
            )

    def insert_sample(self, device: str, host: str, ts: datetime, latency: Optional[float]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO samples(device, host, timestamp, latency_ms) VALUES (?, ?, ?, ?)",
                (device, host, ts.isoformat(), latency),
            )

    def upsert_device(self, name: str, host: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO devices(name, host) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET host=excluded.host",
                (name, host),
            )
            conn.execute(
                "INSERT INTO known_devices(name, host, last_used) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET host=excluded.host, last_used=excluded.last_used",
                (name, host, datetime.now().isoformat()),
            )

    def list_known_devices(self) -> List[Tuple[str, str]]:
        with self._lock, self._connect() as conn:
            return list(
                conn.execute("SELECT name, host FROM known_devices ORDER BY last_used DESC")
            )

    def forget_known_device(self, name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM known_devices WHERE name=?", (name,))

    def list_groups(self) -> List[str]:
        with self._lock, self._connect() as conn:
            return [r[0] for r in conn.execute("SELECT name FROM groups ORDER BY name")]

    def save_group(self, group_name: str, members: List[Tuple[str, str]]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO groups(name) VALUES (?)", (group_name,))
            conn.execute("DELETE FROM group_members WHERE group_name=?", (group_name,))
            conn.executemany(
                "INSERT INTO group_members(group_name, device_name, host) VALUES (?, ?, ?)",
                [(group_name, n, h) for n, h in members],
            )

    def get_group(self, group_name: str) -> List[Tuple[str, str]]:
        with self._lock, self._connect() as conn:
            return list(
                conn.execute(
                    "SELECT device_name, host FROM group_members WHERE group_name=? ORDER BY device_name",
                    (group_name,),
                )
            )

    def delete_group(self, group_name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM group_members WHERE group_name=?", (group_name,))
            conn.execute("DELETE FROM groups WHERE name=?", (group_name,))

    def delete_device(self, name: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM devices WHERE name=?", (name,))

    def list_devices(self) -> List[Tuple[str, str]]:
        with self._lock, self._connect() as conn:
            return list(conn.execute("SELECT name, host FROM devices ORDER BY name"))

    def edit_device(self, old_name: str, new_name: str, new_host: str) -> None:
        with self._lock, self._connect() as conn:
            if new_name != old_name:
                exists = conn.execute(
                    "SELECT 1 FROM devices WHERE name=?", (new_name,)
                ).fetchone()
                if exists:
                    raise ValueError(f"Já existe equipamento com nome '{new_name}'.")
            conn.execute(
                "UPDATE devices SET name=?, host=? WHERE name=?",
                (new_name, new_host, old_name),
            )
            conn.execute(
                "UPDATE samples SET device=?, host=? WHERE device=?",
                (new_name, new_host, old_name),
            )
            conn.execute(
                "UPDATE known_devices SET name=?, host=?, last_used=? WHERE name=?",
                (new_name, new_host, datetime.now().isoformat(), old_name),
            )
            conn.execute(
                "UPDATE group_members SET device_name=?, host=? WHERE device_name=?",
                (new_name, new_host, old_name),
            )

    def set_primary(self, name: str, value: bool) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE devices SET is_primary=? WHERE name=?", (1 if value else 0, name))

    def get_primaries(self) -> set:
        with self._lock, self._connect() as conn:
            return {r[0] for r in conn.execute("SELECT name FROM devices WHERE is_primary=1")}

    def query_window(
        self, since: datetime
    ) -> Dict[str, List[Tuple[datetime, Optional[float]]]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT device, timestamp, latency_ms FROM samples "
                "WHERE timestamp >= ? ORDER BY timestamp",
                (since.isoformat(),),
            ).fetchall()
        result: Dict[str, List[Tuple[datetime, Optional[float]]]] = {}
        for device, ts, latency in rows:
            result.setdefault(device, []).append((datetime.fromisoformat(ts), latency))
        return result

    def export_config(self) -> dict:
        with self._lock, self._connect() as conn:
            devices = [
                {"name": n, "host": h, "is_primary": bool(p)}
                for n, h, p in conn.execute(
                    "SELECT name, host, is_primary FROM devices"
                )
            ]
            known = [
                {"name": n, "host": h, "last_used": ts}
                for n, h, ts in conn.execute(
                    "SELECT name, host, last_used FROM known_devices"
                )
            ]
            group_rows = list(
                conn.execute(
                    "SELECT group_name, device_name, host FROM group_members"
                )
            )
        groups: Dict[str, List[Dict[str, str]]] = {}
        for gname, dname, host in group_rows:
            groups.setdefault(gname, []).append({"name": dname, "host": host})
        return {"version": 1, "devices": devices, "known_devices": known, "groups": groups}

    def import_config(self, data: dict, replace: bool = False) -> None:
        with self._lock, self._connect() as conn:
            if replace:
                conn.execute("DELETE FROM devices")
                conn.execute("DELETE FROM known_devices")
                conn.execute("DELETE FROM group_members")
                conn.execute("DELETE FROM groups")

            for d in data.get("devices", []):
                conn.execute(
                    "INSERT INTO devices(name, host, is_primary) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET host=excluded.host, is_primary=excluded.is_primary",
                    (d["name"], d["host"], 1 if d.get("is_primary") else 0),
                )
            now = datetime.now().isoformat()
            for d in data.get("known_devices", []):
                conn.execute(
                    "INSERT INTO known_devices(name, host, last_used) VALUES (?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET host=excluded.host, last_used=excluded.last_used",
                    (d["name"], d["host"], d.get("last_used", now)),
                )
            for gname, members in data.get("groups", {}).items():
                conn.execute("INSERT OR IGNORE INTO groups(name) VALUES (?)", (gname,))
                conn.execute("DELETE FROM group_members WHERE group_name=?", (gname,))
                conn.executemany(
                    "INSERT INTO group_members(group_name, device_name, host) VALUES (?, ?, ?)",
                    [(gname, m["name"], m["host"]) for m in members],
                )

    def purge_older_than(self, days: int) -> None:
        cutoff = datetime.now() - timedelta(days=days)
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM samples WHERE timestamp < ?", (cutoff.isoformat(),))
