# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedura guidata del primo avvio (riapribile da Impostazioni).

Schermate, in un Gtk.Stack con transizione a scorrimento:
  0 Benvenuto · 1 Dipendenze · 2 Steam/Workshop · 3 Monitor
  4 Preferenze · 5 Avvio automatico · 6 Fine

Il wizard lavora su una copia delle impostazioni: tutto viene salvato solo
quando si preme «Fine», insieme a first_run_done = True.
"""

from __future__ import annotations

import copy
import subprocess
import threading
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib, Gtk

from . import APP_ID, APP_NAME, appearance, autostart, config, deps, engine, hypr, library
from .widgets import (Group, Row, Segmented, Toast, copy_to_clipboard, label, pill,
                      pointerize)

PAGES = ["welcome", "deps", "steam", "monitors", "prefs", "autostart", "done"]


def page_shell(icon: str, title: str, text: str) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
    """Struttura comune: icona grande, titolo, testo, poi il contenuto."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.add_css_class("wizard-page")
    if icon == APP_ID:  # schermata di benvenuto: il logo dell'app, a colori
        box.append(Gtk.Image(icon_name=icon, pixel_size=112, halign=Gtk.Align.CENTER,
                             margin_bottom=6))
    else:
        box.append(Gtk.Image(icon_name=icon, pixel_size=56, halign=Gtk.Align.CENTER,
                             css_classes=["hero-icon"], margin_bottom=6))
    box.append(label(title, "wizard-title", xalign=0.5, wrap=True))
    box.append(label(text, "wizard-text", xalign=0.5, wrap=True, max_chars=70,
                     justify=Gtk.Justification.CENTER, margin_bottom=10))
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.append(content)
    scroller = Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
    return scroller, content


def status_icon(ok: bool | None, required: bool = True) -> Gtk.Image:
    if ok:
        return Gtk.Image(icon_name="emblem-ok-symbolic", css_classes=["check-ok"])
    if ok is None:
        return Gtk.Image(icon_name="content-loading-symbolic", css_classes=["dim"])
    return Gtk.Image(icon_name="dialog-error-symbolic" if required else "dialog-warning-symbolic",
                     css_classes=["check-bad" if required else "check-warn"])


