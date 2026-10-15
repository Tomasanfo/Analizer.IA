# -*- coding: utf-8 -*-
"""
gui.py - Interfaz minimalista Rojo y Negro (CustomTkinter) + hilo de trabajo.
Analizer IA local  •  by @tomasanfo

NOTA: ningún método se llama `_options` (colisiona con tkinter.Misc). Se usan nombres propios
como `_get_config`.
"""
import os
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

import config as C
import rb_db_sync
import tag_writer
from db_cache import TrackCache


# ═════════════════════════════════════════════════════════════
# UTILIDADES
# ═════════════════════════════════════════════════════════════
def scan_folder(folder, recursive):
    files = []
    if recursive:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                if n.startswith("._"):
                    continue
                if os.path.splitext(n)[1].lower() in C.AUDIO_EXTS:
                    files.append(os.path.join(root, n))
    else:
        for n in os.listdir(folder):
            full = os.path.join(folder, n)
            if (os.path.isfile(full) and not n.startswith("._")
                    and os.path.splitext(n)[1].lower() in C.AUDIO_EXTS):
                files.append(full)
    files.sort(key=lambda p: p.lower())
    return files


def track_label(info, path):
    if info.get("artist") and info.get("title"):
        return f"{info['artist']} - {info['title']}"
    return os.path.basename(path)


