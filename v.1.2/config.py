# -*- coding: utf-8 -*-
"""
config.py - Analizer IA local  •  by @tomasanfo
Paleta, prompts de CLAP, pesos de fusión, bytes POPM y utilidades compartidas.
"""
import os
import re
import unicodedata

# ─────────────────────────────────────────────────────────────
# IDENTIDAD
# ─────────────────────────────────────────────────────────────
APP_NAME = "Analizer IA local"
AUTHOR = "by @tomasanfo"
FOOTER_TEXT = f"{APP_NAME}  •  {AUTHOR}"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "tracks_cache.db")
ERROR_LOG = os.path.join(BASE_DIR, "analizer_error.log")

# ─────────────────────────────────────────────────────────────
# PALETA MINIMALISTA ROJO Y NEGRO
# ─────────────────────────────────────────────────────────────
BG_MAIN = "#070709"
BG_ELEMENT = "#121216"
BG_CONSOLE = "#030304"
ACCENT_RED = "#DC2626"
ACCENT_HOVER = "#B91C1C"
TEXT_MAIN = "#FAFAFA"
TEXT_MUTED = "#71717A"
TEXT_SOFT = "#D4D4D8"
ACCENT_OK = "#22C55E"
ACCENT_WARN = "#F59E0B"
ACCENT_ERR = "#F87171"

# ─────────────────────────────────────────────────────────────
# AUDIO
# ─────────────────────────────────────────────────────────────
AUDIO_EXTS = (".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a")

CLAP_MODEL_ID = "laion/clap-htsat-unfused"
SR_ANALYSIS = 16000          # sample rate de análisis (spec)
SR_CLAP = 48000              # CLAP exige 48 kHz: se re-muestrea solo el Drop
CENTER_SECONDS = 480         # se analiza el tema casi entero (tope; si dura más, se toma el centro)
DROP_WINDOW_SECONDS = 30     # ventana del Drop
CLAP_CHUNK_SECONDS = 10      # CLAP unfused procesa 10 s por entrada
MIN_AUDIO_SECONDS = 8        # pistas más cortas se descartan
HOP_LENGTH = 512
N_FFT = 2048

# ─────────────────────────────────────────────────────────────
# PROMPTS CLAP (Zero-Shot de energía). Misma cantidad por grupo.
# ─────────────────────────────────────────────────────────────
PROMPTS_HIGH = [
    "a massive explosive club drop with heavy punchy kick and powerful bass",
    "high energy peak time techno drop, loud, aggressive and intense",
    "an euphoric festival EDM drop with huge energy and driving rhythm",
    "a pounding dance floor banger with thundering bassline",
]
PROMPTS_MID = [
    "a steady groovy dance track with moderate energy",
    "a driving club groove with medium intensity",
    "a rhythmic house beat with balanced energy and smooth bassline",
    "a mid tempo electronic track building up gradually",
]
PROMPTS_LOW = [
    "a calm ambient and relaxing piece with very low energy",
    "a soft chill downtempo track with gentle beat, no drop",
    "a quiet minimal intro with sparse sounds and no bass",
    "a mellow atmospheric background music, slow and soft",
]

# Géneros candidatos SOLO para canciones sin género (Zero-Shot)
GENRE_CANDIDATES = [
    "Melodic Techno", "Techno", "Peak Time Techno", "Minimal / Deep Tech",
    "House", "Deep House", "Tech House", "Progressive House", "Afro House",
    "Organic House", "Indie Dance", "Electro", "Disco / Nu Disco",
    "Trance", "Drum & Bass", "Dubstep", "Hip Hop", "Pop", "Rock",
    "Reggaeton", "Latin", "Downtempo", "Ambient",
]
GENRE_PROMPT_TEMPLATE = "This is a {} music track."

# ─────────────────────────────────────────────────────────────
# FUSIÓN 50% IA + 50% DSP
# ─────────────────────────────────────────────────────────────
# CLAP "flashea": se le baja el peso y manda el DSP (medición real de la señal).
FUSION_WEIGHT_AI = 0.25
FUSION_WEIGHT_DSP = 0.75

