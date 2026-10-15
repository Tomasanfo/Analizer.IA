# -*- coding: utf-8 -*-
"""
audio_analyzer.py - Detector de Drop + IA CLAP (Zero-Shot) + Motor DSP (librosa).
Analizer IA local  •  by @tomasanfo
"""
import time

import librosa
import numpy as np
import soundfile as sf

import config as C


# ─────────────────────────────────────────────────────────────
# CARGA: 120 s centrales a 16 kHz
# ─────────────────────────────────────────────────────────────
def load_center(path):
    """Devuelve (y_16k, offset_en_segundos, duracion_total o None)."""
    dur = None
    try:
        dur = float(sf.info(path).duration)
    except Exception:
        try:
            dur = float(librosa.get_duration(path=path))
        except Exception:
            dur = None

    offset = 0.0
    if dur and dur > C.CENTER_SECONDS:
        offset = (dur - C.CENTER_SECONDS) / 2.0

    y, _ = librosa.load(path, sr=C.SR_ANALYSIS, mono=True,
                        offset=offset, duration=C.CENTER_SECONDS)
    return y, offset, dur


# ─────────────────────────────────────────────────────────────
# DETECCIÓN DINÁMICA DEL DROP (ventana de 30 s más intensa)
# ─────────────────────────────────────────────────────────────
def _norm_p95(x):
    return x / (np.percentile(x, 95) + 1e-9)