# ═════════════════════════════════════════════════════════════
# HILO DE TRABAJO (no toca widgets: solo manda mensajes por cola)
# ═════════════════════════════════════════════════════════════
class Worker(threading.Thread):
    def __init__(self, mode, cfg, ui_q):
        super().__init__(daemon=True)
        self.mode = mode            # "analyze" | "reapply" | "clear"
        self.cfg = cfg
        self.ui_q = ui_q
        self.stop_event = threading.Event()

    # ── mensajería ──────────────────────────────────────────
    def emit(self, kind, *payload):
        self.ui_q.put((kind,) + payload)

    def log(self, text, tag="soft"):
        self.ui_q.put(("log", text, tag))

    # ── ciclo de vida ───────────────────────────────────────
    def run(self):
        status = "ok"
        try:
            if self.mode == "analyze":
                status = self._run_analyze()
            elif self.mode == "clear":
                status = self._run_clear()
            else:
                status = self._run_reapply()
        except Exception as e:
            status = "error"
            self.log(f"✖ Error fatal: {e}", "err")
            try:
                with open(C.ERROR_LOG, "a", encoding="utf-8") as f:
                    f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + traceback.format_exc() + "\n")
            except Exception:
                pass
        finally:
            self.emit("done", status)

    # ── validez de caché ────────────────────────────────────
    def _cache_valid(self, row, st, info):
        cfg = self.cfg
        if not row or row.get("explosivity") is None:
            return False
        if row.get("version") != C.ANALYSIS_VERSION:
            return False
        if abs((row.get("mtime") or 0) - st.st_mtime) > 1 or row.get("size") != st.st_size:
            return False
        if bool(row.get("used_clap")) != bool(cfg["use_ai"]):
            return False
        if (not info["genre"]) and cfg["detect_genre"] and not (row.get("genre_ai") or ""):
            return False
        return True

    # ── ANALIZAR ────────────────────────────────────────────
    def _run_analyze(self):
        cfg = self.cfg
        folder = cfg["folder"]
        self.log(f"📂 Escaneando: {folder}", "soft")
        files = scan_folder(folder, cfg["recursive"])
        total = len(files)
        if total == 0:
            self.log("No encontré archivos de audio en esa carpeta.", "warn")
            return "ok"
        self.log(f"🎵 {total} pistas encontradas.", "red")
        self.emit("kpi", {"total": total, "analyzed": 0, "cached": 0, "genres": 0, "eta": "--"})

        cache = TrackCache()
        import audio_analyzer as AA  # import pesado: se hace acá, dentro del hilo

        clap = AA.ClapEngine(log=lambda m: self.log(m, "muted"),
                             on_ready=lambda: self.emit("model", True))

        analyzed = cached = errors = 0
        t_begin = time.time()
        stats = {}
        stopped = False

        for i, path in enumerate(files, 1):
            if self.stop_event.is_set():
                stopped = True
                break
            try:
                st = os.stat(path)
                info = tag_writer.read_basic_info(path)
                label = track_label(info, path)
                row = cache.get(path)
                self.log(f"[{i}/{total}] {label}", "soft")

                if self._cache_valid(row, st, info):
                    cached += 1
                    if (row.get("genre_original") or "") != info["genre"]:
                        cache.set_genre_original(path, info["genre"])
                    source = "caché"
                else:
                    want_genre = (not info["genre"]) and cfg["detect_genre"]
                    res = AA.analyze_track(path, clap, use_ai=cfg["use_ai"], want_genre=want_genre)
                    res.update({
                        "path": path, "mtime": st.st_mtime, "size": st.st_size,
                        "version": C.ANALYSIS_VERSION, "title": info["title"],
                        "artist": info["artist"], "genre_original": info["genre"],
                        "analyzed_at": time.time(),
                    })
                    cache.upsert(res)
                    analyzed += 1
                    source = "IA"

                assignments, stats = cache.recalibrate(
                    folder, cfg["calibrate"], cfg["detect_genre"], commit=(i % 25 == 0))
                row = cache.get(path)
                a = assignments.get(path)

                ai_txt = "--" if row.get("ai_energy") is None else f"{row['ai_energy']:.2f}"
                self.log(f"     ⏱ Drop en {C.fmt_mmss(row['drop_start'])} "
                         f"({row['drop_start']:.1f}s)  ·  🧠 IA {ai_txt}  ·  "
                         f"DSP {row['dsp_score']:.2f}  ·  🔥 Explosividad {row['explosivity']:.2f}  [{source}]",
                         "muted")
                if a:
                    gen_tag = ""
                    if not info["genre"] and row.get("genre_ai") and cfg["detect_genre"]:
                        gen_tag = " (género IA)"
                    mode_tag = "" if a["mode"] == "rank" else " (umbral absoluto: pocas pistas)"
                    self.log(f"     🎚 {a['genre']}{gen_tag} → percentil {a['pct']}% de {a['n']} pistas  ·  "
                             f"⭐ {a['stars']}★ {C.STAR_LABELS[a['stars']]}{mode_tag}", "red")
            except Exception as e:
                errors += 1
                self.log(f"     ✖ Error: {e}", "err")

            elapsed = time.time() - t_begin
            eta = (elapsed / i) * (total - i)
            self.emit("progress", i / total, f"{int(i / total * 100)}%")
            self.emit("kpi", {"total": total, "analyzed": analyzed, "cached": cached,
                              "genres": len(stats), "eta": C.fmt_eta(eta)})
            self.emit("genres", stats)

        if stopped:
            cache.recalibrate(folder, cfg["calibrate"], cfg["detect_genre"], commit=True)
            self.log("⏸ Pausado. Todo lo analizado quedó guardado en caché; "
                     "al reiniciar continúa desde donde quedó.", "warn")
            cache.close()
            return "paused"

        self.log(f"✔ Análisis completo: {analyzed} nuevas, {cached} desde caché, {errors} con error.", "ok")
        assignments, stats = cache.recalibrate(folder, cfg["calibrate"], cfg["detect_genre"], commit=True)
        self.emit("genres", stats)
        self.emit("kpi", {"total": total, "analyzed": analyzed, "cached": cached,
                          "genres": len(stats), "eta": "0s"})
        self._log_star_summary(stats)

        if cfg["write_tags"] or cfg["sync_rb"]:
            self._apply(cache, folder)
        else:
            self.log("ℹ Escritura de tags y Rekordbox desactivadas: solo se guardó en caché.", "muted")
        cache.close()
        return "ok"

    # ── RE-APLICAR DESDE CACHÉ ──────────────────────────────
    def _run_reapply(self):
        cfg = self.cfg
        folder = cfg["folder"]
        cache = TrackCache()
        n = cache.count_under(folder)
        if n == 0:
            self.log("No hay pistas en la caché para esa carpeta. Corré primero un análisis.", "warn")
            cache.close()
            return "ok"
        self.log(f"⚡ Re-aplicando estrellas desde caché ({n} pistas)...", "red")
        _a, stats = cache.recalibrate(folder, cfg["calibrate"], cfg["detect_genre"], commit=True)
        self.emit("genres", stats)
        self.emit("kpi", {"total": n, "analyzed": 0, "cached": n, "genres": len(stats), "eta": "0s"})
        self._log_star_summary(stats)
        self._apply(cache, folder)
        cache.close()
        return "ok"

    # ── LIMPIAR ESTRELLAS ───────────────────────────────────
    def _run_clear(self):
        cfg = self.cfg
        folder = cfg["folder"]
        self.log(f"📂 Escaneando: {folder}", "soft")
        files = scan_folder(folder, cfg["recursive"])
        total = len(files)
        if total == 0:
            self.log("No encontré archivos de audio en esa carpeta.", "warn")
            return "ok"
        self.log(f"🧹 Limpiando estrellas de {total} pistas...", "red")

        rb = None
        if cfg["sync_rb"]:
            if rb_db_sync.is_rekordbox_running():
                self.log("⚠ Rekordbox sigue abierto: NO toco su base de datos. "
                         "Cerralo y volvé a usar «Limpiar Estrellas».", "warn")
            else:
                rb = rb_db_sync.RekordboxSync(log=lambda m, t="muted": self.log(m, t))
                if not rb.open():
                    rb = None

        cache = TrackCache()
        processed = []
        ok_tags = untouched = ok_rb = miss_rb = err = 0
        stopped = False
        t_begin = time.time()

        for i, path in enumerate(files, 1):
            if self.stop_event.is_set():
                stopped = True
                self.log("⏸ Limpieza interrumpida por el usuario.", "warn")
                break
            flags = []
            if cfg["write_tags"]:
                try:
                    res = tag_writer.clear_energy_tags(path)
                    if res.get("changed"):
                        ok_tags += 1
                        flags.append("tags ✓")
                    else:
                        untouched += 1
                        flags.append("sin cambios")
                except Exception as e:
                    err += 1
                    flags.append(f"tags ✖ ({e})")
            if rb is not None:
                if rb.clear_track_in_rekordbox_db(path):
                    ok_rb += 1
                    flags.append("Rekordbox ✓")
                else:
                    miss_rb += 1
                    flags.append("no está en Rekordbox")
            processed.append(path)
            if flags:
                self.log(f"   🧹 {os.path.basename(path)}  [{' | '.join(flags)}]",
                         "err" if any("✖" in f for f in flags) else "muted")
            elapsed = time.time() - t_begin
            eta = (elapsed / i) * (total - i)
            self.emit("progress", i / total, f"Limpiando {int(i / total * 100)}%")
            self.emit("kpi", {"total": total, "analyzed": 0, "cached": 0, "genres": 0,
                              "eta": C.fmt_eta(eta)})

        if rb is not None:
            rb.commit()
            rb.close()

        removed = cache.delete_tracks_from_cache(processed)
        cache.close()
        self.emit("genres", {})
        self.emit("kpi", {"total": total, "analyzed": 0, "cached": 0, "genres": 0, "eta": "0s"})
        self.log(f"✔ Limpieza terminada: {ok_tags} archivos limpiados ({untouched} sin estrellas), "
                 f"{ok_rb} en Rekordbox ({miss_rb} no encontradas), {removed} borradas de la caché, "
                 f"{err} errores.", "ok")
        self.log("ℹ Género, título, artista y BPM no se tocaron. "
                 "Ya podés volver a analizar esas pistas desde cero.", "muted")
        return "paused" if stopped else "ok"

    def _log_star_summary(self, stats):
        totals = {s: 0 for s in range(1, 6)}
        for g in stats.values():
            for s, c in g["stars"].items():
                totals[s] += c
        self.log("📊 Reparto: " + "  ".join(f"{s}★ {totals[s]}" for s in range(5, 0, -1)), "ok")

    # ── ESCRIBIR TAGS + REKORDBOX ───────────────────────────
    def _apply(self, cache, folder):
        cfg = self.cfg
        rows = [r for r in cache.rows_under(folder) if r.get("stars") and os.path.isfile(r["path"])]
        if not rows:
            self.log("No hay pistas con estrellas para escribir.", "warn")
            return

        write_tags = cfg["write_tags"]
        rb = None
        if cfg["sync_rb"]:
            if rb_db_sync.is_rekordbox_running():
                self.log("⚠ Rekordbox sigue abierto: NO toco su base de datos. "
                         "Cerralo y usá «Re-aplicar desde Caché».", "warn")
            else:
                rb = rb_db_sync.RekordboxSync(log=lambda m, t="muted": self.log(m, t))
                if not rb.open():
                    rb = None

        n = len(rows)
        ok_tags = ok_rb = miss_rb = err = 0
        self.log(f"✍ Escribiendo en {n} pistas...", "red")
        for i, r in enumerate(rows, 1):
            if self.stop_event.is_set():
                self.log("⏸ Escritura interrumpida por el usuario.", "warn")
                break
            label = track_label({"artist": r.get("artist"), "title": r.get("title")}, r["path"])
            stars, pct = int(r["stars"]), int(r["percentile"])
            flags = []

            if write_tags:
                genre_w = ""
                if not (r.get("genre_original") or "") and cfg["detect_genre"]:
                    genre_w = r.get("genre_ai") or ""
                try:
                    res = tag_writer.write_analysis_tags(
                        r["path"], stars, pct, r["drop_start"], r.get("ai_energy"), genre_w)
                    cache.refresh_file_state(r["path"])
                    ok_tags += 1
                    flags.append("tags ✓")
                    if res.get("genre_written"):
                        flags.append(f"género IA: {genre_w}")
                except Exception as e:
                    err += 1
                    flags.append(f"tags ✖ ({e})")

            if rb is not None:
                if rb.update_track(r["path"], stars, pct, r["drop_start"], r.get("ai_energy")):
                    ok_rb += 1
                    flags.append("Rekordbox ✓")
                else:
                    miss_rb += 1
                    flags.append("no está en Rekordbox")

            self.log(f"   {stars}★ {pct:>3}%  {label}  [{' | '.join(flags)}]",
                     "err" if any("✖" in f for f in flags) else "muted")
            self.emit("progress", i / n, f"Escribiendo {int(i / n * 100)}%")

        if rb is not None:
            rb.commit()
            rb.close()
        self.log(f"✔ Escritura terminada: {ok_tags} archivos, {ok_rb} en Rekordbox, "
                 f"{miss_rb} no encontradas en Rekordbox, {err} errores.", "ok")


