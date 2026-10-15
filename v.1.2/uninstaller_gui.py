# -*- coding: utf-8 -*-
"""
uninstaller_gui.py - Desinstalador gráfico (tkinter puro) de Analizer IA local  •  by @tomasanfo

Cierra el programa si está abierto, borra los accesos directos (Escritorio y Menú Inicio),
venv\\, tracks_cache.db, __pycache__\\, app_icon.ico, errores y registros.
JAMÁS toca tus carpetas de música ni los backups de Rekordbox.
"""
import os
import queue
import shutil
import stat
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

APP_NAME = "Analizer IA local"
BG_MAIN = "#070709"
BG_ELEMENT = "#121216"
ACCENT = "#DC2626"
ACCENT_HOVER = "#B91C1C"
TEXT_MAIN = "#FAFAFA"
TEXT_SOFT = "#D4D4D8"
TEXT_MUTED = "#71717A"
OK_GREEN = "#22C55E"
WARN = "#F59E0B"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, "venv")
ICON_PATH = os.path.join(BASE_DIR, "app_icon.ico")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
SHORTCUT_NAME = f"{APP_NAME}.lnk"

# Archivos y carpetas propios del programa que se eliminan (nada fuera de BASE_DIR)
FILES_TO_REMOVE = ["tracks_cache.db", "tracks_cache.db-journal", "tracks_cache.db-wal",
                   "tracks_cache.db-shm", "app_icon.ico", "errors.log", "analizer_error.log",
                   "install.log"]
DIRS_TO_REMOVE = ["__pycache__", ".numba_cache"]


def _set_app_id(app_id):
    """Identidad propia en la barra de tareas (si no, Windows muestra el ícono de Python)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _run_hidden(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=timeout,
                           text=True, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or ""
    except Exception as e:
        return -1, str(e)


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _shortcut_dirs():
    """Escritorio y Menú Inicio reales (respeta OneDrive / carpetas redirigidas)."""
    found = []
    cmd = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
           "[Environment]::GetFolderPath('Desktop'); [Environment]::GetFolderPath('Programs')")
    code, out = _run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd], 30)
    if code == 0:
        found += [ln.strip() for ln in out.splitlines() if ln.strip()]
    home = os.path.expanduser("~")
    found += [os.path.join(home, "Desktop"),
              os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")),
                           "Microsoft", "Windows", "Start Menu", "Programs")]
    unique, seen = [], set()
    for d in found:
        key = os.path.normcase(os.path.normpath(d))
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            unique.append(d)
    return unique


def _force_remove(func, path, _exc):
    """Handler de rmtree: quita solo-lectura y reintenta."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _rmtree(path):
    """Borra una carpeta con reintentos (antivirus / procesos cerrándose). True si desapareció."""
    for _ in range(4):
        shutil.rmtree(path, onerror=_force_remove)
        if not os.path.exists(path):
            return True
        time.sleep(0.8)
    return not os.path.exists(path)


