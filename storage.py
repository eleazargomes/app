"""
Camada de persistência com dois backends intercambiáveis:
  - LocalStore : JSON em ./data (dev/testes, sem internet)
  - GasStore   : Google Apps Script Web App (planilha do AppSheet)
Ambos expõem a mesma interface; um cache em memória reduz idas à planilha.
"""
import json
import os
import threading
import time

import requests

import config
from schema import SCHEMA, coerce_row


class _Cache:
    def __init__(self, ttl):
        self.ttl, self._d, self._lock = ttl, {}, threading.Lock()

    def get(self, k):
        with self._lock:
            item = self._d.get(k)
            if item and time.time() - item[0] < self.ttl:
                return item[1]
        return None

    def set(self, k, v):
        with self._lock:
            self._d[k] = (time.time(), v)

    def drop(self, k):
        with self._lock:
            self._d.pop(k, None)


class BaseStore:
    def __init__(self):
        self._cache = _Cache(config.CACHE_TTL_SECONDS)
        self.write_lock = threading.RLock()

    # -- interface pública ---------------------------------------------------
    def list(self, table):
        cached = self._cache.get(table)
        if cached is not None:
            return [dict(r) for r in cached]
        rows = [coerce_row(table, r) for r in self._read(table)]
        self._cache.set(table, rows)
        return [dict(r) for r in rows]

    def _match(self, table, row, ref):
        keys = SCHEMA[table]["key"]
        return all(str(row.get(k, "")) == str(ref.get(k, "")) for k in keys)

    def upsert(self, table, row):
        with self.write_lock:
            row = coerce_row(table, row)
            self._upsert(table, row)
            self._cache.drop(table)
            return row

    def append(self, table, row):
        with self.write_lock:
            row = coerce_row(table, row)
            self._append(table, [row])
            self._cache.drop(table)
            return row

    def delete(self, table, ref):
        with self.write_lock:
            ok = self._delete(table, ref)
            self._cache.drop(table)
            return ok

    def replace_all(self, table, rows):
        with self.write_lock:
            self._replace(table, [coerce_row(table, r) for r in rows])
            self._cache.drop(table)

    def invalidate(self):
        self._cache = _Cache(config.CACHE_TTL_SECONDS)


class LocalStore(BaseStore):
    def __init__(self, directory=None):
        super().__init__()
        self.dir = directory or config.LOCAL_DATA_DIR
        os.makedirs(self.dir, exist_ok=True)

    def _path(self, table):
        return os.path.join(self.dir, f"{table}.json")

    def _read(self, table):
        p = self._path(table)
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def _write(self, table, rows):
        tmp = self._path(table) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False)
        os.replace(tmp, self._path(table))  # escrita atômica

    def _upsert(self, table, row):
        rows = self._read(table)
        for i, r in enumerate(rows):
            if self._match(table, r, row):
                rows[i] = row
                break
        else:
            rows.append(row)
        self._write(table, rows)

    def _append(self, table, new_rows):
        self._write(table, self._read(table) + new_rows)

    def _delete(self, table, ref):
        rows = self._read(table)
        keep = [r for r in rows if not self._match(table, r, ref)]
        self._write(table, keep)
        return len(keep) != len(rows)

    def _replace(self, table, rows):
        self._write(table, rows)


class GasStore(BaseStore):
    """Fala com gas_integration.js (doPost). O segredo vai no corpo, nunca na URL."""
    CHUNK = 1500

    def __init__(self, url=None, secret=None):
        super().__init__()
        self.url = url or config.GAS_WEBAPP_URL
        self.secret = secret or config.GAS_SHARED_SECRET
        if not self.url or not self.secret:
            raise RuntimeError("Defina GAS_WEBAPP_URL e GAS_SHARED_SECRET.")

    def _call(self, action, table, **payload):
        body = {"secret": self.secret, "action": action, "table": table, **payload}
        r = requests.post(self.url, data=json.dumps(body),
                          headers={"Content-Type": "text/plain;charset=utf-8"},
                          timeout=60, allow_redirects=True)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"GAS {action}/{table}: {data.get('error')}")
        return data

    def _read(self, table):
        return self._call("list", table)["rows"]

    def _upsert(self, table, row):
        self._call("upsert", table, row=row, key=SCHEMA[table]["key"])

    def _append(self, table, new_rows):
        for i in range(0, len(new_rows), self.CHUNK):
            self._call("append_many", table, rows=new_rows[i:i + self.CHUNK])

    def _delete(self, table, ref):
        return self._call("delete", table, ref=ref, key=SCHEMA[table]["key"])["deleted"]

    def _replace(self, table, rows):
        first, rest = rows[:self.CHUNK], rows[self.CHUNK:]
        self._call("replace_all", table, rows=first)
        self._append(table, rest)


def get_store():
    if config.STORAGE_BACKEND == "gas":
        return GasStore()
    return LocalStore()
