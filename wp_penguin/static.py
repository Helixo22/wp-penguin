# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sfondo statico abbinato al wallpaper animato.

Quando avvii un wallpaper animato, wp-penguin ne ricava un'immagine e la
imposta anche come sfondo statico con il gestore di sfondi già presente
(Ambxst, awww/swww o hyprpaper). linux-wallpaperengine disegna sul layer
"bottom", sopra lo sfondo statico (layer "background"): finché l'animazione
gira non si vede, ma se crasha o viene fermata sotto resta l'immagine giusta
invece di uno sfondo scollegato. Con Ambxst anche i colori del tema (matugen)
seguono il wallpaper animato.

L'immagine viene presa, in ordine di preferenza:
  1. dallo screenshot del motore (--screenshot), a piena risoluzione;
  2. da un fotogramma del video (ffmpeg), per i wallpaper video;
  3. dall'anteprima del Workshop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import library

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wp-penguin/static"
# Stato separato da config.json: lo scrive un thread in background e non deve
# essere sovrascritto quando la GUI salva le sue impostazioni.
STATE_FILE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "wp-penguin/static.json"

SCREENSHOT_TIMEOUT = 20  # secondi di attesa per lo screenshot del motore


def screenshot_path(monitor: str, workshop_id: str) -> Path:
    return CACHE_DIR / f"{workshop_id}-{monitor}.png"


def _running(name: str) -> bool:
    return subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0


def backend() -> str | None:
    """Il gestore di sfondi statici disponibile, in ordine di preferenza."""
    if shutil.which("ambxst") and _running("ambxst"):
        return "ambxst"
    for daemon, cli in (("awww-daemon", "awww"), ("swww-daemon", "swww")):
        if _running(daemon) and shutil.which(cli):
            return cli
    if _running("hyprpaper"):
        return "hyprpaper"
    return None


def set_static(monitor: str, image: Path) -> str | None:
    """Imposta image come sfondo statico di monitor. Ritorna il backend usato."""
    b = backend()
    if b == "ambxst":
        cmds = [["ambxst", "wallpaper", str(image), "-monitor", monitor]]
    elif b in ("awww", "swww"):
        cmds = [[b, "img", "-o", monitor, str(image)]]
    elif b == "hyprpaper":
        cmds = [["hyprctl", "hyprpaper", "preload", str(image)],
                ["hyprctl", "hyprpaper", "wallpaper", f"{monitor},{image}"]]
    else:
        return None
    for cmd in cmds:
        subprocess.run(cmd, capture_output=True, timeout=30)
    return b


def _wait_screenshot(path: Path, started: float) -> bool:
    deadline = time.time() + SCREENSHOT_TIMEOUT
    while time.time() < deadline:
        try:
            st = path.stat()
            # Nuovo (scritto dopo l'avvio) e non vuoto; aspettiamo che la scrittura finisca
            if st.st_mtime >= started - 1 and st.st_size > 0:
                time.sleep(0.5)
                if path.stat().st_size == st.st_size:
                    return True
        except FileNotFoundError:
            pass
        time.sleep(0.5)
    return False


def _fallback_image(wp: library.Wallpaper, out: Path) -> bool:
    """Fotogramma del video o anteprima, convertiti in PNG con ffmpeg."""
    sources = []
    if wp.type == "video" and wp.main_file and (wp.path / wp.main_file).is_file():
        sources.append(["-ss", "1", "-i", str(wp.path / wp.main_file)])
    if wp.preview:
        sources.append(["-i", str(wp.preview)])
    for src in sources:
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *src, "-frames:v", "1",
                            "-update", "1", str(out)], capture_output=True, timeout=60)
        if r.returncode == 0 and out.is_file() and out.stat().st_size > 0:
            return True
    return False


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


def apply(monitor: str, wp: library.Wallpaper, started: float) -> None:
    """Da eseguire in un thread dopo l'avvio del wallpaper animato."""
    state = _load_state()
    image = screenshot_path(monitor, wp.workshop_id)
    # Sfondo statico già impostato su questo wallpaper (es. ripristino al login):
    # non lo reimpostiamo, con Ambxst eviterebbe di rigenerare i colori ogni volta.
    if state.get(monitor) == wp.workshop_id and image.is_file():
        return

    if not _wait_screenshot(image, started) and not image.is_file():
        _fallback_image(wp, image)
    if not image.is_file():
        print(f"[wp-penguin] nessuna immagine statica per {wp.workshop_id}")
        return
    used = set_static(monitor, image)
    if used:
        state[monitor] = wp.workshop_id
        _save_state(state)
        print(f"[wp-penguin] sfondo statico di {monitor} impostato con {used}")
