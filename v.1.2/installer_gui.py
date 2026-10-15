# -*- coding: utf-8 -*-
"""
installer_gui.py - Instalador gráfico (tkinter puro) de Analizer IA local  •  by @tomasanfo

Se abre al instante (solo usa la librería estándar) y ejecuta TODO con subprocess ocultos
(CREATE_NO_WINDOW): venv, PyTorch (CUDA o CPU), dependencias, ícono y accesos directos.
Registro completo en install.log (junto a este archivo).
"""
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

# ─────────────────────────────────────────────────────────────
# CONSTANTES
# ─────────────────────────────────────────────────────────────
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
ERR = "#F87171"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VENV_DIR = os.path.join(BASE_DIR, "venv")
VENV_PY = os.path.join(VENV_DIR, "Scripts", "python.exe")
VENV_PYW = os.path.join(VENV_DIR, "Scripts", "pythonw.exe")
MARKER = os.path.join(VENV_DIR, ".installed_ok")
MAIN_PY = os.path.join(BASE_DIR, "main.py")
ICON_PATH = os.path.join(BASE_DIR, "app_icon.ico")
REQ_PATH = os.path.join(BASE_DIR, "requirements.txt")
LOG_PATH = os.path.join(BASE_DIR, "install.log")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)

MIN_PY, MAX_PY = (3, 10), (3, 13)         # versiones con wheels de PyTorch / librosa
PY_PREFERENCE = ("3.12", "3.11", "3.10", "3.13")
CUDA_TAGS = ("cu126", "cu124", "cu121")    # se prueban en orden; si ninguno sirve, CPU
OPTIONAL_PKGS = ("pyrekordbox", "sqlcipher3-wheels")   # si fallan, la app igual funciona

STEPS = [
    (15, "Preparando entorno virtual aislado (venv)..."),
    (40, "Detectando placa de video NVIDIA e instalando motor PyTorch..."),
    (70, "Descargando librerías de audio e Inteligencia Artificial..."),
    (85, "Generando logo oficial (app_icon.ico) y empaquetando lanzador..."),
    (100, "Creando accesos directos en Escritorio y Menú Inicio..."),
]


