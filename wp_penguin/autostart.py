# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aggiunta/rimozione dell'avvio automatico nella config di Hyprland.

Gestisce sia la config classica (hyprland.conf, `exec-once`) sia quella Lua
(hyprland.lua, `hl.on("hyprland.start", ...)`). Il blocco aggiunto è
delimitato da marcatori, così si riconosce e si rimuove senza toccare il resto.
Prima di ogni modifica viene creato un backup con data e ora.
"""

from __future__ import annotations

import os
import shlex
import shutil
import time
from pathlib import Path

BEGIN = ">>> wp-penguin autostart >>>"
END = "<<< wp-penguin autostart <<<"


def hypr_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "hypr"


def config_file() -> Path:
    """La config attiva: hyprland.lua se esiste (Hyprland ≥ 0.5x), altrimenti .conf."""
    lua = hypr_dir() / "hyprland.lua"
    return lua if lua.is_file() else hypr_dir() / "hyprland.conf"


def launcher() -> str:
    """Comando da avviare: preferisce ~/.local/bin/wp-penguin se esiste."""
    local = Path.home() / ".local/bin/wp-penguin"
    if local.exists():
        return str(local)
    return str(Path(__file__).resolve().parent.parent / "wp-penguin")


def _block(path: Path) -> str:
    cmd = f"{shlex.quote(launcher())} --daemon"
    if path.suffix == ".lua":
        # [[...]] = stringa Lua letterale: niente problemi di escape
        return (f"-- {BEGIN}\n"
                f"-- Aggiunto da wp-penguin: ripristina il wallpaper animato all'avvio\n"
                f'hl.on("hyprland.start", function ()\n'
                f"    hl.exec_cmd([[{cmd}]])\n"
                f"end)\n"
                f"-- {END}\n")
    return (f"# {BEGIN}\n"
            f"# Aggiunto da wp-penguin: ripristina il wallpaper animato all'avvio\n"
            f"exec-once = {cmd}\n"
            f"# {END}\n")


def is_enabled(path: Path | None = None) -> bool:
    path = path or config_file()
    try:
        return BEGIN in path.read_text()
    except OSError:
        return False


def backup(path: Path) -> Path:
    stem = f"{path.name}.bak-wp-penguin-{time.strftime('%Y%m%d-%H%M%S')}"
    dst, n = path.with_name(stem), 1
    while dst.exists():  # due modifiche nello stesso secondo
        dst, n = path.with_name(f"{stem}-{n}"), n + 1
    shutil.copy2(path, dst)
    return dst


def _strip_block(text: str) -> str:
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if BEGIN in line:
            skipping = True
            continue
        if END in line:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "".join(out)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".wp-penguin.tmp")
    tmp.write_text(text)
    os.chmod(tmp, path.stat().st_mode)
    tmp.replace(path)


def enable(path: Path | None = None) -> Path:
    """Aggiunge (o aggiorna) il blocco. Ritorna il percorso del backup."""
    path = path or config_file()
    if not path.is_file():
        raise FileNotFoundError(f"{path} non esiste")
    bak = backup(path)
    text = _strip_block(path.read_text()).rstrip("\n")
    _write(path, f"{text}\n\n{_block(path)}")
    return bak


def disable(path: Path | None = None) -> Path | None:
    path = path or config_file()
    if not is_enabled(path):
        return None
    bak = backup(path)
    text = _strip_block(path.read_text()).rstrip("\n") + "\n"
    _write(path, text)
    return bak
