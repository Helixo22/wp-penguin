# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scoperta delle librerie Steam e lettura dei wallpaper del Workshop."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

WE_APPID = "431960"

# Radici Steam più comuni: nativa, symlink legacy e Flatpak
STEAM_ROOTS = [
    Path.home() / ".local/share/Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
]

PREVIEW_CANDIDATES = ("preview.jpg", "preview.png", "preview.gif", "preview.webp")


@dataclass
class Wallpaper:
    workshop_id: str
    title: str
    type: str            # "scene" | "video" | "web" | "application" | ...
    path: Path               # cartella da passare a --bg
    preview: Path | None
    # Solo per i "preset": proprietà da sovrascrivere con --set-property
    properties: dict[str, str] = field(default_factory=dict)
    is_preset: bool = False
    # File principale dichiarato in project.json (es. il .mp4 dei video)
    main_file: str | None = None
    # Quando Steam l'ha scaricato/aggiornato (mtime della cartella del Workshop)
    installed: float = 0.0
    # Quando è arrivato come "nuovo" (0 = c'era già prima della configurazione
    # di wp-penguin). Vedi _mark_new().
    added: float = 0.0

    @property
    def supported(self) -> bool:
        # Le wallpaper "application" sono eseguibili Windows: non supportate.
        return self.type in ("scene", "video", "web")


def library_folders(extra: list[str] | None = None) -> list[Path]:
    """Ritorna tutte le librerie Steam (cartelle che contengono steamapps/).

    Oltre alle radici note legge libraryfolders.vdf, così vengono trovate
    anche le librerie su altri dischi.
    """
    found: list[Path] = []

    def add(p: Path) -> None:
        try:
            p = p.resolve()
        except OSError:
            return
        if p.name == "steamapps":  # accettiamo anche .../steamapps
            p = p.parent
        if (p / "steamapps").is_dir() and p not in found:
            found.append(p)

    for root in STEAM_ROOTS:
        add(root)
        vdf = root / "steamapps/libraryfolders.vdf"
        try:
            text = vdf.read_text(errors="replace")
        except OSError:
            continue
        # Parsing minimale del VDF: ci servono solo le righe "path" "<dir>"
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            add(Path(m.group(1).replace("\\\\", "\\")))
    for p in extra or []:
        add(Path(p).expanduser())
    return found


def steam_roots() -> list[Path]:
    """Installazioni di Steam trovate (nativa e/o Flatpak)."""
    roots = []
    for r in STEAM_ROOTS:
        try:
            rr = r.resolve()
        except OSError:
            continue
        if (rr / "steamapps").is_dir() and rr not in roots:
            roots.append(rr)
    return roots


def is_flatpak_steam() -> bool:
    return any(".var/app/com.valvesoftware.Steam" in str(r) for r in steam_roots())


def workshop_dirs(extra: list[str] | None = None) -> list[Path]:
    dirs = []
    for lib in library_folders(extra):
        d = lib / "steamapps/workshop/content" / WE_APPID
        if d.is_dir():
            dirs.append(d)
    # Cartelle Workshop indicate direttamente (es. scelte a mano nel wizard)
    for p in extra or []:
        d = Path(p).expanduser()
        if d.name == WE_APPID and d.is_dir() and d.resolve() not in dirs:
            dirs.append(d.resolve())
    return dirs


def find_assets_dir(extra: list[str] | None = None) -> Path | None:
    """Cartella assets/ di Wallpaper Engine, necessaria per le scene.

    linux-wallpaperengine la cerca da solo nei percorsi standard, ma con Steam
    Flatpak o librerie secondarie conviene passarla con --assets-dir.
    """
    for lib in library_folders(extra):
        d = lib / "steamapps/common/wallpaper_engine/assets"
        if d.is_dir():
            return d
    return None