# ═════════════════════════════════════════════════════════════
# INTERFAZ
# ═════════════════════════════════════════════════════════════
class AnalizerApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title(C.APP_NAME)
        self.geometry("1260x880")
        self.minsize(1120, 760)
        self.configure(fg_color=C.BG_MAIN)

        self.ui_q = queue.Queue()
        self.worker = None

        self.var_folder = ctk.StringVar(value="")
        self.var_recursive = ctk.BooleanVar(value=True)
        self.var_ai = ctk.BooleanVar(value=True)
        self.var_calibrate = ctk.BooleanVar(value=True)
        self.var_rb = ctk.BooleanVar(value=True)
        self.var_tags = ctk.BooleanVar(value=True)
        self.var_genre_ai = ctk.BooleanVar(value=True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self._build_header()
        self._build_folder_row()
        self._build_switches()
        self._build_kpis()
        self._build_panes()
        self._build_progress_and_buttons()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._poll_queue)
        threading.Thread(target=self._detect_hardware, daemon=True).start()

        self._apply_icon()
        self.after(300, self._apply_icon)        # CTk pisa el ícono a los ~200 ms
        self.after(250, self._show_startup_warning)

    # ── construcción ────────────────────────────────────────
    def _build_header(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=0, column=0, sticky="ew", padx=36, pady=(26, 6))
        f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text=C.APP_NAME, font=("Segoe UI", 28, "bold"),
                     text_color=C.TEXT_MAIN).grid(row=0, column=0, sticky="w")
        status = ctk.CTkFrame(f, fg_color="transparent")
        status.grid(row=0, column=1, sticky="e")
        self.lbl_dot = ctk.CTkLabel(status, text="●", font=("Segoe UI", 16), text_color=C.ACCENT_RED)
        self.lbl_dot.pack(side="left", padx=(0, 6))
        self.lbl_hw = ctk.CTkLabel(status, text="Detectando hardware...", font=("Segoe UI", 12),
                                   text_color=C.TEXT_MUTED)
        self.lbl_hw.pack(side="left")

    def _build_folder_row(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=1, column=0, sticky="ew", padx=36, pady=(14, 6))
        f.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(f, text="📂 Seleccionar Carpeta de Música...", command=self._on_browse,
                      fg_color=C.ACCENT_RED, hover_color=C.ACCENT_HOVER, text_color=C.TEXT_MAIN,
                      font=("Segoe UI", 13, "bold"), height=40, corner_radius=8
                      ).grid(row=0, column=0, padx=(0, 12))
        ctk.CTkEntry(f, textvariable=self.var_folder, fg_color=C.BG_ELEMENT, border_width=0,
                     text_color=C.TEXT_SOFT, placeholder_text="Ruta de tu carpeta de música...",
                     placeholder_text_color=C.TEXT_MUTED, height=40, corner_radius=8,
                     font=("Segoe UI", 12)).grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ctk.CTkCheckBox(f, text="Incluir subcarpetas", variable=self.var_recursive,
                        fg_color=C.ACCENT_RED, hover_color=C.ACCENT_HOVER, border_color=C.TEXT_MUTED,
                        text_color=C.TEXT_SOFT, font=("Segoe UI", 12)).grid(row=0, column=2)

    def _make_switch(self, parent, text, var, row, col):
        sw = ctk.CTkSwitch(parent, text=text, variable=var, progress_color=C.ACCENT_RED,
                           button_color=C.TEXT_MAIN, button_hover_color=C.TEXT_SOFT,
                           fg_color=C.BG_ELEMENT, text_color=C.TEXT_SOFT, font=("Segoe UI", 12))
        sw.grid(row=row, column=col, sticky="w", padx=(0, 24), pady=5)
        return sw

    def _build_switches(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=2, column=0, sticky="ew", padx=36, pady=(10, 4))
        f.grid_columnconfigure((0, 1), weight=1, uniform="sw")
        self._make_switch(f, "🔥 Usar IA Neuronal (CLAP) + Detector de Drop para medir Explosividad",
                          self.var_ai, 0, 0)
        self._make_switch(f, "⭐ Calibrar Estrellitas (1★ a 5★) compitiendo dentro de cada Género",
                          self.var_calibrate, 0, 1)
        self._make_switch(f, "💉 Inyectar Estrellitas directamente en la Base de Datos de Rekordbox (master.db)",
                          self.var_rb, 1, 0)
        self._make_switch(f, "🏷️ Grabar Comentarios [🔥 % | ★] y Tags ID3 en los archivos de audio",
                          self.var_tags, 1, 1)
        self._make_switch(f, "🧠 Detectar Género con IA SOLO si la canción no tiene Género",
                          self.var_genre_ai, 2, 0)
        ctk.CTkLabel(f, text="ℹ  Cerrá Rekordbox antes de escribir en su base de datos.",
                     font=("Segoe UI", 11), text_color=C.TEXT_MUTED
                     ).grid(row=2, column=1, sticky="w", pady=5)

    def _make_kpi(self, parent, col, caption, initial):
        card = ctk.CTkFrame(parent, fg_color=C.BG_ELEMENT, corner_radius=10)
        card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 10, 0))
        val = ctk.CTkLabel(card, text=initial, font=("Segoe UI", 26, "bold"), text_color=C.ACCENT_RED)
        val.pack(anchor="w", padx=18, pady=(12, 0))
        ctk.CTkLabel(card, text=caption, font=("Segoe UI", 11), text_color=C.TEXT_MUTED
                     ).pack(anchor="w", padx=18, pady=(0, 12))
        return val

    def _build_kpis(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=3, column=0, sticky="ew", padx=36, pady=(14, 8))
        f.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="kpi")
        self.kpi_total = self._make_kpi(f, 0, "Total Pistas", "0")
        self.kpi_done = self._make_kpi(f, 1, "Analizadas / Caché", "0 / 0")
        self.kpi_genres = self._make_kpi(f, 2, "Géneros en Competencia", "0")
        self.kpi_eta = self._make_kpi(f, 3, "Tiempo Estimado (ETA)", "--")

    def _build_panes(self):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=4, column=0, sticky="nsew", padx=36, pady=(6, 6))
        f.grid_columnconfigure(0, weight=3)
        f.grid_columnconfigure(1, weight=2)
        f.grid_rowconfigure(0, weight=1)

        # Izquierda: consola en vivo
        self.console = ctk.CTkTextbox(
            f, fg_color=C.BG_CONSOLE, text_color=C.TEXT_SOFT, font=("Consolas", 12),
            corner_radius=10, border_width=0, wrap="word", state="disabled")
        self.console.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        for tag, color in (("red", C.ACCENT_RED), ("ok", C.ACCENT_OK), ("warn", C.ACCENT_WARN),
                           ("err", C.ACCENT_ERR), ("muted", C.TEXT_MUTED), ("soft", C.TEXT_SOFT)):
            self.console.tag_config(tag, foreground=color)

        # Derecha: tabla de géneros
        right = ctk.CTkFrame(f, fg_color=C.BG_ELEMENT, corner_radius=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(right, text="Géneros en competencia", font=("Segoe UI", 12, "bold"),
                     text_color=C.TEXT_MUTED).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Analizer.Treeview", background=C.BG_ELEMENT, fieldbackground=C.BG_ELEMENT,
                        foreground=C.TEXT_MAIN, rowheight=26, borderwidth=0, font=("Segoe UI", 10))
        style.configure("Analizer.Treeview.Heading", background=C.BG_ELEMENT, foreground=C.TEXT_MUTED,
                        relief="flat", borderwidth=0, font=("Segoe UI", 9, "bold"))
        style.map("Analizer.Treeview", background=[("selected", "#1C1C22")],
                  foreground=[("selected", C.TEXT_MAIN)])
        style.map("Analizer.Treeview.Heading", background=[("active", C.BG_ELEMENT)])
        style.layout("Analizer.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

        cols = ("genre", "total", "s1", "s2", "s3", "s4", "s5")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", style="Analizer.Treeview")
        heads = {"genre": "Género", "total": "Total", "s1": "1★", "s2": "2★", "s3": "3★",
                 "s4": "4★", "s5": "5★"}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            if c == "genre":
                self.tree.column(c, width=140, anchor="w", stretch=True)
            else:
                self.tree.column(c, width=46, anchor="center", stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        sb = ctk.CTkScrollbar(right, command=self.tree.yview, fg_color=C.BG_ELEMENT,
                              button_color="#27272F", button_hover_color=C.ACCENT_RED)
        sb.grid(row=1, column=1, sticky="ns", padx=(2, 6), pady=(0, 10))
        self.tree.configure(yscrollcommand=sb.set)

    def _build_progress_and_buttons(self):
        pf = ctk.CTkFrame(self, fg_color="transparent")
        pf.grid(row=5, column=0, sticky="ew", padx=36, pady=(8, 4))
        pf.grid_columnconfigure(0, weight=1)
        self.progress = ctk.CTkProgressBar(pf, height=6, fg_color=C.BG_ELEMENT,
                                           progress_color=C.ACCENT_RED, corner_radius=3)
        self.progress.set(0)
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 14))
        self.lbl_pct = ctk.CTkLabel(pf, text="0%", font=("Segoe UI", 12, "bold"),
                                    text_color=C.TEXT_SOFT, width=120, anchor="e")
        self.lbl_pct.grid(row=0, column=1)

        bf = ctk.CTkFrame(self, fg_color="transparent")
        bf.grid(row=6, column=0, sticky="ew", padx=36, pady=(10, 4))
        bf.grid_columnconfigure(0, weight=4)
        bf.grid_columnconfigure((1, 2, 3), weight=3)
        self.btn_start = ctk.CTkButton(
            bf, text="▶ INICIAR ANÁLISIS IA", command=self._on_start,
            fg_color=C.ACCENT_RED, hover_color=C.ACCENT_HOVER, text_color=C.TEXT_MAIN,
            font=("Segoe UI", 13, "bold"), height=46, corner_radius=8)
        self.btn_start.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.btn_pause = ctk.CTkButton(
            bf, text="⏸ PAUSAR Y GUARDAR EN CACHÉ", command=self._on_pause, state="disabled",
            fg_color=C.BG_ELEMENT, hover_color="#1C1C22", text_color=C.TEXT_SOFT,
            text_color_disabled=C.TEXT_MUTED, font=("Segoe UI", 12, "bold"), height=46, corner_radius=8)
        self.btn_pause.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.btn_reapply = ctk.CTkButton(
            bf, text="⚡ Re-aplicar desde Caché",
            command=self._on_reapply, fg_color=C.BG_ELEMENT, hover_color="#1C1C22",
            text_color=C.TEXT_SOFT, text_color_disabled=C.TEXT_MUTED, font=("Segoe UI", 12, "bold"),
            height=46, corner_radius=8)
        self.btn_reapply.grid(row=0, column=2, sticky="ew", padx=(0, 10))
        self.btn_clear = ctk.CTkButton(
            bf, text="🧹 Limpiar Estrellas", command=self._on_clear,
            fg_color=C.BG_ELEMENT, hover_color=C.ACCENT_HOVER, text_color=C.TEXT_SOFT,
            text_color_disabled=C.TEXT_MUTED, font=("Segoe UI", 12, "bold"),
            height=46, corner_radius=8)
        self.btn_clear.grid(row=0, column=3, sticky="ew")

    def _build_footer(self):
        ctk.CTkLabel(self, text=C.FOOTER_TEXT, font=("Segoe UI", 11), text_color=C.TEXT_MUTED
                     ).grid(row=7, column=0, sticky="ew", padx=40, pady=(8, 14))

    # ── configuración / helpers ─────────────────────────────
    def _get_config(self):
        folder = self.var_folder.get().strip().strip('"')
        return {
            "folder": os.path.normpath(folder) if folder else "",
            "recursive": bool(self.var_recursive.get()),
            "use_ai": bool(self.var_ai.get()),
            "calibrate": bool(self.var_calibrate.get()),
            "sync_rb": bool(self.var_rb.get()),
            "write_tags": bool(self.var_tags.get()),
            "detect_genre": bool(self.var_genre_ai.get()),
        }

    def _log_line(self, text, tag="soft"):
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n", tag)
        try:
            lines = int(self.console.index("end-1c").split(".")[0])
            if lines > 3000:
                self.console.delete("1.0", "800.0")
        except Exception:
            pass
        self.console.see("end")
        self.console.configure(state="disabled")

    def _set_running(self, running):
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_reapply.configure(state="disabled" if running else "normal")
        self.btn_clear.configure(state="disabled" if running else "normal")
        self.btn_pause.configure(state="normal" if running else "disabled",
                                 text="⏸ PAUSAR Y GUARDAR EN CACHÉ")

    def _refresh_genres(self, stats):
        for item in self.tree.get_children():
            self.tree.delete(item)
        for name, g in sorted(stats.items(), key=lambda kv: (-kv[1]["total"], kv[0].casefold())):
            s = g["stars"]
            self.tree.insert("", "end", values=(name, g["total"], s[1], s[2], s[3], s[4], s[5]))

    def _set_kpi(self, k):
        self.kpi_total.configure(text=str(k.get("total", 0)))
        self.kpi_done.configure(text=f"{k.get('analyzed', 0)} / {k.get('cached', 0)}")
        self.kpi_genres.configure(text=str(k.get("genres", 0)))
        self.kpi_eta.configure(text=str(k.get("eta", "--")))

    def _detect_hardware(self):
        try:
            import torch
            if torch.cuda.is_available():
                text = f"GPU: {torch.cuda.get_device_name(0)}"
            else:
                text = "CPU (sin GPU CUDA)"
        except Exception:
            text = "Motor IA no disponible (¿instalaste torch?)"
        self.ui_q.put(("hw", text))

    # ── cola de mensajes del hilo ───────────────────────────
    def _poll_queue(self):
        try:
            for _ in range(300):
                msg = self.ui_q.get_nowait()
                self._handle_msg(msg)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _handle_msg(self, msg):
        kind = msg[0]
        if kind == "log":
            self._log_line(msg[1], msg[2])
        elif kind == "progress":
            self.progress.set(max(0.0, min(1.0, msg[1])))
            self.lbl_pct.configure(text=msg[2])
        elif kind == "kpi":
            self._set_kpi(msg[1])
        elif kind == "genres":
            self._refresh_genres(msg[1])
        elif kind == "hw":
            self.lbl_hw.configure(text=msg[1])
        elif kind == "model":
            self.lbl_dot.configure(text_color=C.ACCENT_OK)
        elif kind == "done":
            self._set_running(False)
            if msg[1] == "ok":
                self.progress.set(1.0)
                self.lbl_pct.configure(text="Listo ✔")
            elif msg[1] == "paused":
                self.lbl_pct.configure(text="Pausado")

    # ── acciones ────────────────────────────────────────────
    def _on_browse(self):
        folder = filedialog.askdirectory(title="Elegí tu carpeta de música")
        if folder:
            self.var_folder.set(os.path.normpath(folder))

    def _validate_folder(self, cfg):
        if not cfg["folder"] or not os.path.isdir(cfg["folder"]):
            messagebox.showwarning(C.APP_NAME, "Elegí primero una carpeta de música válida.")
            return False
        return True

    def _check_rekordbox_closed(self, cfg):
        """Si Rekordbox está abierto, pide cerrarlo. Cancelar = seguir solo con tags."""
        if not cfg["sync_rb"]:
            return
        while rb_db_sync.is_rekordbox_running():
            retry = messagebox.askretrycancel(
                "Rekordbox está abierto",
                "Para escribir las estrellitas en la base de datos de Rekordbox tenés que CERRARLO.\n\n"
                "• Reintentar: ya lo cerré.\n"
                "• Cancelar: continuar sin tocar la base de Rekordbox (solo tags en los archivos).")
            if not retry:
                cfg["sync_rb"] = False
                self._log_line("ℹ Se continúa sin sincronizar con Rekordbox (seguía abierto).", "warn")
                return

    def _launch(self, mode):
        cfg = self._get_config()
        if not self._validate_folder(cfg):
            return
        self._check_rekordbox_closed(cfg)
        self._set_running(True)
        self.progress.set(0)
        self.lbl_pct.configure(text="0%")
        self.worker = Worker(mode, cfg, self.ui_q)
        self.worker.start()

    def _on_start(self):
        self._launch("analyze")

    def _on_reapply(self):
        self._launch("reapply")

    def _on_pause(self):
        if self.worker is not None and self.worker.is_alive():
            self.worker.stop_event.set()
            self.btn_pause.configure(state="disabled", text="Guardando en caché...")

    def _on_clear(self):
        cfg = self._get_config()
        if not self._validate_folder(cfg):
            return
        lines = []
        if cfg["write_tags"]:
            lines.append("• Archivos de audio: estrellas (POPM/RATING), el prefijo [🔥 ...] del "
                         "comentario y el Grouping. Tu comentario original, el Género, Título, "
                         "Artista y BPM NO se tocan.")
        if cfg["sync_rb"]:
            lines.append("• Base de Rekordbox (master.db): Rating = 0, color = 0 y prefijo [🔥 ...] "
                         "del comentario.")
        lines.append("• Caché de análisis: esas pistas se borran para poder analizarlas de cero.")
        msg = (f"Se van a limpiar las estrellitas de TODAS las canciones de:\n{cfg['folder']}\n\n"
               + "\n".join(lines) + "\n\n¿Querés continuar?")
        if not messagebox.askyesno(C.APP_NAME + " - Limpiar Estrellas", msg, icon="warning"):
            return
        self._launch("clear")

    def _apply_icon(self):
        try:
            import icon_generator
            path = icon_generator.ensure_icon()
            if path and os.path.isfile(path) and os.name == "nt":
                self.iconbitmap(path)
        except Exception:
            pass

    def _show_startup_warning(self):
        top = ctk.CTkToplevel(self)
        top.title(C.APP_NAME)
        top.configure(fg_color=C.BG_MAIN)
        top.resizable(False, False)
        w, h = 660, 500
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - w) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - h) // 2)
        top.geometry(f"{w}x{h}+{x}+{y}")
        top.transient(self)

        def close():
            try:
                top.grab_release()
            except tk.TclError:
                pass
            top.destroy()

        top.protocol("WM_DELETE_WINDOW", close)

        ctk.CTkFrame(top, height=5, fg_color=C.ACCENT_RED, corner_radius=0).pack(fill="x")
        ctk.CTkLabel(top, text="⚠️ IMPORTANTE: Flujo de Trabajo Recomendado",
                     font=("Segoe UI", 20, "bold"), text_color=C.TEXT_MAIN,
                     wraplength=w - 60, justify="left").pack(anchor="w", padx=30, pady=(24, 10))
        ctk.CTkLabel(top, text="Para evitar errores de lectura en las compacteras o en tu biblioteca:",
                     font=("Segoe UI", 13), text_color=C.TEXT_SOFT, wraplength=w - 60,
                     justify="left").pack(anchor="w", padx=30, pady=(0, 12))

        items = (
            ("1", "NO analices con este programa la música que YA está exportada lista para "
                  "tocar en tu pendrive."),
            ("2", "Primero analizá tus pistas en tu carpeta de la PC (y en Rekordbox), pasalas por "
                  "Analizer IA local para ponerles las estrellitas, y RECIÉN DESPUÉS exportá los "
                  "tracks a tu pendrive."),
        )
        for num, text in items:
            card = ctk.CTkFrame(top, fg_color=C.BG_ELEMENT, corner_radius=10)
            card.pack(fill="x", padx=30, pady=6)
            ctk.CTkLabel(card, text=num, font=("Segoe UI", 22, "bold"), text_color=C.ACCENT_RED,
                         width=34).pack(side="left", padx=(14, 4), pady=14, anchor="n")
            ctk.CTkLabel(card, text=text, font=("Segoe UI", 13), text_color=C.TEXT_MAIN,
                         wraplength=w - 140, justify="left").pack(side="left", padx=(4, 16), pady=14)

        ctk.CTkButton(top, text="Entendido, continuar", command=close, fg_color=C.ACCENT_RED,
                      hover_color=C.ACCENT_HOVER, text_color=C.TEXT_MAIN,
                      font=("Segoe UI", 14, "bold"), height=46, corner_radius=8
                      ).pack(fill="x", padx=30, pady=(22, 8))
        ctk.CTkLabel(top, text=C.FOOTER_TEXT, font=("Segoe UI", 10),
                     text_color=C.TEXT_MUTED).pack(pady=(0, 10))

        def finish_setup():
            try:
                top.lift()
                top.focus_force()
                top.grab_set()
            except tk.TclError:
                pass
            try:
                import icon_generator
                ico = icon_generator.ensure_icon()
                if ico and os.name == "nt":
                    top.iconbitmap(ico)
            except Exception:
                pass

        top.after(250, finish_setup)

    def _on_close(self):
        if self.worker is not None and self.worker.is_alive():
            self.worker.stop_event.set()
            self.after(400, self.destroy)
        else:
            self.destroy()


def run_app():
    app = AnalizerApp()
    app.mainloop()
