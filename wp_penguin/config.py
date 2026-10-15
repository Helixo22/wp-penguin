# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lettura/scrittura della configurazione persistente.

Il file vive in ~/.config/wp-penguin/config.json ed è condiviso tra GUI e
daemon: la GUI lo scrive a ogni modifica, il daemon lo rilegge a ogni evento
(costa pochissimo) così non serve alcun canale IPC tra i due processi.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wp-penguin"
CONFIG_FILE = CONFIG_DIR / "config.json"

# Modalità di scaling accettate da linux-wallpaperengine (--scaling)
SCALING_MODES = ["default", "stretch", "fit", "fill"]


@dataclass
class Settings:
    # Eseguibile di linux-wallpaperengine (nome nel PATH o percorso assoluto)
    binary: str = "linux-wallpaperengine"
    fps: int = 30
    volume: int = 50                 # 0-100, passato a --volume
    muted: bool = False              # --silent
    pause_on_fullscreen: bool = True # gestito dal daemon via IPC di Hyprland
    # Pausa appena sul monitor c'è una finestra qualsiasi (anche flottante)
    pause_on_windows: bool = False
    # Imposta anche uno sfondo statico (un fotogramma del wallpaper animato):
    # resta visibile sotto quello animato se questo crasha o viene fermato
    static_wallpaper: bool = True
    # Riavvia da solo il wallpaper animato se il processo termina inaspettatamente
    autorestart: bool = True
    scaling: str = "default"
    disable_mouse: bool = False
    # Ferma hyprpaper / swww / awww prima di avviare un wallpaper. Di solito non
    # serve: linux-wallpaperengine disegna sul layer "bottom", sopra di loro.
    kill_conflicting: bool = False
    # Cartelle scelte a mano (oltre a quelle trovate in automatico): possono
    # essere una libreria Steam, la sua steamapps/ o direttamente .../431960
    extra_library_paths: list[str] = field(default_factory=list)
    # Monitor su cui NON usare wallpaper animati (i nuovi sono attivi di default)
    disabled_monitors: list[str] = field(default_factory=list)
    # Monitor fermati a mano con «Ferma»: il watchdog non li riavvia
    stopped_monitors: list[str] = field(default_factory=list)
    # "dark" (predefinito) | "light" | "auto" (segue il tema di sistema)
    color_scheme: str = "dark"
    # Workshop: nasconde i wallpaper con classificazione "Mature"
    workshop_hide_mature: bool = True
    # Trasparenza delle finestre dell'app in %: 0 = opache, max 70
    transparency: int = 20
    # False finché la procedura guidata non è stata completata
    first_run_done: bool = False
    # Assegnazioni correnti: nome monitor (es. "DP-3") -> workshop ID
    monitors: dict[str, str] = field(default_factory=dict)
    # Ultimo monitor selezionato nella GUI
    last_monitor: str = ""


def load() -> Settings:
    """Carica la configurazione; valori mancanti o corrotti -> default."""
    s = Settings()
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return s
    for key, default in asdict(s).items():
        if key in data and isinstance(data[key], type(default)):
            setattr(s, key, data[key])
    s.fps = max(1, min(s.fps, 240))
    s.volume = max(0, min(s.volume, 100))
    if s.scaling not in SCALING_MODES:
        s.scaling = "default"
    s.transparency = max(0, min(s.transparency, 70))
    if s.color_scheme not in ("auto", "light", "dark"):
        s.color_scheme = "dark"
    return s


def exists() -> bool:
    return CONFIG_FILE.is_file()


def save(s: Settings) -> None:
    """Scrittura atomica (file temporaneo + rename) per non lasciare mai
    al daemon un JSON scritto a metà."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s), indent=2))
    tmp.replace(CONFIG_FILE)
