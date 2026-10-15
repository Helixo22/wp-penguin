# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Accesso minimale all'IPC di Hyprland (senza lanciare hyprctl ogni volta).

- .socket.sock  -> richieste/risposte (equivalente di `hyprctl -j ...`)
- .socket2.sock -> flusso di eventi testuali "nome>>dati"
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path


class HyprlandError(RuntimeError):
    pass


def _socket_dir() -> Path:
    sig = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not sig:
        raise HyprlandError("HYPRLAND_INSTANCE_SIGNATURE non impostata: Hyprland non è in esecuzione?")
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    for base in (Path(runtime) / "hypr", Path("/tmp/hypr")):  # /tmp per versioni vecchie
        if (base / sig / ".socket.sock").exists():
            return base / sig
    raise HyprlandError("socket IPC di Hyprland non trovato")


def request_json(command: str):
    """Esegue un comando IPC con output JSON, es. request_json('monitors')."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2)
        s.connect(str(_socket_dir() / ".socket.sock"))
        s.sendall(f"j/{command}".encode())
        chunks = []
        while chunk := s.recv(65536):
            chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks))
    except ValueError as e:
        raise HyprlandError(f"risposta IPC non valida per '{command}'") from e


@dataclass
class Monitor:
    name: str          # es. "DP-3" -> usato con --screen-root
    description: str   # marca/modello
    width: int
    height: int
    refresh: float
    active_workspace: int

    @property
    def label(self) -> str:
        # Breve: finisce nell'header, che con il tiling può essere stretto
        return f"{self.name} · {self.width}×{self.height}@{self.refresh:.0f}Hz"


def monitors() -> list[Monitor]:
    return [
        Monitor(
            name=m["name"],
            description=m.get("description", ""),
            width=m.get("width", 0),
            height=m.get("height", 0),
            refresh=m.get("refreshRate", 0.0),
            active_workspace=m.get("activeWorkspace", {}).get("id", 0),
        )
        for m in request_json("monitors")
        if not m.get("disabled", False)
    ]


def busy_monitors(fullscreen: bool = True, any_window: bool = False) -> set[str]:
    """Monitor il cui wallpaper non si vede (o non serve vederlo):
    - fullscreen: il workspace attivo ha una finestra a schermo intero/massimizzata;
    - any_window: il workspace attivo (o lo special workspace aperto sopra)
      contiene almeno una finestra, anche flottante.
    """
    workspaces = {w["id"]: w for w in request_json("workspaces")}
    busy = set()
    for m in request_json("monitors"):
        if m.get("disabled", False):
            continue
        ids = [m.get("activeWorkspace", {}).get("id")]
        special = m.get("specialWorkspace", {}).get("id", 0)
        if special:  # special workspace (scratchpad) visibile su questo monitor
            ids.append(special)
        for i in ids:
            w = workspaces.get(i)
            if not w:
                continue
            if (any_window and w.get("windows", 0) > 0) or (fullscreen and w.get("hasfullscreen")):
                busy.add(m["name"])
    return busy


def fullscreen_monitors() -> set[str]:
    return busy_monitors(fullscreen=True, any_window=False)


def event_socket() -> socket.socket:
    """Socket connesso al flusso eventi; ogni riga è 'evento>>dati'."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(str(_socket_dir() / ".socket2.sock"))
    return s
