# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Controllo delle dipendenze, con il comando esatto per installare ciò che manca."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from . import engine, hypr, library
from .config import Settings

AUR_PKG = "linux-wallpaperengine-git"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    command: str | None = None   # comando da copiare se manca
    required: bool = True


def aur_helper() -> str | None:
    return next((h for h in ("yay", "paru") if shutil.which(h)), None)


def engine_install_command() -> str:
    helper = aur_helper()
    if helper:
        return f"{helper} -S {AUR_PKG}"
    # Nessun helper AUR: build manuale con makepkg
    return (f"sudo pacman -S --needed base-devel git && "
            f"git clone https://aur.archlinux.org/{AUR_PKG}.git && "
            f"cd {AUR_PKG} && makepkg -si")


def _has_font(*families: str) -> str | None:
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True,
                             timeout=5).stdout.casefold()
    except (OSError, subprocess.TimeoutExpired):
        return None
    # Confronto sul nome esatto della famiglia (o sue varianti "Inter Variable"...),
    # non come sottostringa: "Inter" non deve combaciare con "Winter" o "Interstate".
    names = {n.strip() for line in out.splitlines() for n in line.split(",")}
    return next((f for f in families
                 if any(n == f.casefold() or n.startswith(f.casefold() + " ") for n in names)), None)


def run_checks(s: Settings) -> list[Check]:
    checks: list[Check] = []

    binary = engine.engine_binary(s)
    checks.append(Check(
        "linux-wallpaperengine", bool(binary),
        f"Trovato: {binary}" if binary else "Il motore che riproduce i wallpaper non è installato.",
        None if binary else engine_install_command()))

    try:
        mons = hypr.monitors()
        checks.append(Check("Hyprland (IPC)", True, f"{len(mons)} monitor rilevati"))
    except (hypr.HyprlandError, OSError) as e:
        checks.append(Check("Hyprland (IPC)", False,
                            f"{e}. Avvia l'app da una sessione Hyprland.",
                            "sudo pacman -S --needed hyprland"))

    # Se stiamo girando, PyGObject e GTK4 ci sono per forza: lo mostriamo per completezza
    from gi.repository import Gtk
    checks.append(Check("GTK 4 + PyGObject", True,
                        f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"))

    assets = library.find_assets_dir(s.extra_library_paths)
    steam = "flatpak run com.valvesoftware.Steam" if library.is_flatpak_steam() else "steam"
    checks.append(Check(
        "Wallpaper Engine (assets)", bool(assets),
        str(assets) if assets else
        "Servono i file di Wallpaper Engine. In Steam: Proprietà → Compatibilità → "
        "forza Proton, poi installalo (non serve avviarlo).",
        None if assets else f"{steam} steam://install/431960"))

    from gi.repository import GdkPixbuf
    formats = {f.get_name() for f in GdkPixbuf.Pixbuf.get_formats()}
    checks.append(Check(
        "Anteprime WebP/GIF", {"gif", "webp"} <= formats,
        "Loader gdk-pixbuf presenti" if {"gif", "webp"} <= formats
        else "Alcune anteprime (.webp) non verranno mostrate.",
        None if "webp" in formats else "sudo pacman -S webp-pixbuf-loader", required=False))

    font = _has_font("SF Pro", "Inter")
    checks.append(Check(
        "Font Inter / SF Pro", bool(font),
        f"In uso: {font}" if font else "Facoltativo: senza, l'interfaccia usa il font di sistema.",
        None if font else "sudo pacman -S inter-font", required=False))
    return checks
