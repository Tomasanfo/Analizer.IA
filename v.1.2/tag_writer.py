# -*- coding: utf-8 -*-
"""
tag_writer.py - Lector de género original e inyector de tags (MP3/FLAC/WAV/AIFF/M4A) con Mutagen.
Analizer IA local  •  by @tomasanfo

REGLA SAGRADA: el tag Genre original JAMÁS se sobrescribe. Solo se escribe el género de la IA
si el campo está completamente vacío en el archivo.
"""
import os
import re

from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, ID3NoHeaderError, POPM, TCON, TIT1
from mutagen.mp4 import MP4, MP4FreeForm
from mutagen.wave import WAVE

import config as C


def _ext(path):
    return os.path.splitext(path)[1].lower()


def _id3_text(tags, key):
    if tags is None:
        return ""
    frame = tags.get(key)
    if frame is None:
        return ""
    try:
        return " ".join(str(t).strip() for t in frame.text if str(t).strip())
    except Exception:
        return ""


def _id3_genre(tags):
    if tags is None:
        return ""
    frame = tags.get("TCON")
    if frame is None:
        return ""
    try:
        values = list(frame.genres)
    except Exception:
        values = list(getattr(frame, "text", []))
    return ", ".join(str(v).strip() for v in values if str(v).strip())


def _vorbis_first(audio, key):
    try:
        vals = audio.get(key) or []
    except Exception:
        vals = []
    for v in vals:
        if str(v).strip():
            return str(v).strip()
    return ""


# ─────────────────────────────────────────────────────────────
# LECTURA
# ─────────────────────────────────────────────────────────────
def read_basic_info(path):
    """Devuelve {'title','artist','genre'} (strings, vacío si no hay)."""
    info = {"title": "", "artist": "", "genre": ""}
    ext = _ext(path)
    try:
        if ext == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                tags = None
            info["title"] = _id3_text(tags, "TIT2")
            info["artist"] = _id3_text(tags, "TPE1")
            info["genre"] = _id3_genre(tags)
        elif ext == ".wav":
            tags = WAVE(path).tags
            info["title"] = _id3_text(tags, "TIT2")
            info["artist"] = _id3_text(tags, "TPE1")
            info["genre"] = _id3_genre(tags)
        elif ext in (".aiff", ".aif"):
            tags = AIFF(path).tags
            info["title"] = _id3_text(tags, "TIT2")
            info["artist"] = _id3_text(tags, "TPE1")
            info["genre"] = _id3_genre(tags)
        elif ext == ".flac":
            audio = FLAC(path)
            info["title"] = _vorbis_first(audio, "title")
            info["artist"] = _vorbis_first(audio, "artist")
            info["genre"] = _vorbis_first(audio, "genre")
        elif ext == ".m4a":
            tags = MP4(path).tags or {}
            def _m4(k):
                v = tags.get(k) or [""]
                return str(v[0]).strip()
            info["title"] = _m4("\xa9nam")
            info["artist"] = _m4("\xa9ART")
            info["genre"] = _m4("\xa9gen")
    except Exception:
        pass
    return info


# ─────────────────────────────────────────────────────────────
# ESCRITURA
# ─────────────────────────────────────────────────────────────
def _write_id3_frames(tags, stars, block, grouping, genre_to_write):
    """Escribe POPM + Comments + Grouping (+ Genre solo si está vacío). Devuelve genre_written."""
    tags.delall("POPM")
    tags.add(POPM(email=C.POPM_EMAIL, rating=C.POPM_BYTES[stars], count=0))

    old = []
    for frame in list(tags.getall("COMM")):
        if frame.desc == "":
            old.extend(str(t) for t in frame.text)
            del tags[frame.HashKey]
    text = C.merge_comment(block, "\n".join(old))
    tags.add(COMM(encoding=1, lang="eng", desc="", text=[text]))

    tags.setall("TIT1", [TIT1(encoding=1, text=[grouping])])

    genre_written = False
    if genre_to_write and not _id3_genre(tags):
        tags.setall("TCON", [TCON(encoding=1, text=[genre_to_write])])
        genre_written = True
    return genre_written


def _save_with_v23(obj_or_tags, *args):
    """Guarda en ID3v2.3 (máxima compatibilidad con Rekordbox); cae a default si no se puede."""
    try:
        obj_or_tags.save(*args, v2_version=3)
    except TypeError:
        obj_or_tags.save(*args)


