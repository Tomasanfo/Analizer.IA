# -*- coding: utf-8 -*-
"""
main.py - Punto de entrada seguro de Analizer IA local  •  by @tomasanfo
Verifica dependencias, registra errores en analizer_error.log y lanza la interfaz.
"""
import importlib.util
import os
import sys
import time
import traceback

# Bajo pythonw.exe stdout/stderr son None: las librerías que imprimen romperían.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("NUMBA_CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".numba_cache"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
ERROR_LOG = os.path.join(BASE_DIR, "analizer_error.log")

REQUIRED = ["customtkinter", "numpy", "scipy", "librosa", "soundfile", "mutagen", "torch", "transformers"]
OPTIONAL = ["pyrekordbox"]


def _log_error(text):
    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text + "\n")
    except Exception:
        pass


def _show_error(title, message):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"{title}: {message}")


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    _log_error(text)
    _show_error("Analizer IA local - Error inesperado",
                f"{exc}\n\nEl detalle quedó guardado en:\n{ERROR_LOG}")


def _check_dependencies():
    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        _show_error(
            "Faltan dependencias",
            "No encontré estos módulos:\n  • " + "\n  • ".join(missing) +
            "\n\nEjecutá Installer.vbs (doble clic) para instalarlos automáticamente.")
        return False
    for m in OPTIONAL:
        if importlib.util.find_spec(m) is None:
            _log_error(f"Aviso: falta {m}; la sincronización con Rekordbox no estará disponible.")
    return True


def main():
    if sys.version_info < (3, 9):
        _show_error("Python muy viejo", "Necesitás Python 3.9 o superior (recomendado 3.10 - 3.12).")
        return 1

    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("tomasanfo.AnalizerIALocal")
    except Exception:
        pass

    sys.excepthook = _excepthook

    if not _check_dependencies():
        return 1

    try:
        from gui import run_app
        run_app()
    except Exception:
        _excepthook(*sys.exc_info())
        return 1
    return 0


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