# ─────────────────────────────────────────────────────────────
# TRABAJO (hilo)
# ─────────────────────────────────────────────────────────────
class UninstallWorker(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.problems = []

    def progress(self, pct, text):
        self.q.put(("progress", pct, text))

    def run(self):
        try:
            self._close_processes()
            self._remove_shortcuts()
            self._remove_venv()
            self._remove_files()
            self.progress(100, "Finalizando...")
            time.sleep(0.4)
            self.q.put(("done", list(self.problems)))
        except Exception as e:  # noqa
            self.q.put(("error", str(e)))

    def _close_processes(self):
        self.progress(8, "Cerrando Analizer IA local si está abierto...")
        script = "\n".join([
            f"$venv = {_ps_quote(VENV_DIR)}",
            f"$me = {os.getpid()}",
            "Get-CimInstance Win32_Process | Where-Object {",
            "  $_.ExecutablePath -and $_.ProcessId -ne $me -and",
            "  $_.ExecutablePath.StartsWith($venv, [System.StringComparison]::OrdinalIgnoreCase)",
            "} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }",
        ])
        ps1 = os.path.join(os.environ.get("TEMP", BASE_DIR), "analizer_close.ps1")
        try:
            with open(ps1, "w", encoding="utf-8-sig") as f:
                f.write(script)
            _run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                         "Bypass", "-File", ps1], 45)
        finally:
            try:
                os.remove(ps1)
            except OSError:
                pass
        time.sleep(1.0)      # darle tiempo a Windows para soltar los archivos

    def _remove_shortcuts(self):
        self.progress(30, "Quitando accesos directos del Escritorio y Menú Inicio...")
        for d in _shortcut_dirs():
            lnk = os.path.join(d, SHORTCUT_NAME)
            if os.path.isfile(lnk):
                try:
                    os.remove(lnk)
                except OSError as e:
                    self.problems.append(f"No pude borrar {lnk}: {e}")
        time.sleep(0.3)

    def _remove_venv(self):
        self.progress(55, "Liberando espacio: eliminando el entorno de IA (venv)...")
        if os.path.isdir(VENV_DIR) and not _rmtree(VENV_DIR):
            self.problems.append("No pude borrar por completo la carpeta venv (¿algo la está usando?). "
                                 "Reiniciá la PC y borrala a mano.")

    def _remove_files(self):
        self.progress(85, "Borrando caché, ícono, registros y archivos temporales...")
        for name in FILES_TO_REMOVE:
            path = os.path.join(BASE_DIR, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError as e:
                    self.problems.append(f"No pude borrar {name}: {e}")
        for name in DIRS_TO_REMOVE:
            path = os.path.join(BASE_DIR, name)
            if os.path.isdir(path) and not _rmtree(path):
                self.problems.append(f"No pude borrar la carpeta {name}.")


# ─────────────────────────────────────────────────────────────
# INTERFAZ
# ─────────────────────────────────────────────────────────────
class UninstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Desinstalador • {APP_NAME}")
        self.configure(bg=BG_MAIN)
        self.resizable(False, False)
        w, h = 520, 320
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")
        try:
            if os.path.isfile(ICON_PATH):
                self.iconbitmap(ICON_PATH)
        except Exception:
            pass

        self.q = queue.Queue()
        self.running = False
        self.disp = 0.0
        self.target = 0.0

        tk.Frame(self, bg=ACCENT, height=4).pack(fill="x")
        tk.Label(self, text=f"Desinstalador • {APP_NAME}", bg=BG_MAIN, fg=TEXT_MAIN,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=32, pady=(22, 0))
        tk.Label(self, text="by @tomasanfo", bg=BG_MAIN, fg=ACCENT,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=32, pady=(0, 16))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Red.Horizontal.TProgressbar", troughcolor=BG_ELEMENT, background=ACCENT,
                        bordercolor=BG_ELEMENT, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

        self.body = tk.Frame(self, bg=BG_MAIN)
        self.body.pack(fill="both", expand=True, padx=32)
        self.btn_frame = tk.Frame(self, bg=BG_MAIN)
        self.btn_frame.pack(side="bottom", fill="x", padx=32, pady=(0, 22))

        self._show_question()
        self.after(30, self._tick)
        self.after(100, self._poll)

    # ── helpers de UI ───────────────────────────────────────
    def _button(self, text, command, primary=True, **pack):
        bg = ACCENT if primary else BG_ELEMENT
        hover = ACCENT_HOVER if primary else "#1C1C22"
        b = tk.Button(self.btn_frame, text=text, command=command, bg=bg, fg=TEXT_MAIN,
                      activebackground=hover, activeforeground=TEXT_MAIN, relief="flat", bd=0,
                      cursor="hand2", font=("Segoe UI", 11, "bold"), padx=16, pady=9)
        b.bind("<Enter>", lambda _e: b.configure(bg=hover))
        b.bind("<Leave>", lambda _e: b.configure(bg=bg))
        b.pack(**pack)
        return b

    def _clear(self, frame):
        for child in frame.winfo_children():
            child.destroy()

    # ── pantallas ───────────────────────────────────────────
    def _show_question(self):
        self._clear(self.body)
        self._clear(self.btn_frame)
        tk.Label(self.body,
                 text="¿Deseás desinstalar Analizer IA local, quitar el ícono del Escritorio y "
                      "liberar el espacio del entorno IA?",
                 bg=BG_MAIN, fg=TEXT_MAIN, font=("Segoe UI", 12), wraplength=456, justify="left"
                 ).pack(anchor="w", pady=(0, 10))
        tk.Label(self.body,
                 text="Tus carpetas de música NO se tocan. Las estrellitas ya escritas en tus "
                      "archivos y en Rekordbox tampoco (para quitarlas usá antes «🧹 Limpiar "
                      "Estrellas» dentro del programa).",
                 bg=BG_MAIN, fg=TEXT_MUTED, font=("Segoe UI", 10), wraplength=456, justify="left"
                 ).pack(anchor="w")
        self._button("Sí, desinstalar", self._start, side="left")
        self._button("Cancelar", self.destroy, primary=False, side="right")

    def _start(self):
        self._clear(self.body)
        self._clear(self.btn_frame)
        row = tk.Frame(self.body, bg=BG_MAIN)
        row.pack(fill="x", pady=(6, 0))
        self.bar = ttk.Progressbar(row, style="Red.Horizontal.TProgressbar", maximum=100,
                                   mode="determinate")
        self.bar.pack(side="left", fill="x", expand=True)
        self.lbl_pct = tk.Label(row, text="0%", bg=BG_MAIN, fg=TEXT_MAIN, width=5, anchor="e",
                                font=("Segoe UI", 12, "bold"))
        self.lbl_pct.pack(side="left", padx=(12, 0))
        self.lbl_status = tk.Label(self.body, text="Preparando...", bg=BG_MAIN, fg=TEXT_MAIN,
                                   font=("Segoe UI", 11), wraplength=456, justify="left", anchor="w")
        self.lbl_status.pack(fill="x", pady=(18, 2))
        self.lbl_detail = tk.Label(self.body, text="", bg=BG_MAIN, fg=TEXT_MUTED,
                                   font=("Segoe UI", 9), wraplength=456, justify="left", anchor="w")
        self.lbl_detail.pack(fill="x")
        self.running = True
        self.protocol("WM_DELETE_WINDOW", lambda: None)     # no cerrar a mitad de camino
        UninstallWorker(self.q).start()

    # ── animación y mensajes ────────────────────────────────
    def _tick(self):
        if self.running and hasattr(self, "bar"):
            if self.disp < self.target:
                self.disp = min(self.target, self.disp + max(0.6, (self.target - self.disp) * 0.15))
            self.bar["value"] = self.disp
            self.lbl_pct.configure(text=f"{int(self.disp)}%")
        self.after(30, self._tick)

    def _poll(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "progress":
            self.target = float(msg[1])
            self.lbl_status.configure(text=msg[2])
        elif kind == "done":
            self.target = 100.0
            self.disp = 100.0
            self.bar["value"] = 100
            self.lbl_pct.configure(text="100%")
            self.running = False
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            problems = msg[1]
            if problems:
                self.lbl_status.configure(text="Desinstalación completada con avisos", fg=WARN)
                self.lbl_detail.configure(text="\n".join(problems[:4]), fg=TEXT_SOFT)
            else:
                self.lbl_status.configure(text="✔ Desinstalación completada con éxito", fg=OK_GREEN)
                self.lbl_detail.configure(
                    text="Ya podés borrar la carpeta del programa si querés. "
                         "Tu música quedó intacta.", fg=TEXT_MUTED)
            self._clear(self.btn_frame)
            self._button("Cerrar", self.destroy, fill="x")
        elif kind == "error":
            self.running = False
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.lbl_status.configure(text="✖ Ocurrió un error durante la desinstalación", fg="#F87171")
            self.lbl_detail.configure(text=msg[1], fg=TEXT_SOFT)
            self._clear(self.btn_frame)
            self._button("Cerrar", self.destroy, fill="x")


def main():
    _set_app_id("tomasanfo.AnalizerIALocal.Uninstaller")
    app = UninstallerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