def find_drop(y, sr=C.SR_ANALYSIS):
    """Devuelve (start_sample, end_sample) de la ventana más intensa."""
    hop = C.HOP_LENGTH
    win_samples = int(C.DROP_WINDOW_SECONDS * sr)
    if len(y) <= win_samples:
        return 0, len(y)

    rms = librosa.feature.rms(y=y, frame_length=C.N_FFT, hop_length=hop)[0]
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    S = np.abs(librosa.stft(y, n_fft=C.N_FFT, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=C.N_FFT)
    low = S[(freqs >= 20) & (freqs <= 150)].sum(axis=0)

    n = min(len(rms), len(onset), len(low))
    intensity = (0.50 * _norm_p95(rms[:n])
                 + 0.30 * _norm_p95(low[:n])
                 + 0.20 * _norm_p95(onset[:n]))

    win_frames = int(C.DROP_WINDOW_SECONDS * sr / hop)
    if n <= win_frames:
        return 0, len(y)

    csum = np.cumsum(np.insert(intensity, 0, 0.0))
    means = (csum[win_frames:] - csum[:-win_frames]) / win_frames
    start_frame = int(np.argmax(means))
    start = start_frame * hop
    end = min(len(y), start + win_samples)
    return start, end


# ─────────────────────────────────────────────────────────────
# MOTOR DSP (librosa) sobre el Drop
# ─────────────────────────────────────────────────────────────
def dsp_features(y_drop, y_full, sr=C.SR_ANALYSIS):
    hop = C.HOP_LENGTH

    rms_drop = librosa.feature.rms(y=y_drop, frame_length=C.N_FFT, hop_length=hop)[0]
    rms_full = librosa.feature.rms(y=y_full, frame_length=C.N_FFT, hop_length=hop)[0]
    drop_rms_p95 = float(np.percentile(rms_drop, 95))
    rms_db = 20.0 * np.log10(drop_rms_p95 + 1e-9)

    onset = librosa.onset.onset_strength(y=y_drop, sr=sr, hop_length=hop)
    onset_punch = float(np.percentile(onset, 90))

    S = np.abs(librosa.stft(y_drop, n_fft=C.N_FFT, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=sr, n_fft=C.N_FFT)
    sub_mask = (freqs >= 20) & (freqs <= 120)
    sub_bass_weight = float(S[sub_mask].sum() / (S.sum() + 1e-12))

    med_full = float(np.median(rms_full))
    ratio = float(np.mean(rms_drop) / (med_full + 1e-9))
    drop_contrast = max(0.0, ratio - 1.0)

    R = C.DSP_RANGES
    W = C.DSP_WEIGHTS
    dsp_score = (
        W["rms"] * C.scale01(rms_db, *R["rms_db"])
        + W["onset"] * C.scale01(onset_punch, *R["onset"])
        + W["sub"] * C.scale01(sub_bass_weight, *R["sub"])
        + W["contrast"] * C.scale01(drop_contrast, *R["contrast"])
    )
    return {
        "drop_rms_p95": drop_rms_p95,
        "onset_punch": onset_punch,
        "sub_bass_weight": sub_bass_weight,
        "drop_contrast": drop_contrast,
        "dsp_score": float(C.clamp01(dsp_score)),
    }


# ─────────────────────────────────────────────────────────────
# IA LOCAL: CLAP Zero-Shot
# ─────────────────────────────────────────────────────────────
def make_clap_chunks(y_drop):
    """Parte el Drop en bloques de 10 s (lo que procesa CLAP unfused) y pasa a 48 kHz."""
    n = int(C.CLAP_CHUNK_SECONDS * C.SR_ANALYSIS)
    if len(y_drop) < n:
        chunks = [np.pad(y_drop, (0, n - len(y_drop)))]
    else:
        k = len(y_drop) // n
        chunks = [y_drop[i * n:(i + 1) * n] for i in range(k)]
    return [librosa.resample(c, orig_sr=C.SR_ANALYSIS, target_sr=C.SR_CLAP).astype(np.float32)
            for c in chunks]


class ClapEngine:
    """Carga perezosa de laion/clap-htsat-unfused (CPU o GPU)."""

    def __init__(self, log=None, on_ready=None):
        self.log = log or (lambda m: None)
        self.on_ready = on_ready
        self.model = None
        self.processor = None
        self.torch = None
        self.device = "cpu"
        self.energy_prompts = C.PROMPTS_HIGH + C.PROMPTS_MID + C.PROMPTS_LOW
        self.genre_prompts = [C.GENRE_PROMPT_TEMPLATE.format(g) for g in C.GENRE_CANDIDATES]

    def ensure_loaded(self):
        if self.model is not None:
            return
        import torch
        from transformers import ClapModel, ClapProcessor

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        t0 = time.time()
        self.log(f"🧠 Cargando CLAP ({C.CLAP_MODEL_ID}) en {self.device.upper()}...")
        try:
            self.processor = ClapProcessor.from_pretrained(C.CLAP_MODEL_ID, local_files_only=True)
            self.model = ClapModel.from_pretrained(C.CLAP_MODEL_ID, local_files_only=True)
        except Exception:
            self.log("⬇ Primera vez: descargando el modelo (~600 MB, se hace una sola vez)...")
            self.processor = ClapProcessor.from_pretrained(C.CLAP_MODEL_ID)
            self.model = ClapModel.from_pretrained(C.CLAP_MODEL_ID)
        self.model.to(self.device)
        self.model.eval()
        self.log(f"✔ CLAP listo en {time.time() - t0:.1f}s")
        if self.on_ready:
            self.on_ready()

    def _logits(self, chunks, prompts):
        # transformers nuevo usa `audio=`; las versiones viejas usan `audios=`.
        try:
            inputs = self.processor(text=prompts, audio=chunks, sampling_rate=C.SR_CLAP,
                                    return_tensors="pt", padding=True)
        except (TypeError, ValueError):
            inputs = self.processor(text=prompts, audios=chunks, sampling_rate=C.SR_CLAP,
                                    return_tensors="pt", padding=True)
        inputs = {k: v.to(self.device) for k, v in inputs.items()}
        with self.torch.no_grad():
            out = self.model(**inputs)
        return out.logits_per_audio  # (n_chunks, n_prompts)

    def energy_score(self, chunks):
        """ai_energy_score 0..1: masa de probabilidad en prompts altos (+0.5 medios)."""
        logits = self._logits(chunks, self.energy_prompts)
        probs = self.torch.softmax(logits, dim=-1).mean(dim=0).cpu().numpy()
        nh, nm = len(C.PROMPTS_HIGH), len(C.PROMPTS_MID)
        p_high = float(probs[:nh].sum())
        p_mid = float(probs[nh:nh + nm].sum())
        return float(C.clamp01(p_high + 0.5 * p_mid))

    def detect_genre(self, chunks):
        logits = self._logits(chunks, self.genre_prompts)
        probs = self.torch.softmax(logits, dim=-1).mean(dim=0).cpu().numpy()
        idx = int(np.argmax(probs))
        return C.GENRE_CANDIDATES[idx], float(probs[idx])


# ─────────────────────────────────────────────────────────────
# PIPELINE POR PISTA
# ─────────────────────────────────────────────────────────────
def analyze_track(path, clap, use_ai=True, want_genre=False):
    """
    Devuelve dict con: drop_start (seg absolutos), ai_energy (o None), genre_ai, genre_ai_conf,
    métricas DSP, dsp_score, explosivity (0..1), used_clap.
    """
    y, offset, _dur = load_center(path)
    if len(y) < C.MIN_AUDIO_SECONDS * C.SR_ANALYSIS:
        raise ValueError("audio demasiado corto o ilegible")

    start, end = find_drop(y)
    y_drop = y[start:end]
    drop_start = offset + start / C.SR_ANALYSIS

    feats = dsp_features(y_drop, y)

    ai_energy = None
    genre_ai, genre_conf = "", None
    if use_ai or want_genre:
        chunks = make_clap_chunks(y_drop)
        clap.ensure_loaded()
        if use_ai:
            ai_energy = clap.energy_score(chunks)
        if want_genre:
            genre_ai, genre_conf = clap.detect_genre(chunks)

    if ai_energy is not None:
        explosivity = C.FUSION_WEIGHT_AI * ai_energy + C.FUSION_WEIGHT_DSP * feats["dsp_score"]
    else:
        explosivity = feats["dsp_score"]

    result = dict(feats)
    result.update({
        "drop_start": float(drop_start),
        "ai_energy": ai_energy,
        "used_clap": 1 if ai_energy is not None else 0,
        "genre_ai": genre_ai,
        "genre_ai_conf": genre_conf,
        "explosivity": float(C.clamp01(explosivity)),
    })
    return result
