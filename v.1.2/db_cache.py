# -*- coding: utf-8 -*-
"""
db_cache.py - SQLite local (tracks_cache.db) + motor de recalibración por Género.
Analizer IA local  •  by @tomasanfo
"""
import math
import os
import sqlite3
import threading

import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    path            TEXT PRIMARY KEY,
    mtime           REAL,
    size            INTEGER,
    version         INTEGER,
    title           TEXT,
    artist          TEXT,
    genre_original  TEXT DEFAULT '',
    genre_ai        TEXT DEFAULT '',
    genre_ai_conf   REAL,
    genre_final     TEXT DEFAULT '',
    drop_start      REAL,
    ai_energy       REAL,
    used_clap       INTEGER DEFAULT 0,
    drop_rms_p95    REAL,
    onset_punch     REAL,
    sub_bass_weight REAL,
    drop_contrast   REAL,
    dsp_score       REAL,
    explosivity     REAL,
    percentile      INTEGER,
    stars           INTEGER,
    peak_score      REAL,
    sustained_score REAL,
    bpm             REAL,
    role            INTEGER,
    group_mode      TEXT DEFAULT '',
    analyzed_at     REAL
);
"""

# Columnas que escribe el análisis (percentile/stars/genre_final los calcula recalibrate)
ANALYSIS_COLS = [
    "path", "mtime", "size", "version", "title", "artist",
    "genre_original", "genre_ai", "genre_ai_conf", "drop_start", "ai_energy",
    "used_clap", "drop_rms_p95", "onset_punch", "sub_bass_weight",
    "drop_contrast", "dsp_score", "explosivity", "analyzed_at",
    "peak_score", "sustained_score", "bpm",
]


def _percentiles(values):
    """Percentil 1..100 por rango promedio (maneja empates)."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return [max(1, min(100, int(math.ceil(r / n * 100.0)))) for r in ranks]