# ─────────────────────────────────────────────────────────────
# INTENSIDAD SOSTENIDA (todo el tema, no solo el drop) y ROL DE SET
# ─────────────────────────────────────────────────────────────
# Explosividad final = pico (drop) + intensidad sostenida. Es lo que reparte estrellas.
W_PEAK = 0.45
W_SUSTAINED = 0.55

ACTIVE_DB = -10.0            # un frame cuenta como "a full" si su RMS supera este nivel (dBFS)
SUST_WEIGHTS = {"mean_db": 0.25, "active": 0.30, "low": 0.20, "onset": 0.15, "bright": 0.10}
SUST_RANGES = {
    "mean_db": (-28.0, -6.0),    # RMS medio de todo el tema
    "active": (0.20, 0.95),      # fracción del tema por encima de ACTIVE_DB
    "low": (-18.0, -4.0),        # graves 20-150 Hz: nivel mediano (dB) menos nivel del drop (p95)
    "onset": (0.8, 4.0),         # actividad percusiva media
    "bright": (1200.0, 3500.0),  # centroide espectral (Hz)
}

# Rol de set. Es RELATIVO AL GÉNERO (igual que las estrellas) y sin CLAP:
# role_index = mezcla de sostenida + pico; se rankea dentro del género.
ROLE_W_SUST = 0.70
ROLE_W_PEAK = 0.30
ROLE_LABELS = {1: "WARM UP", 2: "BUILD", 3: "MAIN", 4: "PEAK"}
# Cortes de percentil dentro del género: <=c0 WARM UP, <=c1 BUILD, <=c2 MAIN, resto PEAK
ROLE_PERCENTILE_CUTS = (30, 60, 85)
# Cortes absolutos del role_index para géneros chicos (< MIN_GENRE_SIZE)
ROLE_ABS_CUTS = (0.35, 0.55, 0.72)

# Pesos internos del score DSP (suman 1.0)
# "contrast" casi no pesa: castigaba a los temas que empujan parejo (cuanto más constante, menos contraste).
DSP_WEIGHTS = {
    "rms": 0.40,
    "onset": 0.30,
    "sub": 0.25,
    "contrast": 0.05,
}
# Rangos de normalización (lo, hi) -> 0..1. Ajustables a gusto.
DSP_RANGES = {
    "rms_db": (-26.0, -4.0),     # dBFS del RMS p95 (ancho para que no se sature con temas fuertes)
    "onset": (1.0, 8.0),         # p90 de la onset strength
    "sub": (0.25, 0.85),         # proporción de energía 20-120 Hz
    "contrast": (0.0, 0.8),      # (RMS drop / mediana RMS) - 1
}

# ─────────────────────────────────────────────────────────────
# CALIBRACIÓN POR GÉNERO / ESTRELLAS
# ─────────────────────────────────────────────────────────────
ANALYSIS_VERSION = 4             # subilo para invalidar la caché
MIN_GENRE_SIZE = 5               # géneros con menos pistas usan umbrales absolutos
# Cortes de percentil (1..100): <=c0 -> 1★, <=c1 -> 2★, <=c2 -> 3★, <=c3 -> 4★, resto 5★
STAR_PERCENTILE_CUTS = (20, 45, 75, 92)
# Cortes absolutos de explosividad (0..1) para géneros chicos o sin calibración
STAR_ABS_CUTS = (0.30, 0.45, 0.60, 0.75)
# Banda del ranking por género: las estrellas relativas (percentil) solo pueden alejarse
# STAR_REL_BAND estrellas de las que le tocarían por explosividad absoluta. Evita saltos
# absurdos (1★ vs 5★) entre temas con explosividades parecidas. Poné 4 para desactivarla.
STAR_REL_BAND = 1
# Piso absoluto: un tema con explosividad >= STAR_FLOOR_EXPL nunca baja de STAR_FLOOR_STARS,
# aunque su género sea muy parejo y el ranking relativo lo mande abajo.
STAR_FLOOR_EXPL = 0.60
STAR_FLOOR_STARS = 3

