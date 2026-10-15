# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Avvio/arresto dei processi linux-wallpaperengine, uno per monitor.

I processi in esecuzione non vengono tracciati con PID file ma cercati in
/proc leggendo la riga di comando: così GUI, daemon e riavvii vedono sempre
lo stesso stato reale, anche per istanze lanciate a mano.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import library, static
from .config import Settings

ENGINE_NAME = "linux-wallpaperengine"
LOG_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "wp-penguin"

# Altri gestori di sfondi che disegnano sul layer "background" di wlr-layer-shell.
# swww è stato rinominato awww: gestiamo entrambi.
CONFLICTING = {
    "hyprpaper": None,
    "swww-daemon": ["swww", "kill"],
    "awww-daemon": ["awww", "kill"],
}


INCOMPATIBLE_FILE = LOG_DIR / "incompatible.json"
STARTUP_CHECK = 2.5  # secondi in cui un crash all'avvio viene riconosciuto


class EngineError(RuntimeError):
    pass


def incompatible() -> dict[str, str]:
    """Wallpaper che hanno fatto crashare il motore all'avvio: ID → motivo."""
    import json
    try:
        return json.loads(INCOMPATIBLE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _set_incompatible(workshop_id: str, reason: str | None) -> None:
    import json
    data = incompatible()
    if reason is None and workshop_id not in data:
        return
    if reason is None:
        data.pop(workshop_id, None)
    else:
        data[workshop_id] = reason
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    INCOMPATIBLE_FILE.write_text(json.dumps(data, indent=2))


def _crash_reason(log: Path) -> str:
    """Riga più significativa del log dopo un crash (es. l'eccezione C++)."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return "motivo sconosciuto"
    for line in reversed(lines):
        if "what():" in line:
            return line.split("what():", 1)[1].strip()
    for line in reversed(lines):
        if any(k in line.lower() for k in ("error", "failed", "cannot", "exception")):
            return line.strip()
    return lines[-1].strip() if lines else "motivo sconosciuto"


def engine_binary(s: Settings) -> str | None:
    return shutil.which(s.binary) or (s.binary if os.access(s.binary, os.X_OK) else None)


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def running() -> dict[str, int]:
    """Mappa monitor -> PID delle istanze linux-wallpaperengine attive."""
    result: dict[str, int] = {}
    my_uid = os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != my_uid:
                continue
        except OSError:
            continue
        args = _cmdline(int(entry.name))
        if not args or Path(args[0]).name != ENGINE_NAME:
            continue
        # Un'istanza può gestire più schermi: --screen-root A --bg X --screen-root B ...
        for i, a in enumerate(args[:-1]):
            if a == "--screen-root":
                result[args[i + 1]] = int(entry.name)
    return result


def _terminate(pid: int, timeout: float = 3.0) -> None:
    try:
        # Se il daemon l'aveva messo in pausa con SIGSTOP, un SIGTERM resterebbe
        # pendente: prima lo risvegliamo.
        os.kill(pid, signal.SIGCONT)
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists() or _is_zombie(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _is_zombie(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return True


def is_paused(pid: int) -> bool:
    """True se il processo è fermo (SIGSTOP del daemon per lo schermo intero)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] in ("T", "t")
    except (OSError, IndexError):
        return False


def stop(monitor: str) -> None:
    """Ferma il wallpaper sul monitor indicato (se presente)."""
    pid = running().get(monitor)
    if pid:
        _terminate(pid)


def stop_all() -> None:
    for pid in set(running().values()):
        _terminate(pid)


def kill_conflicting() -> list[str]:
    """Chiude hyprpaper/swww/awww. Ritorna i nomi dei processi fermati."""
    killed = []
    for proc, graceful in CONFLICTING.items():
        if subprocess.run(["pgrep", "-x", "-u", str(os.getuid()), proc],
                          capture_output=True).returncode != 0:
            continue
        if graceful and shutil.which(graceful[0]):
            subprocess.run(graceful, capture_output=True, timeout=5)
        subprocess.run(["pkill", "-x", "-u", str(os.getuid()), proc], capture_output=True)
        killed.append(proc)
    return killed


def build_command(s: Settings, monitor: str, wp: library.Wallpaper) -> list[str]:
    binary = engine_binary(s)
    if not binary:
        raise EngineError(f"'{s.binary}' non trovato nel PATH. Installa linux-wallpaperengine-git dall'AUR.")
    cmd = [binary, "--screen-root", monitor, "--bg", str(wp.path),
           "--fps", str(s.fps), "--scaling", s.scaling,
           # La pausa a schermo intero la gestisce il nostro daemon (per monitor,
           # via IPC di Hyprland): disattiviamo quella interna per non duplicarla.
           "--no-fullscreen-pause"]
    if s.muted:
        # --noautomute: il rilevatore «un'altra app sta suonando» si collega a
        # PulseAudio; senza audio (vedi engine_env) farebbe bloccare la chiusura
        cmd += ["--silent", "--noautomute", "--no-audio-processing"]
    else:
        # linux-wallpaperengine passa il volume a SDL_MixAudioFormat (0-128)
        cmd += ["--volume", str(round(s.volume * 128 / 100))]
    if s.disable_mouse:
        cmd.append("--disable-mouse")
    for key, val in wp.properties.items():  # preset del Workshop
        cmd += ["--set-property", f"{key}={val}"]
    if s.static_wallpaper:
        # Fotogramma a piena risoluzione per lo sfondo statico abbinato
        shot = static.screenshot_path(monitor, wp.workshop_id)
        shot.parent.mkdir(parents=True, exist_ok=True)
        cmd += ["--screenshot", str(shot), "--screenshot-delay", "30"]
    assets = library.find_assets_dir(s.extra_library_paths)
    if assets:
        cmd += ["--assets-dir", str(assets)]
    return cmd