def write_analysis_tags(path, stars, percentile, drop_start, ai_energy, genre_to_write=""):
    """
    Inyecta estrellas, comentario y grouping en el archivo físico.
    Devuelve {'genre_written': bool}.
    """
    stars = max(1, min(5, int(stars)))
    block = C.build_comment_block(stars, percentile, drop_start, ai_energy)
    grouping = f"{stars}★ {C.STAR_LABELS[stars]}"
    ext = _ext(path)
    genre_written = False

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        genre_written = _write_id3_frames(tags, stars, block, grouping, genre_to_write)
        _save_with_v23(tags, path)

    elif ext in (".wav", ".aiff", ".aif"):
        audio = WAVE(path) if ext == ".wav" else AIFF(path)
        if audio.tags is None:
            audio.add_tags()
        genre_written = _write_id3_frames(audio.tags, stars, block, grouping, genre_to_write)
        _save_with_v23(audio)

    elif ext == ".flac":
        audio = FLAC(path)
        audio["rating"] = [str(stars * 20)]
        audio["fmps_rating"] = [f"{stars / 5.0:.2f}"]
        previous = _vorbis_first(audio, "comment")
        audio["comment"] = [C.merge_comment(block, previous)]
        audio["grouping"] = [grouping]
        if genre_to_write and not _vorbis_first(audio, "genre"):
            audio["genre"] = [genre_to_write]
            genre_written = True
        audio.save()

    elif ext == ".m4a":
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        tags = audio.tags
        previous = ""
        if tags.get("\xa9cmt"):
            previous = str(tags["\xa9cmt"][0])
        tags["\xa9cmt"] = [C.merge_comment(block, previous)]
        tags["\xa9grp"] = [grouping]
        tags["----:com.apple.iTunes:FMPS_Rating"] = [
            MP4FreeForm(f"{stars / 5.0:.2f}".encode("utf-8"))
        ]
        current_genre = str(tags["\xa9gen"][0]).strip() if tags.get("\xa9gen") else ""
        if genre_to_write and not current_genre:
            tags["\xa9gen"] = [genre_to_write]
            genre_written = True
        audio.save()

    else:
        raise ValueError(f"Formato no soportado: {ext}")

    return {"genre_written": genre_written}


# ─────────────────────────────────────────────────────────────
# LIMPIEZA (🧹 Limpiar Estrellas)
# Nunca toca Genre, Title, Artist ni BPM.
# ─────────────────────────────────────────────────────────────
_OUR_GROUPING_RE = re.compile(r"^\s*[1-5]★\s")


def _is_our_grouping(text):
    """Solo borramos el Grouping si lo escribió esta app (ej. '5★ EXPLOSIVO')."""
    return bool(_OUR_GROUPING_RE.match(text or ""))


def _clean_id3_frames(tags):
    changed = False
    if tags.getall("POPM"):
        tags.delall("POPM")
        changed = True

    for frame in list(tags.getall("COMM")):
        old = [str(t) for t in frame.text]
        new = [C.strip_previous_block(t) for t in old]
        if new != old:
            changed = True
            if not any(t.strip() for t in new):
                del tags[frame.HashKey]
            else:
                frame.text = new

    if _is_our_grouping(_id3_text(tags, "TIT1")):
        tags.delall("TIT1")
        changed = True
    return changed


def clear_energy_tags(path):
    """
    Quita del archivo: POPM/RATING, el prefijo [🔥 ...] del comentario (respeta el comentario
    original del DJ) y el Grouping escrito por esta app. Devuelve {'changed': bool}.
    """
    ext = _ext(path)
    changed = False

    if ext == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return {"changed": False}
        changed = _clean_id3_frames(tags)
        if changed:
            _save_with_v23(tags, path)

    elif ext in (".wav", ".aiff", ".aif"):
        audio = WAVE(path) if ext == ".wav" else AIFF(path)
        if audio.tags is None:
            return {"changed": False}
        changed = _clean_id3_frames(audio.tags)
        if changed:
            _save_with_v23(audio)

    elif ext == ".flac":
        audio = FLAC(path)
        for key in ("rating", "fmps_rating"):
            if key in audio:
                del audio[key]
                changed = True
        previous = _vorbis_first(audio, "comment")
        if previous:
            cleaned = C.strip_previous_block(previous)
            if cleaned != previous:
                changed = True
                if cleaned:
                    audio["comment"] = [cleaned]
                else:
                    del audio["comment"]
        if "grouping" in audio and _is_our_grouping(_vorbis_first(audio, "grouping")):
            del audio["grouping"]
            changed = True
        if changed:
            audio.save()

    elif ext == ".m4a":
        audio = MP4(path)
        tags = audio.tags
        if tags is None:
            return {"changed": False}
        key = "----:com.apple.iTunes:FMPS_Rating"
        if key in tags:
            del tags[key]
            changed = True
        if tags.get("\xa9cmt"):
            previous = str(tags["\xa9cmt"][0])
            cleaned = C.strip_previous_block(previous)
            if cleaned != previous:
                changed = True
                if cleaned:
                    tags["\xa9cmt"] = [cleaned]
                else:
                    del tags["\xa9cmt"]
        if tags.get("\xa9grp") and _is_our_grouping(str(tags["\xa9grp"][0])):
            del tags["\xa9grp"]
            changed = True
        if changed:
            audio.save()

    else:
        raise ValueError(f"Formato no soportado: {ext}")

    return {"changed": changed}