class Wizard(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application, on_finish: Callable[[config.Settings], None],
                 parent: Gtk.Window | None = None) -> None:
        super().__init__(application=app, title=f"Benvenuto in {APP_NAME}",
                         default_width=780, default_height=640,
                         modal=parent is not None, transient_for=parent)
        self.add_css_class("wpp")
        self.add_css_class("wizard")
        # Niente barra del titolo CSD: su Hyprland le decorazioni le fa il compositor
        self.set_titlebar(Gtk.Box(visible=False))

        self.s = copy.deepcopy(config.load())
        self.on_finish = on_finish
        self.index = 0
        self.monitor_switches: dict[str, Gtk.Switch] = {}

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=380, vexpand=True)
        builders = [self._page_welcome, self._page_deps, self._page_steam, self._page_monitors,
                    self._page_prefs, self._page_autostart, self._page_done]
        for name, build in zip(PAGES, builders):
            self.stack.add_named(build(), name)

        # --- piè di pagina: Indietro · puntini · Continua
        self.back_btn = pill("Indietro")
        self.back_btn.connect("clicked", lambda *_: self.go(self.index - 1))
        self.next_btn = pill("Continua", "accent")
        self.next_btn.connect("clicked", lambda *_: self._next())
        self.dots = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
        self.dots.add_css_class("dots")
        for _ in PAGES:
            self.dots.append(Gtk.Box(css_classes=["dot"], valign=Gtk.Align.CENTER))
        footer = Gtk.Box(spacing=12)
        footer.add_css_class("wizard-footer")
        footer.append(self.back_btn)
        footer.append(self.dots)
        footer.append(self.next_btn)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self.stack)
        root.append(footer)
        self.toast = Toast()
        overlay = Gtk.Overlay(child=root)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)
        pointerize(self)  # cursore a manina su pulsanti, interruttori, ecc.
        self.go(0)

    # ---------------------------------------------------------- navigazione

    def go(self, i: int) -> None:
        if not 0 <= i < len(PAGES):
            return
        forward = i >= self.index
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT if forward
                                       else Gtk.StackTransitionType.SLIDE_RIGHT)
        self.index = i
        self.stack.set_visible_child_name(PAGES[i])
        for n, dot in enumerate(self._children(self.dots)):
            (dot.add_css_class if n == i else dot.remove_css_class)("current")
        self.back_btn.set_opacity(0 if i == 0 else 1)
        self.back_btn.set_sensitive(i > 0)
        last = i == len(PAGES) - 1
        self.next_btn.set_label("Inizia a usare wp-penguin" if last else
                                "Iniziamo" if i == 0 else "Continua")
        # Aggiorna i contenuti che dipendono dal sistema quando si entra nella pagina
        {"deps": self._refresh_deps, "steam": self._refresh_steam,
         "monitors": self._refresh_monitors, "autostart": self._refresh_autostart,
         "done": self._refresh_summary}.get(PAGES[i], lambda: None)()

    @staticmethod
    def _children(w: Gtk.Widget):
        c = w.get_first_child()
        while c is not None:
            yield c
            c = c.get_next_sibling()

    def _next(self) -> None:
        if PAGES[self.index] == "monitors":
            self.s.disabled_monitors = [m for m, sw in self.monitor_switches.items()
                                        if not sw.get_active()]
        if PAGES[self.index] == "prefs":
            self._read_prefs()
        if self.index == len(PAGES) - 1:
            self._finish()
        else:
            self.go(self.index + 1)

    # ------------------------------------------------------------- pagine

    def _page_welcome(self) -> Gtk.Widget:
        page, content = page_shell(
            APP_ID, f"Benvenuto in {APP_NAME}",
            "Usa i wallpaper animati di Wallpaper Engine come sfondo di Hyprland. "
            "Scegli un wallpaper dal Workshop di Steam, decidi su quale monitor mostrarlo "
            "e wp-penguin si occupa del resto: avvio, pausa quando giochi a schermo intero "
            "e ripristino a ogni accesso.")
        g = Group()
        g.add(Row("Scene, video e pagine web", "Riprodotti da linux-wallpaperengine",
                  icon="video-x-generic-symbolic"))
        g.add(Row("Un wallpaper per monitor", "Ogni schermo può avere il suo",
                  icon="video-display-symbolic", icon_class="green"))
        g.add(Row("Leggero", "Si mette in pausa da solo con le app a schermo intero",
                  icon="battery-full-symbolic", icon_class="orange"))
        content.append(g)
        content.append(label("Ci vorrà circa un minuto. Puoi riaprire questa procedura "
                             "in qualsiasi momento da Impostazioni.",
                             "caption-text", xalign=0.5, wrap=True, margin_top=14))
        return page

    def _page_deps(self) -> Gtk.Widget:
        page, content = page_shell(
            "system-software-install-symbolic", "Dipendenze",
            "Controllo che sul sistema ci sia tutto il necessario. Se manca qualcosa trovi "
            "il comando esatto da incollare nel terminale.")
        self.deps_group = Group()
        content.append(self.deps_group)
        recheck = pill("Ricontrolla", icon="view-refresh-symbolic", halign=Gtk.Align.CENTER,
                       margin_top=14)
        recheck.connect("clicked", lambda *_: self._refresh_deps())
        content.append(recheck)
        return page

    def _refresh_deps(self) -> None:
        self.deps_group.clear()
        self.deps_group.add(Row("Controllo in corso…", suffix=Gtk.Spinner(spinning=True)))

        def work():
            checks = deps.run_checks(self.s)
            GLib.idle_add(self._show_deps, checks)

        threading.Thread(target=work, daemon=True).start()

    def _show_deps(self, checks: list[deps.Check]) -> bool:
        self.deps_group.clear()
        for c in checks:
            row = Row(c.name + ("" if c.required else "  (facoltativo)"), c.detail,
                      suffix=status_icon(c.ok, c.required))
            self.deps_group.add(row)
            if c.command:
                # Riga con il comando da copiare
                box = Gtk.Box(spacing=8)
                box.add_css_class("code-box")
                box.append(label(c.command, "mono", wrap=True, hexpand=True, selectable=True))
                copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copia",
                                      valign=Gtk.Align.CENTER, css_classes=["flat"])
                copy_btn.connect("clicked", lambda _b, cmd=c.command: (
                    copy_to_clipboard(self, cmd), self.toast.pop("Comando copiato negli appunti")))
                box.append(copy_btn)
                cmd_row = Gtk.ListBoxRow(activatable=False, selectable=False, child=box)
                self.deps_group.add(cmd_row)
        missing = [c.name for c in checks if c.required and not c.ok]
        self.deps_group.footer.set_label(
            "Tutto pronto." if not missing else
            "Puoi continuare la configurazione anche ora e installare in seguito: "
            "l'app ricontrolla quando applichi un wallpaper.")
        self.deps_group.footer.set_visible(True)
        return False

    def _page_steam(self) -> Gtk.Widget:
        page, content = page_shell(
            "folder-download-symbolic", "Steam e Workshop",
            "Cerco l'installazione di Steam (nativa o Flatpak) e la cartella dei wallpaper "
            "scaricati dal Workshop (steamapps/workshop/content/431960).")
        self.steam_group = Group()
        content.append(self.steam_group)
        btns = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=14)
        b1 = pill("Scegli cartella Steam…", icon="folder-open-symbolic")
        b1.connect("clicked", lambda *_: self._pick_folder("Cartella di Steam o della libreria"))
        b2 = pill("Scegli cartella Workshop…", icon="folder-open-symbolic")
        b2.connect("clicked", lambda *_: self._pick_folder("Cartella …/workshop/content/431960"))
        btns.append(b1)
        btns.append(b2)
        content.append(btns)
        self.reset_paths_btn = pill("Rimuovi cartelle scelte a mano", "danger",
                                    halign=Gtk.Align.CENTER, margin_top=6)
        self.reset_paths_btn.connect("clicked", lambda *_: (self.s.extra_library_paths.clear(),
                                                            self._refresh_steam()))
        content.append(self.reset_paths_btn)
        return page

    def _refresh_steam(self) -> None:
        g = self.steam_group
        g.clear()
        roots = library.steam_roots()
        for r in roots:
            kind = "Flatpak" if ".var/app" in str(r) else "nativo"
            g.add(Row(f"Steam ({kind})", str(r), suffix=status_icon(True)))
        if not roots:
            g.add(Row("Steam", "Non trovato nei percorsi standard: sceglilo a mano.",
                      suffix=status_icon(False)))
        for p in self.s.extra_library_paths:
            g.add(Row("Cartella scelta a mano", p, suffix=status_icon(Path(p).is_dir())))

        wdirs = library.workshop_dirs(self.s.extra_library_paths)
        if wdirs:
            count = len(library.scan(self.s.extra_library_paths))
            for d in wdirs:
                g.add(Row("Workshop di Wallpaper Engine", str(d), suffix=status_icon(True)))
            g.footer.set_label(f"Trovati {count} wallpaper.")
        else:
            g.add(Row("Workshop di Wallpaper Engine",
                      "Nessuna cartella trovata. Iscriviti a qualche wallpaper nel Workshop "
                      "o scegli la cartella a mano.", suffix=status_icon(False)))
            g.footer.set_label("")
        g.footer.set_visible(bool(wdirs))
        assets = library.find_assets_dir(self.s.extra_library_paths)
        g.add(Row("Assets di Wallpaper Engine",
                  str(assets) if assets else "Non trovati: alcune scene non funzioneranno.",
                  suffix=status_icon(bool(assets), required=False)))
        self.reset_paths_btn.set_visible(bool(self.s.extra_library_paths))

    def _pick_folder(self, title: str) -> None:
        dialog = Gtk.FileDialog(title=title, modal=True)
        dialog.set_initial_folder(Gio.File.new_for_path(str(Path.home())))

        def done(dlg, result):
            try:
                folder = dlg.select_folder_finish(result)
            except GLib.Error:
                return  # annullato
            path = folder.get_path()
            before = len(library.workshop_dirs(self.s.extra_library_paths))
            if path and path not in self.s.extra_library_paths:
                self.s.extra_library_paths.append(path)
            if len(library.workshop_dirs(self.s.extra_library_paths)) <= before:
                self.s.extra_library_paths.remove(path)
                self.toast.pop("In quella cartella non c'è un Workshop di Wallpaper Engine")
            self._refresh_steam()

        dialog.select_folder(self, None, done)

    def _page_monitors(self) -> Gtk.Widget:
        page, content = page_shell(
            "video-display-symbolic", "Monitor",
            "Ecco gli schermi rilevati da Hyprland. Scegli su quali vuoi usare i "
            "wallpaper animati: sugli altri non verrà avviato nulla.")
        self.mon_group = Group(footer="Potrai cambiare idea dalla sezione Monitor.")
        content.append(self.mon_group)
        return page

    def _refresh_monitors(self) -> None:
        g = self.mon_group
        g.clear()
        self.monitor_switches.clear()
        try:
            mons = hypr.monitors()
        except (hypr.HyprlandError, OSError) as e:
            g.add(Row("Impossibile leggere i monitor", str(e), suffix=status_icon(False)))
            return
        for m in mons:
            sw = Gtk.Switch(active=m.name not in self.s.disabled_monitors)
            self.monitor_switches[m.name] = sw
            g.add(Row(m.name, f"{m.description}\n{m.width}×{m.height} · {m.refresh:.0f} Hz",
                      suffix=sw, icon="video-display-symbolic"))

    def _page_prefs(self) -> Gtk.Widget:
        page, content = page_shell(
            "preferences-system-symbolic", "Preferenze",
            "Le impostazioni di partenza. Si cambiano quando vuoi da Impostazioni.")
        s = self.s
        perf = Group("Prestazioni", "Meno FPS significa meno GPU e meno batteria. "
                                    "30 è un buon compromesso.")
        self.fps_spin = Gtk.SpinButton.new_with_range(5, 240, 5)
        self.fps_spin.set_value(s.fps)
        perf.add(Row("FPS massimi", suffix=self.fps_spin, icon="speedometer-symbolic"))
        self.pause_sw = Gtk.Switch(active=s.pause_on_fullscreen)
        perf.add(Row("Pausa con schermo intero",
                     "Congela il wallpaper del monitor dove c'è un gioco o un video a tutto schermo",
                     suffix=self.pause_sw, icon="media-playback-pause-symbolic", icon_class="orange"))
        self.pause_win_sw = Gtk.Switch(active=s.pause_on_windows)
        perf.add(Row("Pausa con finestre aperte",
                     "Anche una sola finestra flottante sul monitor mette in pausa lo sfondo",
                     suffix=self.pause_win_sw, icon="window-new-symbolic", icon_class="orange"))
        self.static_sw = Gtk.Switch(active=s.static_wallpaper)
        perf.add(Row("Imposta anche come sfondo statico",
                     "Un fotogramma resta sotto l'animazione: se crasha vedi comunque "
                     "lo sfondo giusto", suffix=self.static_sw,
                     icon="image-x-generic-symbolic", icon_class="green"))
        content.append(perf)

        audio = Group("Audio")
        self.mute_sw = Gtk.Switch(active=s.muted)
        audio.add(Row("Muto", suffix=self.mute_sw, icon="audio-volume-muted-symbolic",
                      icon_class="gray"))
        self.vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.vol_scale.set_value(s.volume)
        self.vol_scale.set_size_request(200, -1)
        self.vol_scale.set_sensitive(not s.muted)
        self.mute_sw.connect("notify::active",
                             lambda sw, _p: self.vol_scale.set_sensitive(not sw.get_active()))
        audio.add(Row("Volume", suffix=self.vol_scale, icon="audio-volume-high-symbolic",
                      icon_class="green"))
        content.append(audio)

        look = Group("Aspetto")
        self.scheme_seg = Segmented(["Automatico", "Chiaro", "Scuro"],
                                    appearance.COLOR_SCHEMES.index(s.color_scheme))
        self.scheme_seg.connect("changed", lambda _w, i: appearance.apply_color_scheme(
            appearance.COLOR_SCHEMES[i]))
        look.add(Row("Tema", "Automatico segue il tema di sistema", suffix=self.scheme_seg,
                     icon="weather-clear-night-symbolic", icon_class="gray"))
        content.append(look)
        return page

    def _read_prefs(self) -> None:
        self.s.fps = int(self.fps_spin.get_value())
        self.s.pause_on_fullscreen = self.pause_sw.get_active()
        self.s.pause_on_windows = self.pause_win_sw.get_active()
        self.s.static_wallpaper = self.static_sw.get_active()
        self.s.muted = self.mute_sw.get_active()
        self.s.volume = int(self.vol_scale.get_value())
        self.s.color_scheme = appearance.COLOR_SCHEMES[self.scheme_seg.selected]

    def _page_autostart(self) -> Gtk.Widget:
        page, content = page_shell(
            "system-run-symbolic", "Avvio automatico",
            "wp-penguin può aggiungersi alla configurazione di Hyprland per ripristinare "
            "il wallpaper a ogni accesso. Prima di modificarla ne faccio un backup.")
        g = Group()
        self.autostart_sw = Gtk.Switch(active=True)
        self.autostart_row = g.add(Row("Avvia all'accensione", "", suffix=self.autostart_sw,
                                       icon="system-run-symbolic", icon_class="green"))
        self.conflict_sw = Gtk.Switch(active=self.s.kill_conflicting)
        self.conflict_row = g.add(Row("Chiudi hyprpaper / swww / awww",
                                      "Di solito non serve: il wallpaper animato sta su un "
                                      "livello sopra gli altri gestori di sfondi",
                                      suffix=self.conflict_sw, icon="process-stop-symbolic",
                                      icon_class="orange"))
        content.append(g)
        self.autostart_info = label("", "section-footer", wrap=True)
        content.append(self.autostart_info)
        return page

    def _refresh_autostart(self) -> None:
        path = autostart.config_file()
        already = autostart.is_enabled(path)
        if not path.exists():
            self.autostart_sw.set_active(False)
        kind = "Lua: hl.on(\"hyprland.start\", …)" if path.suffix == ".lua" else "exec-once"
        self.autostart_row.set_subtitle(
            f"{path}\n{kind} → {autostart.launcher()} --daemon"
            + ("\n✓ Già presente" if already else ""))
        self.autostart_sw.set_sensitive(path.exists())

        running = []
        for proc in [*engine.CONFLICTING, "ambxst"]:
            if subprocess.run(["pgrep", "-x", proc], capture_output=True).returncode == 0:
                running.append(proc)
        info = (f"Il backup verrà salvato come {path.name}.bak-wp-penguin-<data>, accanto "
                "all'originale.")
        if not path.exists():
            info = f"{path} non esiste: l'avvio automatico non può essere aggiunto."
        others = [p for p in running if p != "ambxst"]
        if others:
            info += ("\n\nIn esecuzione ora: " + ", ".join(others) + ". Toglili dall'avvio "
                     "automatico di Hyprland per evitare che coprano il wallpaper animato.")
        if "ambxst" in running:
            info += ("\n\nAmbxst disegna un suo sfondo che non si può disattivare da qui: "
                     "wp-penguin si avvia dopo di lui, così il wallpaper animato resta sopra.")
        self.autostart_info.set_label(info)

    def _page_done(self) -> Gtk.Widget:
        page, content = page_shell("emblem-ok-symbolic", "Tutto pronto",
                                   "Ecco il riepilogo. Premi il pulsante per aprire la libreria "
                                   "e scegliere il primo wallpaper.")
        self.summary = Group()
        content.append(self.summary)
        return page

    def _refresh_summary(self) -> None:
        g = self.summary
        g.clear()
        n = len(library.scan(self.s.extra_library_paths))
        g.add(Row("Wallpaper trovati", str(n), icon="image-x-generic-symbolic"))
        enabled = [m for m, sw in self.monitor_switches.items() if sw.get_active()]
        g.add(Row("Monitor attivi", ", ".join(enabled) or "nessuno",
                  icon="video-display-symbolic", icon_class="green"))
        g.add(Row("Preferenze", f"{self.s.fps} FPS · "
                  + ("muto" if self.s.muted else f"volume {self.s.volume}%") + " · pausa "
                  + ("attiva" if self.s.pause_on_fullscreen else "disattivata"),
                  icon="preferences-system-symbolic", icon_class="orange"))
        g.add(Row("Avvio automatico", "sì" if self.autostart_sw.get_active() else "no",
                  icon="system-run-symbolic", icon_class="gray"))

    # ---------------------------------------------------------------- fine

    def _finish(self) -> None:
        s = self.s
        s.kill_conflicting = self.conflict_sw.get_active()
        path = autostart.config_file()
        try:
            if path.exists():
                if self.autostart_sw.get_active() and not autostart.is_enabled(path):
                    autostart.enable(path)
                elif not self.autostart_sw.get_active() and autostart.is_enabled(path):
                    autostart.disable(path)
        except OSError as e:
            Gtk.AlertDialog(message="Avvio automatico non configurato", detail=str(e)).show(self)
        s.first_run_done = True
        config.save(s)
        self.on_finish(s)
        self.close()