ALSA_NULL_CONF = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wp-penguin/alsa-null.conf"


def engine_env(s: Settings) -> dict[str, str]:
    """Ambiente del processo. Con «Muto» il motore non deve aprire NESSUNO stream
    audio: --silent da solo mette in muto mpv ma lascia comunque aperti uno
    stream SDL e uno di libmpv, che con PipeWire/EasyEffects possono andare in
    xrun e ammutolire tutto l'audio del PC. Togliamo quindi l'accesso ai
    server audio: SDL usa il driver nullo, mpv non trova né PipeWire né
    PulseAudio e ALSA punta a un dispositivo nullo."""
    env = dict(os.environ)
    if s.muted:
        ALSA_NULL_CONF.parent.mkdir(parents=True, exist_ok=True)
        if not ALSA_NULL_CONF.exists():
            ALSA_NULL_CONF.write_text("pcm.!default { type null }\nctl.!default { type hw card 0 }\n")
        env.update({
            "SDL_AUDIODRIVER": "dummy",
            "PIPEWIRE_REMOTE": "wp-penguin-no-audio",
            "PULSE_SERVER": "unix:/nonexistent/wp-penguin-no-audio",
            "ALSA_CONFIG_PATH": str(ALSA_NULL_CONF),
        })
    return env


def start(s: Settings, monitor: str, wp: library.Wallpaper) -> int:
    """Ferma l'eventuale wallpaper precedente su `monitor` e avvia `wp`.

    Il processo è staccato (nuova sessione): sopravvive alla chiusura della
    GUI. stdout/stderr finiscono in ~/.local/state/wp-penguin/<monitor>.log.
    """
    if not wp.supported:
        raise EngineError(f"Il tipo '{wp.type}' non è supportato da linux-wallpaperengine.")
    cmd = build_command(s, monitor, wp)
    stop(monitor)
    # Con lo sfondo statico attivo gli altri gestori servono: non li chiudiamo
    if s.kill_conflicting and not s.static_wallpaper:
        kill_conflicting()
    started = time.time()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = open(LOG_DIR / f"{monitor}.log", "wb")
    with log:
        log.write((" ".join(cmd) + "\n\n").encode())
        log.flush()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, env=engine_env(s),
                                stderr=subprocess.STDOUT, start_new_session=True)
    # Se crasha all'avvio (asset mancanti, effetti non gestiti, bug del motore…)
    # lo segnaliamo e marchiamo il wallpaper come non compatibile.
    deadline = time.monotonic() + STARTUP_CHECK
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.1)
    if proc.poll() is not None:
        log_path = LOG_DIR / f"{monitor}.log"
        reason = _crash_reason(log_path)
        _set_incompatible(wp.workshop_id, reason)
        raise EngineError(
            f"«{wp.title}» non è compatibile con linux-wallpaperengine: il motore si è "
            f"chiuso all'avvio (codice {proc.returncode}).\n\n"
            f"Errore: {reason}\n\nLog completo: {log_path}")
    _set_incompatible(wp.workshop_id, None)  # funziona: togliamo un'eventuale marcatura
    if s.static_wallpaper:
        # Thread non-daemon: anche `wp-penguin --restore` aspetta che finisca
        threading.Thread(target=static.apply, args=(monitor, wp, started),
                         name=f"static-{monitor}").start()
    return proc.pid


def restore(s: Settings, only: set[str] | None = None, include_stopped: bool = False) -> list[str]:
    """Riavvia i wallpaper salvati in config. Ritorna eventuali errori.

    I monitor fermati a mano («Ferma») vengono saltati, salvo include_stopped
    (riavvio esplicito dalla GUI)."""
    errors = []
    try:
        from . import hypr
        present = {m.name for m in hypr.monitors()}
    except Exception:
        present = None  # IPC non disponibile: proviamo comunque
    for monitor, wid in s.monitors.items():
        if only is not None and monitor not in only:
            continue
        if monitor in s.disabled_monitors:
            continue
        if monitor in s.stopped_monitors and not include_stopped:
            continue
        if present is not None and monitor not in present:
            continue
        wp = library.by_id(wid, s.extra_library_paths)
        if not wp:
            errors.append(f"{monitor}: wallpaper {wid} non trovato")
            continue
        try:
            start(s, monitor, wp)
        except EngineError as e:
            errors.append(f"{monitor}: {e}")
    return errors