STAR_LABELS = {5: "EXPLOSIVO", 4: "POTENTE", 3: "SÓLIDO", 2: "SUAVE", 1: "CHILL"}

NO_GENRE_LABEL = "Sin Género"
GLOBAL_LABEL = "Todas las pistas"

# ─────────────────────────────────────────────────────────────
# TAGS / REKORDBOX
# ─────────────────────────────────────────────────────────────
# Bytes POPM estándar (mapeo Windows Media Player / Rekordbox)
POPM_BYTES = {0: 0, 1: 1, 2: 64, 3: 128, 4: 196, 5: 255}
POPM_EMAIL = "Windows Media Player 9 Series"

# Escala del campo Rating en master.db de Rekordbox.
# 1  -> guarda 0..5 estrellas directamente.
# 51 -> guarda 0,51,102,153,204,255 (si tu versión usa esa escala, cambialo acá).
RB_RATING_SCALE = 1

# ColorID de Rekordbox: 1 rosa, 2 rojo, 3 naranja, 4 amarillo, 5 verde, 6 aqua, 7 azul, 8 violeta
STAR_TO_COLOR_ID = {5: 2, 4: 3, 3: 4, 2: 6, 1: 7}


# ─────────────────────────────────────────────────────────────
# UTILIDADES COMPARTIDAS
# ─────────────────────────────────────────────────────────────
def clamp01(x):
    return max(0.0, min(1.0, float(x)))


def scale01(x, lo, hi):
    if hi == lo:
        return 0.0
    return clamp01((float(x) - lo) / (hi - lo))


def stars_from_percentile(pct):
    c = STAR_PERCENTILE_CUTS
    if pct <= c[0]:
        return 1
    if pct <= c[1]:
        return 2
    if pct <= c[2]:
        return 3
    if pct <= c[3]:
        return 4
    return 5


def stars_from_explosivity(x):
    c = STAR_ABS_CUTS
    if x < c[0]:
        return 1
    if x < c[1]:
        return 2
    if x < c[2]:
        return 3
    if x < c[3]:
        return 4
    return 5


def role_from_percentile(pct):
    c = ROLE_PERCENTILE_CUTS
    if pct <= c[0]:
        return 1
    if pct <= c[1]:
        return 2
    if pct <= c[2]:
        return 3
    return 4


def role_from_score(x):
    c = ROLE_ABS_CUTS
    if x < c[0]:
        return 1
    if x < c[1]:
        return 2
    if x < c[2]:
        return 3
    return 4


def fmt_mmss(seconds):
    seconds = max(0, int(round(float(seconds or 0))))
    return f"{seconds // 60}:{seconds % 60:02d}"


def fmt_eta(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def path_key(p):
    """Clave canónica para comparar rutas (Windows: sin mayúsculas, sin barras mixtas)."""
    p = os.path.normcase(os.path.normpath(os.path.abspath(str(p))))
    return unicodedata.normalize("NFC", p)


_BLOCK_RE = re.compile(r"^\s*\[🔥[^\]]*\]\s*(?:\[Drop[^\]]*\]\s*)?")


def build_comment_block(stars, pct, drop_start, ai_energy, role=0):
    stars = int(stars)
    label = STAR_LABELS.get(stars, "")
    ai_txt = "--" if ai_energy is None else f"{float(ai_energy):.2f}"
    role_txt = f" | {ROLE_LABELS[int(role)]}" if role and int(role) in ROLE_LABELS else ""
    return (f"[🔥 {int(pct)}% | {stars}★ {label}{role_txt}] "
            f"[Drop {fmt_mmss(drop_start)} · IA {ai_txt}]")


def strip_previous_block(text):
    """Quita el bloque que escribió una corrida anterior, dejando el comentario original."""
    return _BLOCK_RE.sub("", text or "", count=1).strip()


def merge_comment(block, previous):
    rest = strip_previous_block(previous)
    return f"{block} {rest}" if rest else block
