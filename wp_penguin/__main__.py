# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Punto di ingresso: `python -m wp_penguin [--daemon|--restore|--stop]`."""

import argparse
import sys


def main() -> int:
    p = argparse.ArgumentParser(prog="wp-penguin",
                                description="Frontend per linux-wallpaperengine su Hyprland")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--daemon", action="store_true",
                   help="ripristina l'ultima scelta e gestisce la pausa a schermo intero (per exec-once)")
    g.add_argument("--restore", action="store_true", help="ripristina l'ultima scelta ed esce")
    # --watch: solo pausa a schermo intero, senza ripristino (lo usa la GUI)
    g.add_argument("--watch", action="store_true", help=argparse.SUPPRESS)
    g.add_argument("--wizard", action="store_true", help="apre la procedura guidata")
    g.add_argument("--stop", action="store_true", help="ferma tutti i wallpaper ed esce")
    args = p.parse_args()

    if args.daemon or args.watch:
        from . import daemon
        return daemon.main(restore=args.daemon)
    if args.restore:
        from . import config, engine
        errors = engine.restore(config.load())
        for e in errors:
            print(e, file=sys.stderr)
        return 1 if errors else 0
    if args.stop:
        from . import config, engine
        # Segnati come fermati a mano: il watchdog del daemon non li riavvia
        s = config.load()
        s.stopped_monitors = sorted(set(s.stopped_monitors) | set(engine.running()))
        config.save(s)
        engine.stop_all()
        return 0

    from . import gui
    return gui.main(wizard=args.wizard)


if __name__ == "__main__":
    sys.exit(main())