class TrackCache:
    def __init__(self, db_path=None):
        self.db_path = db_path or C.DB_PATH
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        """Agrega las columnas nuevas a una caché creada con una versión anterior."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(tracks)").fetchall()}
        for col, typ in (("peak_score", "REAL"), ("sustained_score", "REAL"),
                         ("bpm", "REAL"), ("role", "INTEGER")):
            if col not in have:
                self.conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ}")

    # ── lectura / escritura básica ──────────────────────────
    def get(self, path):
        with self.lock:
            row = self.conn.execute("SELECT * FROM tracks WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def upsert(self, data):
        values = {c: data.get(c) for c in ANALYSIS_COLS}
        cols = ",".join(ANALYSIS_COLS)
        params = ",".join(":" + c for c in ANALYSIS_COLS)
        updates = ",".join(f"{c}=excluded.{c}" for c in ANALYSIS_COLS if c != "path")
        sql = (f"INSERT INTO tracks ({cols}) VALUES ({params}) "
               f"ON CONFLICT(path) DO UPDATE SET {updates}")
        with self.lock:
            self.conn.execute(sql, values)
            self.conn.commit()

    def set_genre_original(self, path, genre):
        with self.lock:
            self.conn.execute("UPDATE tracks SET genre_original = ? WHERE path = ?", (genre or "", path))
            self.conn.commit()

    def refresh_file_state(self, path):
        """Tras escribir tags el mtime cambia: lo actualizamos para no re-analizar."""
        try:
            st = os.stat(path)
        except OSError:
            return
        with self.lock:
            self.conn.execute("UPDATE tracks SET mtime = ?, size = ? WHERE path = ?",
                              (st.st_mtime, st.st_size, path))
            self.conn.commit()

    def delete_tracks_from_cache(self, paths):
        """Borra esas pistas de tracks_cache.db para que puedan analizarse de nuevo desde cero.
        Devuelve la cantidad de filas eliminadas."""
        paths = [p for p in (paths or []) if p]
        if not paths:
            return 0
        deleted = 0
        with self.lock:
            for i in range(0, len(paths), 500):
                chunk = paths[i:i + 500]
                cur = self.conn.executemany("DELETE FROM tracks WHERE path = ?", [(p,) for p in chunk])
                if cur.rowcount and cur.rowcount > 0:
                    deleted += cur.rowcount
            self.conn.commit()
        return deleted

    def rows_under(self, folder=None):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM tracks WHERE explosivity IS NOT NULL").fetchall()
        out = [dict(r) for r in rows]
        if folder:
            scope = C.path_key(folder).rstrip("\\/") + os.sep
            out = [r for r in out if C.path_key(r["path"]).startswith(scope)]
        return out

    def count_under(self, folder=None):
        return len(self.rows_under(folder))

    # ── motor de recalibración por Género ───────────────────
    def recalibrate(self, folder=None, by_genre=True, use_ai_genre=True, commit=True):
        """
        Reparte percentiles (1-100) y estrellas (1★-5★) compitiendo SOLO dentro de cada género.
        Géneros con menos de MIN_GENRE_SIZE pistas usan umbrales absolutos de explosividad.
        Devuelve (assignments, stats):
          assignments[path] = {"genre","pct","stars","mode","n"}
          stats[genre]      = {"total", "stars": {1..5}, "mode"}
        """
        rows = self.rows_under(folder)
        groups = {}
        for r in rows:
            genre = (r.get("genre_original") or "").strip()
            if not genre and use_ai_genre:
                genre = (r.get("genre_ai") or "").strip()
            if not genre:
                genre = C.NO_GENRE_LABEL
            if not by_genre:
                genre = C.GLOBAL_LABEL
            key = genre.casefold()
            g = groups.setdefault(key, {"name": genre, "items": []})
            expl = float(r["explosivity"])
            sus = r.get("sustained_score")
            pk = r.get("peak_score")
            sus = expl if sus is None else float(sus)
            pk = expl if pk is None else float(pk)
            role_idx = C.ROLE_W_SUST * sus + C.ROLE_W_PEAK * pk
            g["items"].append((r["path"], expl, role_idx))

        assignments = {}
        stats = {}
        for g in groups.values():
            items = g["items"]
            n = len(items)
            name = g["name"]
            if n >= C.MIN_GENRE_SIZE:
                mode = "rank"
                pcts = _percentiles([x[1] for x in items])
                stars_list = []
                for p, x in zip(pcts, items):
                    base = C.stars_from_explosivity(x[1])
                    st = C.stars_from_percentile(p)
                    stars_list.append(max(base - C.STAR_REL_BAND, min(base + C.STAR_REL_BAND, st)))
                rpcts = _percentiles([x[2] for x in items])
                roles = [C.role_from_percentile(p) for p in rpcts]
            else:
                mode = "abs"
                pcts = [max(1, min(100, int(round(x[1] * 100)))) for x in items]
                stars_list = [C.stars_from_explosivity(x[1]) for x in items]
                roles = [C.role_from_score(x[2]) for x in items]
            stars_list = [max(st, C.STAR_FLOOR_STARS) if x[1] >= C.STAR_FLOOR_EXPL else st
                          for st, x in zip(stars_list, items)]
            counts = {s: 0 for s in range(1, 6)}
            rcounts = {r: 0 for r in range(1, 5)}
            for (path, _e, _ri), pct, st, ro in zip(items, pcts, stars_list, roles):
                assignments[path] = {"genre": name, "pct": pct, "stars": st, "mode": mode,
                                     "n": n, "role": ro}
                counts[st] += 1
                rcounts[ro] += 1
            stats[name] = {"total": n, "stars": counts, "roles": rcounts, "mode": mode}

        if commit and assignments:
            with self.lock:
                self.conn.executemany(
                    "UPDATE tracks SET genre_final = ?, percentile = ?, stars = ?, group_mode = ?, role = ? WHERE path = ?",
                    [(a["genre"], a["pct"], a["stars"], a["mode"], a["role"], p) for p, a in assignments.items()],
                )
                self.conn.commit()
        return assignments, stats

    def close(self):
        with self.lock:
            try:
                self.conn.commit()
                self.conn.close()
            except Exception:
                pass
