# -*- coding: utf-8 -*-
"""
rb_db_sync.py - Sincronizador directo con la base master.db de Rekordbox (pyrekordbox).
Analizer IA local  •  by @tomasanfo

Actualiza Rating, Commnt y ColorID de cada pista (buscada por ruta) y hace commit al final.
IMPORTANTE: Rekordbox debe estar CERRADO. Si no se encuentra la base, el programa igual
graba los tags físicos en los audios (este módulo solo informa y devuelve False).
"""
import datetime
import os
import shutil
import subprocess

import config as C


def is_rekordbox_running():
    """True si rekordbox.exe está abierto (solo Windows)."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq rekordbox.exe", "/NH"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        ).stdout.lower()
        return "rekordbox.exe" in out
    except Exception:
        return False


class RekordboxSync:
    def __init__(self, log=None):
        self.log = log or (lambda m, t="muted": None)
        self.db = None
        self.index = {}
        self.dirty = False

    # ── apertura ────────────────────────────────────────────
    def open(self):
        """Abre master.db. Devuelve True si pudo, False si no (se sigue solo con tags)."""
        try:
            try:
                from pyrekordbox import MasterDatabase as DbClass
            except ImportError:
                from pyrekordbox import Rekordbox6Database as DbClass
            self.db = DbClass()
        except Exception as e:
            self.log(f"⚠ No pude abrir la base de Rekordbox ({e}). "
                     f"Sigo solo con los tags de los archivos.", "warn")
            self.db = None
            return False

        self._backup()
        try:
            self._build_index()
        except Exception as e:
            self.log(f"⚠ No pude leer las pistas de Rekordbox ({e}).", "warn")
            self.close()
            return False

        self.log(f"✔ Base de Rekordbox abierta: {len(self.index)} pistas indexadas.", "ok")
        return True

    def _db_file(self):
        """Intenta ubicar el archivo master.db real para respaldarlo."""
        candidates = []
        directory = getattr(self.db, "db_directory", None)
        if directory:
            candidates.append(os.path.join(str(directory), "master.db"))
        try:
            engine_path = self.db.engine.url.database
            if engine_path:
                candidates.append(str(engine_path))
        except Exception:
            pass
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        return None

    def _backup(self):
        try:
            src = self._db_file()
            if not src:
                return
            stamp = datetime.datetime.now().strftime("%Y%m%d")
            dst = f"{src}.analizer_backup_{stamp}"
            if not os.path.exists(dst):
                shutil.copy2(src, dst)
                self.log(f"🛟 Backup de master.db: {os.path.basename(dst)}", "muted")
        except Exception as e:
            self.log(f"⚠ No pude crear backup de master.db: {e}", "warn")

    def _build_index(self):
        self.index = {}
        query = self.db.get_content()
        rows = query.all() if hasattr(query, "all") else list(query)
        for content in rows:
            folder_path = getattr(content, "FolderPath", None)
            if folder_path:
                self.index[C.path_key(folder_path)] = content

    # ── escritura ───────────────────────────────────────────
    def update_track(self, path, stars, percentile, drop_start, ai_energy):
        """Actualiza Rating, Commnt y ColorID. Devuelve True si la pista está en Rekordbox."""
        if self.db is None:
            return False
        track = self.index.get(C.path_key(path))
        if track is None:
            return False

        stars = max(1, min(5, int(stars)))
        block = C.build_comment_block(stars, percentile, drop_start, ai_energy)
        track.Rating = stars * C.RB_RATING_SCALE
        track.Commnt = C.merge_comment(block, track.Commnt or "")
        color = C.STAR_TO_COLOR_ID.get(stars)
        if color:
            track.ColorID = color
        self.dirty = True
        return True

    def clear_track_in_rekordbox_db(self, path):
        """Rating = 0, ColorID = 0 y quita SOLO el prefijo [🔥 ...] del comentario (deja el
        comentario original del DJ). Devuelve True si la pista está en Rekordbox."""
        if self.db is None:
            return False
        track = self.index.get(C.path_key(path))
        if track is None:
            return False
        track.Rating = 0
        track.ColorID = 0
        track.Commnt = C.strip_previous_block(track.Commnt or "")
        self.dirty = True
        return True

    def commit(self):
        """Confirma los cambios en master.db. Devuelve True si salió bien."""
        if self.db is None or not self.dirty:
            return True
        try:
            self.db.commit()
            self.dirty = False
            self.log("💾 Cambios confirmados en la base de Rekordbox (commit).", "ok")
            return True
        except Exception as e:
            try:
                self.db.rollback()
            except Exception:
                pass
            self.log(f"✖ Falló el commit en Rekordbox: {e}. "
                     f"¿Está Rekordbox cerrado? Los tags de los archivos sí quedaron.", "err")
            return False

    def close(self):
        try:
            if self.db is not None:
                self.db.close()
        except Exception:
            pass
        self.db = None
        self.index = {}


def clear_tracks_in_rekordbox_db(paths, log=None):
    """Conveniencia: abre master.db, limpia todas las rutas dadas, confirma y cierra.
    Devuelve {'opened','cleared','missing','committed'}."""
    sync = RekordboxSync(log=log)
    result = {"opened": False, "cleared": 0, "missing": 0, "committed": False}
    if not sync.open():
        return result
    result["opened"] = True
    for p in paths:
        if sync.clear_track_in_rekordbox_db(p):
            result["cleared"] += 1
        else:
            result["missing"] += 1
    result["committed"] = sync.commit()
    sync.close()
    return result