def _set_app_id(app_id):
    """Identidad propia en la barra de tareas (si no, Windows muestra el ícono de Python)."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


class InstallError(Exception):
    def __init__(self, message, kind="generic"):
        super().__init__(message)
        self.kind = kind


# ─────────────────────────────────────────────────────────────
# TRABAJO DE INSTALACIÓN (hilo; solo habla con la UI por cola)
# ─────────────────────────────────────────────────────────────
class InstallWorker(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.warnings = []
        self.current_proc = None
        self.cancelled = False
        self.log_file = None

    # ── utilidades ──────────────────────────────────────────
    def log(self, text):
        try:
            if self.log_file:
                self.log_file.write(text.rstrip("\n") + "\n")
                self.log_file.flush()
        except Exception:
            pass

    def warn(self, text):
        self.warnings.append(text)
        self.log("[AVISO] " + text)

    def detail(self, text):
        self.q.put(("detail", text))

    def step(self, index):
        pct, text = STEPS[index]
        ceil = STEPS[index + 1][0] if index + 1 < len(STEPS) else 100
        self.log(f"\n=== PASO {index + 1}: {text}")
        self.q.put(("step", pct, ceil, text))

    def _env(self):
        env = dict(os.environ)
        env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def capture(self, cmd, timeout=40):
        """Ejecuta oculto y devuelve (codigo, salida). Nunca lanza excepción."""
        try:
            p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=NO_WINDOW, timeout=timeout,
                               text=True, encoding="utf-8", errors="replace", env=self._env())
            return p.returncode, p.stdout or ""
        except Exception as e:
            return -1, str(e)

    def exec_cmd(self, cmd, cwd=None):
        """Ejecuta oculto, vuelca la salida a install.log y muestra la última línea en la UI."""
        if self.cancelled:
            raise InstallError("Instalación cancelada.")
        self.log("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd or BASE_DIR, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, creationflags=NO_WINDOW, text=True, encoding="utf-8",
                errors="replace", env=self._env())
        except Exception as e:
            self.log(f"No se pudo ejecutar: {e}")
            return -1
        self.current_proc = proc
        try:
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                self.log(line)
                self.detail(line[:90])
        finally:
            proc.wait()
            self.current_proc = None
        self.log(f"-> código de salida {proc.returncode}")
        return proc.returncode

    def cancel(self):
        self.cancelled = True
        p = self.current_proc
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass

    def pip(self, *args):
        return self.exec_cmd([VENV_PY, "-m", "pip", *args, "--progress-bar", "off",
                         "--disable-pip-version-check"])

    # ── ciclo de vida ───────────────────────────────────────
    def run_all(self):
        self.step(0)
        py = self.find_python()
        self.ensure_venv(py)

        self.step(1)
        self.install_torch()

        self.step(2)
        self.install_requirements()

        self.step(3)
        self.make_icon()

        self.step(4)
        self.make_shortcuts()
        try:
            with open(MARKER, "w", encoding="utf-8") as f:
                f.write("ok\n")
        except Exception:
            pass

    def run(self):
        """Punto de entrada del hilo."""
        try:
            self.log_file = open(LOG_PATH, "w", encoding="utf-8")
        except Exception:
            self.log_file = None
        self.log(f"Instalador de {APP_NAME} - {time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"Carpeta: {BASE_DIR}")
        try:
            self.run_all()
            self.q.put(("ok", list(self.warnings)))
        except InstallError as e:
            self.log(f"[ERROR] {e}")
            self.q.put(("fail", str(e), e.kind))
        except Exception as e:  # noqa
            import traceback
            self.log(traceback.format_exc())
            self.q.put(("fail", f"Error inesperado: {e}", "generic"))
        finally:
            try:
                if self.log_file:
                    self.log_file.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────
# PASOS DE LA INSTALACIÓN (se enlazan como métodos de InstallWorker más abajo)
# ─────────────────────────────────────────────────────────────
def _find_python(self):
    """Busca un Python 3.10-3.13 con venv/ensurepip. Prioriza 3.12 (wheels de PyTorch)."""
    candidates = [["py", f"-{v}"] for v in PY_PREFERENCE]
    cur = sys.executable or ""
    if cur:
        if cur.lower().endswith("pythonw.exe"):
            sib = os.path.join(os.path.dirname(cur), "python.exe")
            cur = sib if os.path.isfile(sib) else cur
        candidates.append([cur])
    candidates.append(["python"])

    probe = "import sys, venv, ensurepip; print(sys.executable); print(sys.version_info[0], sys.version_info[1])"
    for cmd in candidates:
        code, out = self.capture(cmd + ["-c", probe])
        if code != 0:
            continue
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        try:
            exe = lines[-2]
            major, minor = (int(x) for x in lines[-1].split()[:2])
        except ValueError:
            continue
        if MIN_PY <= (major, minor) <= MAX_PY:
            if exe.lower().endswith("pythonw.exe"):
                sib = os.path.join(os.path.dirname(exe), "python.exe")
                exe = sib if os.path.isfile(sib) else exe
            self.log(f"Python elegido: {exe} ({major}.{minor})")
            self.detail(f"Usando Python {major}.{minor}")
            return exe
    raise InstallError(
        "No encontré Python 3.10 a 3.12 (recomendado 3.12).\n"
        "Instalalo desde python.org marcando «Add Python to PATH» y volvé a abrir el instalador.",
        kind="python")


def _ensure_venv(self, py):
    if os.path.isfile(VENV_PY):
        code, out = self.capture([VENV_PY, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"])
        ok = False
        if code == 0:
            try:
                major, minor = (int(x) for x in out.split()[:2])
                ok = MIN_PY <= (major, minor) <= MAX_PY
            except ValueError:
                ok = False
        if not ok:
            self.log("El venv existente usa un Python incompatible: lo recreo.")
            self.detail("Recreando venv con un Python compatible...")
            shutil.rmtree(VENV_DIR, ignore_errors=True)
    if not os.path.isfile(VENV_PY):
        self.detail("Creando entorno virtual...")
        if self.exec_cmd([py, "-m", "venv", VENV_DIR]) != 0 or not os.path.isfile(VENV_PY):
            raise InstallError("No pude crear el entorno virtual (venv). Mirá install.log.")
    try:
        if os.path.isfile(MARKER):
            os.remove(MARKER)
    except OSError:
        pass
    if self.pip("install", "--upgrade", "pip", "setuptools", "wheel") != 0:
        self.warn("No pude actualizar pip (se continúa con la versión incluida).")


def _has_nvidia(self):
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    for exe in ("nvidia-smi",
                os.path.join(sysroot, "System32", "nvidia-smi.exe"),
                r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        code, out = self.capture([exe, "-L"], timeout=15)
        if code == 0 and "GPU" in out:
            self.log("GPU NVIDIA detectada: " + out.strip().splitlines()[0])
            return True
    return False


def _install_torch(self):
    gpu = self.has_nvidia()
    torch_ok = False
    if gpu:
        for tag in CUDA_TAGS:
            self.detail(f"NVIDIA detectada: instalando PyTorch CUDA ({tag})...")
            code = self.pip("install", "torch", "--index-url", f"https://download.pytorch.org/whl/{tag}",
                            "--extra-index-url", "https://pypi.org/simple")
            if code == 0:
                torch_ok = True
                break
            self.log(f"PyTorch {tag} no disponible para este Python; pruebo el siguiente.")
        if not torch_ok:
            self.warn("No pude instalar PyTorch con CUDA: se instala la versión CPU.")
    else:
        self.log("No se detectó GPU NVIDIA: PyTorch CPU.")
    if not torch_ok:
        self.detail("Instalando PyTorch (CPU)...")
        if self.pip("install", "torch") != 0:
            raise InstallError("No pude instalar PyTorch. Mirá install.log "
                               "(suele ser un Python demasiado nuevo: usá 3.12).")
    code, out = self.capture([VENV_PY, "-c", "import torch; print(torch.__version__, torch.cuda.is_available())"],
                             timeout=120)
    self.log("Verificación torch: " + out.strip())
    if gpu and torch_ok and "True" not in out:
        self.warn("PyTorch quedó instalado pero no ve la GPU: la IA correrá en CPU "
                  "(actualizá el driver de NVIDIA si querés usar CUDA).")


def _pkg_name(line):
    name = line.strip()
    for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", ";", " "):
        name = name.split(sep)[0]
    return name.strip().lower()


def _install_requirements(self):
    if not os.path.isfile(REQ_PATH):
        raise InstallError("Falta requirements.txt junto al instalador.")
    self.detail("Descargando librerías de audio e IA (puede tardar varios minutos)...")
    if self.pip("install", "-r", REQ_PATH) == 0:
        return
    self.log("Falló la instalación en bloque: pruebo paquete por paquete.")
    with open(REQ_PATH, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
    for line in lines:
        name = _pkg_name(line)
        self.detail(f"Instalando {name}...")
        if self.pip("install", line) != 0:
            if name in OPTIONAL_PKGS:
                self.warn(f"No pude instalar «{name}»: la sincronización con Rekordbox no estará "
                          f"disponible (los tags de los archivos sí funcionan).")
            else:
                raise InstallError(f"No pude instalar «{name}». Mirá install.log.")


def _make_icon(self):
    self.detail("Dibujando app_icon.ico con Pillow...")
    script = os.path.join(BASE_DIR, "icon_generator.py")
    if os.path.isfile(script):
        self.exec_cmd([VENV_PY, script])
    if os.path.isfile(ICON_PATH):
        self.q.put(("icon",))
    else:
        self.warn("No pude generar app_icon.ico: los accesos directos usarán el ícono de Python.")


def _ps_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _make_shortcuts(self):
    if not os.path.isfile(VENV_PYW):
        raise InstallError("No encuentro venv\\Scripts\\pythonw.exe: el venv quedó incompleto.")
    name = f"{APP_NAME}.lnk"
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$ws = New-Object -ComObject WScript.Shell",
        f"$target = {_ps_quote(VENV_PYW)}",
        f"$arguments = {_ps_quote(chr(34) + MAIN_PY + chr(34))}",
        f"$work = {_ps_quote(BASE_DIR)}",
        f"$icon = {_ps_quote(ICON_PATH)}",
        f"$name = {_ps_quote(name)}",
        "foreach ($folder in @('Desktop', 'Programs')) {",
        "  try {",
        "    $dir = [Environment]::GetFolderPath($folder)",
        "    if (-not $dir) { continue }",
        "    New-Item -ItemType Directory -Force -Path $dir | Out-Null",
        "    $lnk = $ws.CreateShortcut((Join-Path $dir $name))",
        "    $lnk.TargetPath = $target",
        "    $lnk.Arguments = $arguments",
        "    $lnk.WorkingDirectory = $work",
        "    if (Test-Path $icon) { $lnk.IconLocation = $icon + ',0' }",
        f"    $lnk.Description = {_ps_quote(APP_NAME + '  -  by @tomasanfo')}",
        "    $lnk.Save()",
        "    Write-Output ('OK ' + $folder + ' ' + $dir)",
        "  } catch { Write-Output ('FAIL ' + $folder + ' ' + $_.Exception.Message) }",
        "}",
    ])
    ps1 = os.path.join(os.environ.get("TEMP", BASE_DIR), "analizer_shortcuts.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:      # BOM: PowerShell 5 lee bien los acentos
        f.write(script)
    self.detail("Creando accesos directos...")
    code, out = self.capture(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                              "Bypass", "-File", ps1], timeout=60)
    self.log(out.strip())
    try:
        os.remove(ps1)
    except OSError:
        pass
    created = [ln for ln in out.splitlines() if ln.startswith("OK ")]
    if not created:
        self.warn("No pude crear los accesos directos. Podés abrir el programa con "
                  "venv\\Scripts\\pythonw.exe main.py.")
    elif len(created) < 2:
        self.warn("Solo pude crear uno de los dos accesos directos (Escritorio / Menú Inicio).")


# Enlazamos los pasos como métodos del worker
InstallWorker.find_python = _find_python
InstallWorker.ensure_venv = _ensure_venv
InstallWorker.has_nvidia = _has_nvidia
InstallWorker.install_torch = _install_torch
InstallWorker.install_requirements = _install_requirements
InstallWorker.make_icon = _make_icon
InstallWorker.make_shortcuts = _make_shortcuts


# ─────────────────────────────────────────────────────────────
# INTERFAZ
# ─────────────────────────────────────────────────────────────
class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Instalador • {APP_NAME}")
        self.configure(bg=BG_MAIN)
        self.resizable(False, False)
        w, h = 520, 320
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"{w}x{h}+{x}+{y}")

        # Ícono (si ya existe de una instalación previa)
        try:
            if os.path.isfile(ICON_PATH):
                self.iconbitmap(ICON_PATH)
        except Exception:
            pass

        self.q = queue.Queue()
        self.worker = None
        self.running = True
        self.floor = 0.0
        self.ceil = 0.0
        self.disp = 0.0
        self.finished = False

        tk.Frame(self, bg=ACCENT, height=4).pack(fill="x")
        tk.Label(self, text=f"Instalador • {APP_NAME}", bg=BG_MAIN, fg=TEXT_MAIN,
                 font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=32, pady=(22, 24))

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Red.Horizontal.TProgressbar", troughcolor=BG_ELEMENT, background=ACCENT,
                        bordercolor=BG_ELEMENT, lightcolor=ACCENT, darkcolor=ACCENT, thickness=10)

        row = tk.Frame(self, bg=BG_MAIN)
        row.pack(fill="x", padx=32)
        self.bar = ttk.Progressbar(row, style="Red.Horizontal.TProgressbar", maximum=100,
                                   mode="determinate", length=380)
        self.bar.pack(side="left", fill="x", expand=True)
        self.lbl_pct = tk.Label(row, text="0%", bg=BG_MAIN, fg=TEXT_MAIN, width=5, anchor="e",
                                font=("Segoe UI", 12, "bold"))
        self.lbl_pct.pack(side="left", padx=(12, 0))

        self.lbl_status = tk.Label(self, text="Iniciando...", bg=BG_MAIN, fg=TEXT_MAIN, anchor="w",
                                   justify="left", wraplength=456, font=("Segoe UI", 11))
        self.lbl_status.pack(fill="x", padx=32, pady=(18, 2))
        self.lbl_detail = tk.Label(self, text="", bg=BG_MAIN, fg=TEXT_MUTED, anchor="w",
                                   justify="left", wraplength=456, font=("Consolas", 9))
        self.lbl_detail.pack(fill="x", padx=32)

        self.btn_frame = tk.Frame(self, bg=BG_MAIN)
        self.btn_frame.pack(side="bottom", fill="x", padx=32, pady=(0, 22))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.worker = InstallWorker(self.q)
        self.worker.start()
        self.after(30, self._tick)
        self.after(100, self._poll)

    # ── botones ─────────────────────────────────────────────
    def _button(self, text, command, primary=True):
        bg = ACCENT if primary else BG_ELEMENT
        hover = ACCENT_HOVER if primary else "#1C1C22"
        b = tk.Button(self.btn_frame, text=text, command=command, bg=bg, fg=TEXT_MAIN,
                      activebackground=hover, activeforeground=TEXT_MAIN, relief="flat", bd=0,
                      cursor="hand2", font=("Segoe UI", 11, "bold"), padx=16, pady=9)
        b.bind("<Enter>", lambda _e: b.configure(bg=hover))
        b.bind("<Leave>", lambda _e: b.configure(bg=bg))
        return b

    def _clear_buttons(self):
        for child in self.btn_frame.winfo_children():
            child.destroy()

    # ── animación de la barra ───────────────────────────────
    def _tick(self):
        if self.disp < self.floor:
            self.disp = min(self.floor, self.disp + max(0.35, (self.floor - self.disp) * 0.12))
        elif self.running and self.disp < self.ceil - 0.5:
            self.disp += (self.ceil - self.disp) * 0.0012      # avance lento mientras trabaja
        self.bar["value"] = self.disp
        self.lbl_pct.configure(text=f"{int(self.disp)}%")
        self.after(30, self._tick)

    # ── mensajes del hilo ───────────────────────────────────
    def _poll(self):
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, msg):
        kind = msg[0]
        if kind == "step":
            _, pct, ceil, text = msg
            self.floor, self.ceil = float(pct), float(ceil)
            self.lbl_status.configure(text=text, fg=TEXT_MAIN)
            self.lbl_detail.configure(text="")
        elif kind == "detail":
            self.lbl_detail.configure(text=msg[1])
        elif kind == "icon":
            try:
                self.iconbitmap(ICON_PATH)
            except Exception:
                pass
        elif kind == "ok":
            self._on_success(msg[1])
        elif kind == "fail":
            self._on_failure(msg[1], msg[2])

    def _on_success(self, warnings):
        self.running = False
        self.finished = True
        self.floor = self.ceil = 100.0
        self.lbl_status.configure(text="✔ ¡Instalación completada con éxito!", fg=OK_GREEN)
        if warnings:
            self.lbl_detail.configure(text="Avisos: " + " ".join(warnings), fg=WARN)
        else:
            self.lbl_detail.configure(text="")
        self._clear_buttons()
        self._button("🚀 Finalizar y Abrir Analizer IA local", self._launch_app).pack(fill="x")
        if not warnings:
            self._countdown(2)

    def _countdown(self, n):
        if not self.finished:
            return
        if n <= 0:
            self._launch_app()
            return
        self.lbl_detail.configure(text=f"Abriendo automáticamente en {n}s...", fg=TEXT_MUTED)
        self.after(1000, lambda: self._countdown(n - 1))

    def _on_failure(self, message, kind):
        self.running = False
        self.finished = False
        self.lbl_status.configure(text="✖ La instalación no pudo completarse", fg=ERR)
        self.lbl_detail.configure(text=message, fg=TEXT_SOFT, font=("Segoe UI", 10))
        self._clear_buttons()
        if kind == "python":
            self._button("Descargar Python 3.12",
                         lambda: webbrowser.open("https://www.python.org/downloads/windows/")
                         ).pack(side="left")
        else:
            self._button("Ver registro", self._open_log).pack(side="left")
        self._button("Cerrar", self.destroy, primary=False).pack(side="right")

    def _open_log(self):
        try:
            os.startfile(LOG_PATH)
        except Exception:
            messagebox.showinfo(APP_NAME, f"El registro está en:\n{LOG_PATH}")

    def _launch_app(self):
        if not self.finished:
            return
        self.finished = False
        try:
            subprocess.Popen([VENV_PYW, MAIN_PY], cwd=BASE_DIR, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW | DETACHED, close_fds=True)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"No pude abrir el programa: {e}")
            return
        self.destroy()

    def _on_close(self):
        if self.running:
            if not messagebox.askyesno(APP_NAME, "La instalación sigue en curso.\n¿Querés cancelarla y salir?"):
                return
            if self.worker is not None:
                self.worker.cancel()
        self.destroy()


def main():
    _set_app_id("tomasanfo.AnalizerIALocal.Installer")
    app = InstallerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
