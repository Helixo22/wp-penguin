# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Daemon in background: ripristino all'avvio, pausa e watchdog.

Pensato per `exec-once`. Fa quattro cose:
  1. ripristina i wallpaper salvati nella configurazione;
  2. ascolta gli eventi di Hyprland e mette in pausa (SIGSTOP) il wallpaper di
     un monitor quando il suo workspace attivo ha una finestra a schermo
     intero — o, con «pausa con finestre aperte», una finestra qualsiasi,
     anche flottante — e lo riprende (SIGCONT) quando il desktop è libero;
  3. rilancia il wallpaper quando un monitor viene ricollegato;
  4. watchdog: se un wallpaper termina da solo (crash) lo riavvia, con un
     limite di tentativi per non entrare in un ciclo di crash.

SIGSTOP azzera l'uso di CPU/GPU, cosa che un semplice "abbassa gli FPS" non fa.
"""

from __future__ import annotations

import fcntl
import os
import select
import signal
import sys
import time
from pathlib import Path

from . import config, engine, hypr

LOCK_FILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "wp-penguin.lock")
PKG_DIR = Path(__file__).resolve().parent


def code_mtime() -> float:
    """Ultima modifica del codice di wp-penguin (per ricaricarsi dopo un aggiornamento)."""
    return max((p.stat().st_mtime for p in PKG_DIR.glob("*.py")), default=0.0)


def running_pid() -> int | None:
    try:
        return int(Path(LOCK_FILE).read_text().strip()) if is_running() else None
    except (OSError, ValueError):
        return None

# Eventi dopo i quali lo stato "a schermo intero" può essere cambiato
RELEVANT = ("fullscreen", "workspace", "workspacev2", "focusedmon", "focusedmonv2",
            "openwindow", "closewindow", "movewindow", "movewindowv2",
            "moveworkspace", "moveworkspacev2", "changefloatingmode",
            "activespecial", "activespecialv2")
HOTPLUG = ("monitoradded", "monitoraddedv2")
POLL_SECONDS = 5  # rivalutazione periodica (copre i wallpaper avviati dalla GUI)
CRASH_GRACE = 8          # secondi di assenza prima di considerarlo un crash
CRASH_WINDOW = 300       # finestra temporale per contare i riavvii…
CRASH_MAX_RESTARTS = 3   # …e numero massimo di riavvii in quella finestra


def acquire_lock():
    """Un solo daemon per sessione. Ritorna il file aperto o None."""
    fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fd.close()
        return None
    fd.write(str(os.getpid()))
    fd.flush()
    return fd


def is_running() -> bool:
    try:
        with open(LOCK_FILE) as fd:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except FileNotFoundError:
        return False
    except BlockingIOError:
        return True


class Daemon:
    def __init__(self) -> None:
        self.paused: set[int] = set()   # PID che abbiamo fermato noi
        self.stopping = False
        self.missing_since: dict[str, float] = {}  # monitor → da quando manca il processo
        self.restarts: dict[str, list[float]] = {}  # monitor → istanti dei riavvii
        self.gave_up: set[str] = set()
        self.code_mtime = code_mtime()
        self._last_code_check = time.monotonic()
        self.reload = False

    def _signal(self, pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            self.paused.discard(pid)

    def evaluate(self) -> None:
        s = config.load()
        procs = engine.running()
        self.paused &= set(procs.values())  # dimentica i PID morti
        self.watchdog(s, procs)
        try:
            full = (hypr.busy_monitors(s.pause_on_fullscreen, s.pause_on_windows)
                    if (s.pause_on_fullscreen or s.pause_on_windows) else set())
        except (hypr.HyprlandError, OSError):
            return
        for monitor, pid in procs.items():
            if monitor in full and pid not in self.paused:
                self._signal(pid, signal.SIGSTOP)
                self.paused.add(pid)
            elif monitor not in full and pid in self.paused:
                self._signal(pid, signal.SIGCONT)
                self.paused.discard(pid)

    def watchdog(self, s: config.Settings, procs: dict[str, int]) -> None:
        """Riavvia i wallpaper che dovrebbero girare ma il cui processo è sparito."""
        if not s.autorestart or not s.monitors:
            return
        try:
            present = {m.name for m in hypr.monitors()}
        except (hypr.HyprlandError, OSError):
            return
        now = time.monotonic()
        for mon in s.monitors:
            expected = (mon in present and mon not in s.disabled_monitors
                        and mon not in s.stopped_monitors)
            if not expected or mon in procs:
                self.missing_since.pop(mon, None)
                if mon in procs:
                    self.gave_up.discard(mon)
                continue
            since = self.missing_since.setdefault(mon, now)
            if now - since < CRASH_GRACE or mon in self.gave_up:
                continue
            recent = [t for t in self.restarts.get(mon, []) if now - t < CRASH_WINDOW]
            if len(recent) >= CRASH_MAX_RESTARTS:
                self.gave_up.add(mon)
                print(f"[wp-penguin] {mon}: troppi crash ravvicinati, smetto di riavviarlo "
                      f"(resta lo sfondo statico)", file=sys.stderr)
                continue
            recent.append(now)
            self.restarts[mon] = recent
            self.missing_since.pop(mon, None)
            print(f"[wp-penguin] {mon}: wallpaper terminato inaspettatamente, lo riavvio "
                  f"({len(recent)}/{CRASH_MAX_RESTARTS})", file=sys.stderr)
            for err in engine.restore(s, only={mon}):
                print(f"[wp-penguin] {err}", file=sys.stderr)

    def resume_all(self) -> None:
        for pid in list(self.paused):
            self._signal(pid, signal.SIGCONT)
        self.paused.clear()

    def run(self, restore: bool) -> int:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stopping", True))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "stopping", True))

        if restore:
            # All'avvio della sessione la shell/barra può non essere ancora
            # pronta: un piccolo ritardo evita che il suo sfondo finisca sopra.
            time.sleep(float(os.environ.get("WP_PENGUIN_DELAY", "1.5")))
            for err in engine.restore(config.load()):
                print(f"[wp-penguin] {err}", file=sys.stderr)

        try:
            while not self.stopping:
                try:
                    self._loop()
                except (OSError, hypr.HyprlandError) as e:
                    if self.stopping:
                        break
                    print(f"[wp-penguin] IPC: {e}; riprovo tra 2s", file=sys.stderr)
                    time.sleep(2)
        finally:
            # Mai lasciare un wallpaper congelato quando usciamo
            self.resume_all()
        if self.reload:
            # Codice aggiornato: ci rilanciamo (il lock si libera con l'exec,
            # perché Python apre i file con O_CLOEXEC) senza ripristinare di nuovo
            print("[wp-penguin] codice aggiornato, riavvio del daemon", file=sys.stderr)
            os.execv(sys.executable, [sys.executable, "-m", "wp_penguin", "--watch"])
        return 0

    def _loop(self) -> None:
        sock = hypr.event_socket()
        buf = b""
        self.evaluate()
        with sock:
            while not self.stopping:
                try:
                    ready, _, _ = select.select([sock], [], [], POLL_SECONDS)
                except InterruptedError:
                    continue
                # Codice aggiornato? Controllo a tempo, non solo nei momenti di
                # quiete: con molti eventi (es. pausa con finestre aperte) la
                # select non scadrebbe mai
                if time.monotonic() - self._last_code_check >= POLL_SECONDS:
                    self._last_code_check = time.monotonic()
                    if code_mtime() > self.code_mtime:
                        self.reload = self.stopping = True
                        break
                if not ready:
                    self.evaluate()
                    continue
                data = sock.recv(4096)
                if not data:
                    raise OSError("socket eventi chiuso")
                buf += data
                *lines, buf = buf.split(b"\n")
                dirty = False
                for line in lines:
                    name, _, payload = line.decode(errors="replace").partition(">>")
                    if name in RELEVANT:
                        dirty = True
                    elif name in HOTPLUG:
                        # monitoraddedv2>>ID,NAME,DESCRIPTION  /  monitoradded>>NAME
                        mon = payload.split(",")[1] if name.endswith("v2") else payload
                        s = config.load()
                        if mon in s.monitors and mon not in engine.running():
                            time.sleep(0.5)
                            engine.restore(s, only={mon})
                        dirty = True
                if dirty:
                    self.evaluate()


def main(restore: bool = True) -> int:
    lock = acquire_lock()
    if lock is None:
        print("[wp-penguin] daemon già in esecuzione", file=sys.stderr)
        # Anche se il daemon c'è già, un --daemon esplicito ripristina comunque
        if restore:
            engine.restore(config.load())
        return 0
    return Daemon().run(restore)