def _load_json(folder: Path) -> dict | None:
    try:
        # utf-8-sig: molti project.json hanno il BOM
        data = json.loads((folder / "project.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _prop_value(v) -> str | None:
    """Converte un valore di preset nel formato atteso da --set-property."""
    if v is None:
        return None
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _read_project(folder: Path) -> Wallpaper | None:
    data = _load_json(folder)
    if data is None:
        return None

    preview = None
    name = data.get("preview")
    if isinstance(name, str) and (folder / name).is_file():
        preview = folder / name
    else:
        preview = next((folder / c for c in PREVIEW_CANDIDATES if (folder / c).is_file()), None)

    wp = Wallpaper(
        workshop_id=folder.name,
        title=str(data.get("title") or folder.name).strip(),
        # Il tipo compare sia come "scene" che "Scene": normalizziamo
        type=str(data.get("type") or "unknown").lower(),
        path=folder,
        preview=preview,
        main_file=data.get("file") if isinstance(data.get("file"), str) else None,
        installed=folder.stat().st_mtime,
    )

    # Preset: nessun "type", ma "dependency" (ID del wallpaper base, che Steam
    # scarica nella stessa cartella) + "preset" (valori delle proprietà).
    dep = data.get("dependency")
    if "type" not in data and dep:
        wp.is_preset = True
        base_dir = folder.parent / str(dep)
        base = _load_json(base_dir)
        if base is None:
            wp.type = "preset (base mancante)"
            return wp
        wp.type = str(base.get("type") or "unknown").lower()
        wp.path = base_dir
        wp.main_file = base.get("file") if isinstance(base.get("file"), str) else None
        # Passiamo solo le proprietà che il wallpaper base dichiara davvero
        declared = (base.get("general") or {}).get("properties") or {}
        for key, val in (data.get("preset") or {}).items():
            sval = _prop_value(val)
            if key in declared and sval is not None:
                wp.properties[key] = sval
    return wp


def scan(extra: list[str] | None = None) -> list[Wallpaper]:
    """Legge tutti i wallpaper, ordinati per titolo."""
    result: dict[str, Wallpaper] = {}
    for wdir in workshop_dirs(extra):
        for folder in wdir.iterdir():
            if folder.is_dir() and folder.name not in result:
                wp = _read_project(folder)
                if wp:
                    result[folder.name] = wp
    _mark_new(result)
    return sorted(result.values(), key=lambda w: w.title.casefold())


# ------------------------------------------------------------ «Nuovi»
#
# Steam non registra quando ti sei iscritto a un wallpaper e, la prima volta,
# scarica tutto l'insieme in un colpo: la data delle cartelle da sola non basta.
# Teniamo quindi un registro di quando wp-penguin vede ogni wallpaper: ciò che
# c'era già prima della configurazione dell'app è "vecchio", ciò che arriva
# dopo è "nuovo", con la data di arrivo.

SEEN_FILE = (Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
             / "wp-penguin/seen.json")


def _birth_time(path: Path) -> float:
    """Data di creazione del file (statx via coreutils), altrimenti mtime."""
    try:
        out = subprocess.run(["stat", "-c", "%W", str(path)], capture_output=True,
                             text=True, timeout=5).stdout.strip()
        if out.isdigit() and int(out) > 0:
            return float(out)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        return path.stat().st_mtime
    except OSError:
        return time.time()


def _mark_new(wallpapers: dict[str, Wallpaper]) -> None:
    from .config import CONFIG_DIR
    try:
        state = json.loads(SEEN_FILE.read_text())
    except (OSError, ValueError):
        # Primo avvio di questa funzione: il punto di partenza è la creazione
        # della configurazione di wp-penguin (o adesso, se non esiste ancora)
        state = {"baseline": _birth_time(CONFIG_DIR) if CONFIG_DIR.exists() else time.time(),
                 "seen": {}}
    baseline, seen = state["baseline"], state["seen"]
    changed = False
    for wid, wp in wallpapers.items():
        if wid not in seen:
            seen[wid] = wp.installed if wp.installed > baseline else 0
            changed = True
        wp.added = seen[wid]
    if changed:
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = SEEN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(SEEN_FILE)


def by_id(workshop_id: str, extra: list[str] | None = None) -> Wallpaper | None:
    for wdir in workshop_dirs(extra):
        folder = wdir / workshop_id
        if folder.is_dir():
            return _read_project(folder)
    return None
