# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finestra principale GTK4 in stile macOS.

    ┌──────────────┬────────────────────────────────────────┬─────────────┐
    │ ● wp-penguin │ Libreria                [cerca] [seg]  │ inspector   │
    │              │ ┌────┐ ┌────┐ ┌────┐ ┌────┐            │ (a scompar- │
    │ ▣ Libreria   │ │    │ │    │ │    │ │    │            │  sa quando  │
    │ ▭ Monitor    │ └────┘ └────┘ └────┘ └────┘            │  selezioni  │
    │ ⚙ Impostaz.  │ Titolo  Titolo  Titolo  Titolo         │  un wall.)  │
    │              │                                        │             │
    │ [in riprod.] │                                        │             │
    └──────────────┴────────────────────────────────────────┴─────────────┘

Lo stile è tutto in style.css; qui si assegnano solo le classi CSS.
Le operazioni lente (scansione, decodifica anteprime, avvio del motore) girano
in thread e aggiornano la UI con GLib.idle_add.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import (APP_ID, APP_NAME, appearance, autostart, config, daemon,  # noqa: E402
               engine, hypr, library, static)
from .widgets import (Group, Row, Segmented, Toast, label, page_header, pill,  # noqa: E402
                      pointerize)
from .workshop import WorkshopPage  # noqa: E402

THUMB_W, THUMB_H = 300, 169          # anteprime grandi, 16:9
HERO_W, HERO_H = 300, 169
TYPE_FILTERS = [("Tutti", None), ("Nuovi", "new"), ("Scene", "scene"), ("Video", "video"),
                ("Web", "web")]
NEW_FILTER_INDEX = 1
NEW_BADGE_DAYS = 3     # badge «Nuovo» sulle card scaricate negli ultimi N giorni
NEW_LIST_DAYS = 30     # la sezione «Nuovi» mostra gli ultimi N giorni, dal più recente


# ------------------------------------------------------------------ immagini

def pixbuf_to_texture(pb: GdkPixbuf.Pixbuf) -> Gdk.Texture:
    """Conversione senza la deprecata Gdk.Texture.new_for_pixbuf."""
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt,
                                 pb.read_pixel_bytes(), pb.get_rowstride())


def load_thumb(path: Path, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    """Decodifica e ritaglia al centro esattamente w×h (molte anteprime sono
    quadrate: senza ritaglio deformerebbero il layout). GIF: primo frame."""
    try:
        info = GdkPixbuf.Pixbuf.get_file_info(str(path))
        fw, fh = info[1], info[2]
        if not (fw and fh):
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), w, h, False)
        scale = max(w / fw, h / fh)
        sw, sh = max(w, round(fw * scale)), max(h, round(fh * scale))
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), sw, sh, False)
        return pb.new_subpixbuf((sw - w) // 2, (sh - h) // 2, w, h).copy()
    except (GLib.Error, TypeError):
        return None


class FixedPicture(Gtk.Picture):
    """Picture di dimensione fissa: la sua grandezza non dipende dalla
    risoluzione dell'immagine (un'anteprima a 600 px non allarga il layout)."""

    def __init__(self, w: int, h: int) -> None:
        super().__init__(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        self._size = (w, h)

    def do_measure(self, orientation, _for_size):
        n = self._size[0] if orientation == Gtk.Orientation.HORIZONTAL else self._size[1]
        return n, n, -1, -1


def rounded_picture(w: int, h: int, *classes: str) -> tuple[Gtk.Overlay, Gtk.Picture]:
    """Picture dentro un contenitore che ritaglia gli angoli arrotondati."""
    pic = FixedPicture(w, h)
    frame = Gtk.Overlay(child=pic, overflow=Gtk.Overflow.HIDDEN,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
    for c in classes:
        frame.add_css_class(c)
    return frame, pic


# ---------------------------------------------------------------- card griglia

class Card(Gtk.FlowBoxChild):
    """Anteprima grande + badge + titolo sotto."""

    def __init__(self, wp: library.Wallpaper, broken: str | None = None) -> None:
        super().__init__()
        self.wp = wp
        self.broken = broken  # motivo del crash, se il motore non lo regge
        self.add_css_class("card")
        self.set_tooltip_text(f"{wp.title}\nID {wp.workshop_id}")

        self.frame, self.picture = rounded_picture(THUMB_W, THUMB_H, "thumb")
        kind = wp.type.capitalize() + (" · preset" if wp.is_preset else "")
        text = "Non supportato" if not wp.supported else "Non compatibile" if broken else kind
        badges = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.START)
        badge = label(text, "badge")
        if broken:
            badge.add_css_class("broken")
        badges.append(badge)
        self.is_new = bool(wp.added) and time.time() - wp.added < NEW_BADGE_DAYS * 86400
        if self.is_new:
            badges.append(label("Nuovo", "badge", "new"))
        self.frame.add_overlay(badges)
        self.active_badge = label("", "badge", "active", halign=Gtk.Align.END,
                                  valign=Gtk.Align.START, visible=False)
        self.frame.add_overlay(self.active_badge)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.frame)
        title = label(wp.title, "card-title", ellipsize=Pango.EllipsizeMode.END,
                      max_chars=30)
        title.set_size_request(THUMB_W, -1)
        box.append(title)
        if not wp.supported or broken:
            self.set_opacity(0.5)
        self.set_child(box)
        self.set_cursor_from_name("pointer")

    def set_texture(self, tex: Gdk.Texture) -> None:
        self.picture.set_paintable(tex)
        self.picture.add_css_class("loaded")   # → dissolvenza in entrata (CSS)

    def set_active_on(self, monitors: list[str]) -> None:
        self.active_badge.set_visible(bool(monitors))
        self.active_badge.set_label("In uso · " + ", ".join(monitors))


# ------------------------------------------------------------- finestra

class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app: "App") -> None:
        super().__init__(application=app, title=APP_NAME, default_width=1360, default_height=860)
        self.app = app
        self.add_css_class("wpp")
        self.set_titlebar(Gtk.Box(visible=False))  # niente headerbar: look "unified"

        self.settings = config.load()
        self.wallpapers: list[library.Wallpaper] = []
        self.cards: dict[str, Card] = {}
        self.monitors: list[hypr.Monitor] = []
        self.thumb_cache: dict[str, Gdk.Texture] = {}
        self.pool = ThreadPoolExecutor(max_workers=4)
        self._loading = False
        self._dir_monitors: dict[str, Gio.FileMonitor] = {}  # cartelle Workshop osservate
        self._rescan_timeout = 0
        self._known_before: set[str] | None = None  # per riconoscere i nuovi download
        self._syncing_nav = False

        self.toast = Toast()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=260, hexpand=True)
        self.stack.add_named(self._build_library(), "library")
        self.workshop = WorkshopPage(on_use=self.show_in_library,
                                     known_ids=lambda: set(self.cards),
                                     toast=self.toast.pop,
                                     get_hide_mature=lambda: self.settings.workshop_hide_mature,
                                     set_hide_mature=self._set_hide_mature)
        self.stack.add_named(self.workshop, "workshop")
        self.stack.add_named(self._build_monitors(), "monitors")
        self.stack.add_named(self._build_settings(), "settings")
        self.stack.connect("notify::visible-child-name", self._on_page_changed)

        body = Gtk.Box()
        body.append(self._build_sidebar())
        body.append(self.stack)
        overlay = Gtk.Overlay(child=body)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

        # Chiudere la finestra principale chiude l'app: usciamo subito, prima che
        # GTK inizi a smontare i widget (e WebKit) — vedi App._hard_exit
        self.connect("close-request", lambda *_: App._hard_exit(None))
        self._load_monitors()
        self._load_settings_into_widgets()
        self.refresh_library()
        pointerize(self)  # cursore a manina su tutto ciò che è cliccabile
        GLib.timeout_add_seconds(2, self._tick)

    # ============================================================ sidebar

    def _build_sidebar(self) -> Gtk.Widget:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=220)
        side.add_css_class("sidebar")

        # Logo: icona dell'app + scritta "wp-penguin" con i colori del logo
        head = Gtk.Box(spacing=10, margin_start=6, margin_bottom=20)
        head.add_css_class("sidebar-logo")
        head.append(Gtk.Image(icon_name="io.github.wppenguin", pixel_size=40))
        word = Gtk.Box(valign=Gtk.Align.CENTER)
        word.append(label("wp", "logo-wp"))
        word.append(label("-", "logo-dash"))
        word.append(label("penguin", "logo-name"))
        head.append(word)
        side.append(head)

        self.nav = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.new_count = label("", "count-badge", visible=False, hexpand=True,
                               halign=Gtk.Align.END)
        for name, icon, text in (("library", "view-grid-symbolic", "Libreria"),
                                 ("new", "starred-symbolic", "Nuovi"),
                                 ("workshop", "web-browser-symbolic", "Workshop"),
                                 ("monitors", "video-display-symbolic", "Monitor"),
                                 ("settings", "emblem-system-symbolic", "Impostazioni")):
            box = Gtk.Box(spacing=10)
            box.append(Gtk.Image(icon_name=icon))
            box.append(label(text))
            if name == "new":
                box.append(self.new_count)
            row = Gtk.ListBoxRow(child=box, name=name)
            row.set_cursor_from_name("pointer")
            self.nav.append(row)
        self.nav.connect("row-selected", self._on_nav)
        side.append(self.nav)

        side.append(Gtk.Box(vexpand=True))
        # Riquadro "in riproduzione" in basso
        self.now_playing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.now_playing.add_css_class("now-playing")
        side.append(self.now_playing)
        return side

    NAV = ("library", "new", "workshop", "monitors", "settings")

    def goto(self, page: str) -> None:
        if page in self.NAV:
            self.nav.select_row(self.nav.get_row_at_index(self.NAV.index(page)))

    def _on_nav(self, _lb, row) -> None:
        if row is None or self._syncing_nav:
            return
        name = row.get_name()
        if name == "new":
            # «Nuovi» è la Libreria con il filtro Nuovi (ordinata per data)
            self.stack.set_visible_child_name("library")
            self.type_seg.set_selected(NEW_FILTER_INDEX, notify=True)
            return
        if name == "library" and self.type_seg.selected == NEW_FILTER_INDEX:
            self.type_seg.set_selected(0, notify=True)
        self.stack.set_visible_child_name(name)

    def _on_type_filter(self, *_a) -> None:
        new_mode = self.type_seg.selected == NEW_FILTER_INDEX
        # Nuovi: dal più recente; altrimenti l'ordine alfabetico della scansione
        self.flow.set_sort_func(self._sort_newest if new_mode else None)
        self.flow.invalidate_filter()
        self._update_lib_subtitle()
        # Tieni allineata la voce selezionata nella sidebar
        want = "new" if new_mode else "library"
        cur = self.nav.get_selected_row()
        if self.stack.get_visible_child_name() == "library" and cur and cur.get_name() != want:
            self._syncing_nav = True
            self.nav.select_row(self.nav.get_row_at_index(self.NAV.index(want)))
            self._syncing_nav = False

    @staticmethod
    def _sort_newest(a: "Card", b: "Card") -> int:
        return (b.wp.added > a.wp.added) - (b.wp.added < a.wp.added)

    def _on_page_changed(self, *_a) -> None:
        if self.stack.get_visible_child_name() == "monitors":
            self._rebuild_monitor_cards()

    def _tick(self) -> bool:
        """Aggiornamento periodico dello stato (processi in esecuzione/in pausa)."""
        self._update_now_playing()
        if self.stack.get_visible_child_name() == "monitors":
            self._update_monitor_status()
        return True

    def _status_of(self, monitor: str, running: dict[str, int]) -> tuple[str, str]:
        """(classe del pallino, testo) per un monitor."""
        if monitor in self.settings.disabled_monitors:
            return "", "Disattivato"
        pid = running.get(monitor)
        if not pid:
            return "", ("Fermato a mano" if monitor in self.settings.stopped_monitors
                        else "Fermo")
        if engine.is_paused(pid):
            return "paused", "In pausa · finestra in primo piano"
        return "on", "In riproduzione"

    def _update_now_playing(self) -> None:
        box = self.now_playing
        while (c := box.get_first_child()) is not None:
            box.remove(c)
        box.append(label("SFONDI", "section-title", margin_top=0, margin_start=0))
        running = engine.running()
        for m in self.monitors:
            dot_cls, text = self._status_of(m.name, running)
            wid = self.settings.monitors.get(m.name)
            wp = next((w for w in self.wallpapers if w.workshop_id == wid), None)
            line = Gtk.Box(spacing=8)
            line.append(Gtk.Box(css_classes=["status-dot"] + ([dot_cls] if dot_cls else []),
                                valign=Gtk.Align.CENTER))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            texts.append(label(m.name, "row-title"))
            texts.append(label(wp.title if (wp and running.get(m.name)) else text,
                               "caption-text", ellipsize=Pango.EllipsizeMode.END, max_chars=22))
            line.append(texts)
            box.append(line)

    # ============================================================ libreria

    def _build_library(self) -> Gtk.Widget:
        header, self.lib_subtitle = page_header("Libreria", "Lettura del Workshop…")
        self.search = Gtk.SearchEntry(placeholder_text="Cerca", width_chars=18,
                                      valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda *_: self.flow.invalidate_filter())
        self.type_seg = Segmented([t for t, _ in TYPE_FILTERS])
        self.type_seg.connect("changed", self._on_type_filter)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Rileggi il Workshop",
                             css_classes=["flat"], valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_: (self._load_monitors(), self.refresh_library()))

        browse = pill("Workshop", icon="web-browser-symbolic", valign=Gtk.Align.CENTER,
                      tooltip_text="Trova nuovi wallpaper nel Workshop di Steam")
        browse.connect("clicked", lambda *_: self.goto("workshop"))
        self.search.set_hexpand(True)
        self.search.set_width_chars(10)

        # Due righe: così la barra sta anche in una finestra stretta (tiling)
        row1 = Gtk.Box(spacing=10)
        row1.append(header)
        row1.append(refresh)
        row1.append(browse)
        row2 = Gtk.Box(spacing=10, margin_top=12)
        row2.append(self.search)
        row2.append(self.type_seg)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.add_css_class("page")
        top.append(row1)
        top.append(row2)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                valign=Gtk.Align.START, max_children_per_line=10,
                                min_children_per_line=1, row_spacing=10, column_spacing=10,
                                activate_on_single_click=False)
        self.flow.add_css_class("wall-grid")
        self.flow.set_filter_func(self._filter)
        self.flow.connect("selected-children-changed", self._on_selection)
        self.flow.connect("child-activated", lambda *_: self.apply_selected())  # doppio clic
        scroller = Gtk.ScrolledWindow(child=self.flow, vexpand=True,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER, margin_top=12)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        main.append(top)
        main.append(scroller)

        # Pannello di dettaglio flottante sopra la griglia (come i pannelli di
        # macOS): non ruba larghezza, quindi funziona anche con finestre strette
        self.inspector = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                      transition_duration=320, child=self._build_inspector(),
                                      halign=Gtk.Align.END)
        page = Gtk.Overlay(child=main)
        page.add_overlay(self.inspector)
        return page

    def _build_inspector(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=344)
        box.add_css_class("inspector")
        close = Gtk.Button(icon_name="window-close-symbolic", halign=Gtk.Align.END,
                           css_classes=["flat", "circular"], tooltip_text="Chiudi")
        close.connect("clicked", lambda *_: (self.flow.unselect_all(),
                                             self.inspector.set_reveal_child(False)))
        box.append(close)
        hero_frame, self.hero = rounded_picture(HERO_W, HERO_H, "hero")
        box.append(hero_frame)
        self.insp_title = label("", "inspector-title", wrap=True, max_chars=28, margin_top=6)
        box.append(self.insp_title)
        self.insp_info = label("", "dim", wrap=True, max_chars=36, selectable=True)
        box.append(self.insp_info)
        ws = Gtk.Button(css_classes=["flat"], halign=Gtk.Align.START,
                        tooltip_text="Pagina del wallpaper: commenti, valutazioni, autore")
        ws_box = Gtk.Box(spacing=6)
        ws_box.append(Gtk.Image(icon_name="web-browser-symbolic"))
        ws_box.append(Gtk.Label(label="Apri nel Workshop"))
        ws.set_child(ws_box)
        ws.connect("clicked", lambda *_: self._open_selected_in_workshop())
        box.append(ws)

        g = Group("Mostra su")
        self.monitor_dd = Gtk.DropDown.new_from_strings([])
        self.monitor_dd.connect("notify::selected", self._on_monitor_dd)
        g.add(Row("Monitor", suffix=self.monitor_dd, icon="video-display-symbolic"))
        box.append(g)

        box.append(Gtk.Box(vexpand=True))
        self.apply_btn = pill("Imposta come sfondo", "accent", icon="emblem-ok-symbolic")
        self.apply_btn.connect("clicked", lambda *_: self.apply_selected())
        box.append(self.apply_btn)
        self.apply_all_btn = pill("Applica a tutti i monitor", icon="video-display-symbolic")
        self.apply_all_btn.connect("clicked", lambda *_: self.apply_selected(all_monitors=True))
        box.append(self.apply_all_btn)
        stop = pill("Ferma su questo monitor", "danger")
        stop.connect("clicked", lambda *_: self.stop_monitor(self.current_monitor))
        box.append(stop)
        return box

    def refresh_library(self) -> None:
        self.lib_subtitle.set_label("Lettura del Workshop…")

        def work():
            wps = library.scan(self.settings.extra_library_paths)
            GLib.idle_add(self._populate, wps)

        threading.Thread(target=work, daemon=True).start()

    def _populate(self, wps: list[library.Wallpaper]) -> bool:
        self.flow.remove_all()
        self.cards.clear()
        self.wallpapers = wps
        scale = self.get_scale_factor() or 1
        broken = engine.incompatible()
        for wp in wps:
            card = Card(wp, broken.get(wp.workshop_id))
            self.cards[wp.workshop_id] = card
            self.flow.append(card)
            if wp.preview:
                self.pool.submit(self._thumb_job, card, wp.preview, scale)

        self._update_lib_subtitle()
        self.flow.invalidate_sort()
        self._mark_active()
        self._update_now_playing()
        self._watch_workshop_dirs()
        # Nuovi wallpaper rispetto alla scansione precedente → notifica
        ids = {w.workshop_id for w in wps}
        if self._known_before is not None:
            new = [w for w in wps if w.workshop_id not in self._known_before]
            if len(new) == 1:
                self.toast.pop(f"Nuovo wallpaper scaricato: «{new[0].title}» · lo trovi in Nuovi", 6)
            elif new:
                self.toast.pop(f"{len(new)} nuovi wallpaper scaricati · li trovi in Nuovi", 6)
        self._known_before = ids
        self.workshop.refresh_info()
        return False

    # ---------------------------------------- aggiornamento automatico

    def _watch_workshop_dirs(self) -> None:
        """Osserva le cartelle del Workshop: quando Steam scarica (o rimuove)
        un wallpaper la Libreria si aggiorna da sola."""
        for d in library.workshop_dirs(self.settings.extra_library_paths):
            key = str(d)
            if key in self._dir_monitors:
                continue
            mon = Gio.File.new_for_path(key).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            mon.connect("changed", self._on_workshop_changed)
            self._dir_monitors[key] = mon

    def _on_workshop_changed(self, _mon, _file, _other, event) -> None:
        if event not in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED,
                         Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.MOVED_OUT):
            return
        # Steam crea la cartella e poi scrive i file: aspettiamo che si calmi
        if self._rescan_timeout:
            GLib.source_remove(self._rescan_timeout)
        self._rescan_timeout = GLib.timeout_add_seconds(3, self._rescan)

    def _rescan(self) -> bool:
        self._rescan_timeout = 0
        self.refresh_library()
        return False

    def _set_hide_mature(self, hide: bool) -> None:
        self.settings.workshop_hide_mature = hide
        config.save(self.settings)

    def show_in_library(self, workshop_id: str) -> None:
        """Dal Workshop: mostra il wallpaper nella Libreria con il pannello aperto."""
        card = self.cards.get(workshop_id)
        if not card:
            return
        self.search.set_text("")
        self.type_seg.set_selected(0, notify=True)
        self.goto("library")
        self.flow.select_child(card)
        GLib.idle_add(lambda: card.grab_focus() and False)  # scorre fino alla card

    def _open_selected_in_workshop(self) -> None:
        wp = self._selected()
        if wp:
            self.workshop.open_item(wp.workshop_id)
            self.goto("workshop")

    def _update_lib_subtitle(self) -> None:
        wps = self.wallpapers
        n_new = sum(c.is_new for c in self.cards.values())
        self.new_count.set_label(str(n_new))
        self.new_count.set_visible(n_new > 0)
        if not library.workshop_dirs(self.settings.extra_library_paths):
            self.lib_subtitle.set_label("Nessuna cartella del Workshop trovata · "
                                        "aggiungila da Impostazioni")
        elif self.type_seg.selected == NEW_FILTER_INDEX:
            recent = sum(bool(w.added) and time.time() - w.added <= NEW_LIST_DAYS * 86400
                         for w in wps)
            self.lib_subtitle.set_label(
                f"{recent} arrivati negli ultimi {NEW_LIST_DAYS} giorni, dal più recente"
                if recent else "Nessun wallpaper nuovo: iscriviti a qualcosa nel Workshop")
        else:
            bad = sum(not w.supported for w in wps)
            self.lib_subtitle.set_label(f"{len(wps)} wallpaper"
                                        + (f" · {n_new} nuovi" if n_new else "")
                                        + (f" · {bad} non supportati" if bad else ""))

    def _thumb_job(self, card: Card, path: Path, scale: int) -> None:
        pb = load_thumb(path, THUMB_W * scale, THUMB_H * scale)
        if pb:
            def done():
                tex = pixbuf_to_texture(pb)
                self.thumb_cache[card.wp.workshop_id] = tex
                card.set_texture(tex)
                return False
            GLib.idle_add(done)

    def _filter(self, card: Card) -> bool:
        wanted = TYPE_FILTERS[self.type_seg.selected][1]
        if wanted == "new":
            if not card.wp.added or time.time() - card.wp.added > NEW_LIST_DAYS * 86400:
                return False
        elif wanted and card.wp.type != wanted:
            return False
        q = self.search.get_text().strip().casefold()
        return not q or q in card.wp.title.casefold() or q in card.wp.workshop_id

    def _mark_active(self) -> None:
        running = engine.running()
        by_wp: dict[str, list[str]] = {}
        for mon, wid in self.settings.monitors.items():
            if mon in running:
                by_wp.setdefault(wid, []).append(mon)
        for wid, card in self.cards.items():
            card.set_active_on(by_wp.get(wid, []))

    def _selected(self) -> library.Wallpaper | None:
        sel = self.flow.get_selected_children()
        return sel[0].wp if sel else None

    def _on_selection(self, *_a) -> None:
        wp = self._selected()
        if not wp:
            return
        self.inspector.set_reveal_child(True)
        self.insp_title.set_label(wp.title)
        info = f"{wp.type.capitalize()} · ID {wp.workshop_id}"
        if wp.is_preset:
            info += f"\nPreset di {wp.path.name} · {len(wp.properties)} proprietà personalizzate"
        if not wp.supported:
            info += "\n⚠ Tipo non supportato da linux-wallpaperengine"
        card = self.cards.get(wp.workshop_id)
        if card and card.broken:
            info += f"\n⚠ Ha fatto crashare il motore all'avvio: {card.broken}"
        self.insp_info.set_label(info)
        self.apply_btn.set_sensitive(wp.supported)
        self.apply_all_btn.set_sensitive(wp.supported and len(self._enabled_monitors()) > 1)
        self.hero.set_paintable(self.thumb_cache.get(wp.workshop_id))

        def job(p=wp.preview, wid=wp.workshop_id, scale=self.get_scale_factor() or 1):
            pb = load_thumb(p, HERO_W * scale * 2, HERO_H * scale * 2) if p else None
            if pb:
                GLib.idle_add(self._set_hero, wid, pb)
        self.pool.submit(job)

    def _set_hero(self, wid: str, pb: GdkPixbuf.Pixbuf) -> bool:
        cur = self._selected()
        if cur and cur.workshop_id == wid:  # la selezione potrebbe essere cambiata
            self.hero.set_paintable(pixbuf_to_texture(pb))
        return False

    # ============================================================ monitor

    def _enabled_monitors(self) -> list[hypr.Monitor]:
        return [m for m in self.monitors if m.name not in self.settings.disabled_monitors]

    def _load_monitors(self) -> None:
        try:
            self.monitors = hypr.monitors()
        except (hypr.HyprlandError, OSError) as e:
            self.monitors = []
            self.toast.pop(f"IPC di Hyprland non disponibile: {e}")
        self._refresh_monitor_dropdown()

    def _refresh_monitor_dropdown(self) -> None:
        enabled = self._enabled_monitors()
        self._loading = True
        self.monitor_dd.set_model(Gtk.StringList.new(
            [f"{m.name} · {m.width}×{m.height}" for m in enabled]))
        names = [m.name for m in enabled]
        if self.settings.last_monitor in names:
            self.monitor_dd.set_selected(names.index(self.settings.last_monitor))
        self._loading = False

    @property
    def current_monitor(self) -> str | None:
        enabled = self._enabled_monitors()
        i = self.monitor_dd.get_selected()
        return enabled[i].name if 0 <= i < len(enabled) else None

    def _on_monitor_dd(self, *_a) -> None:
        mon = self.current_monitor
        if not self._loading and mon and mon != self.settings.last_monitor:
            self.settings.last_monitor = mon
            config.save(self.settings)

    def _build_monitors(self) -> Gtk.Widget:
        header, _ = page_header("Monitor", "Gli schermi rilevati da Hyprland")
        top = Gtk.Box()
        top.add_css_class("page")
        top.append(header)
        self.mon_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                margin_start=30, margin_end=30, margin_top=18, margin_bottom=30)
        self.mon_status: dict[str, tuple[Gtk.Box, Gtk.Label]] = {}
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(top)
        page.append(Gtk.ScrolledWindow(child=self.mon_list, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))
        return page

    def _rebuild_monitor_cards(self) -> None:
        self._load_monitors()
        while (c := self.mon_list.get_first_child()) is not None:
            self.mon_list.remove(c)
        self.mon_status.clear()
        if not self.monitors:
            self.mon_list.append(label("Nessun monitor rilevato.", "dim"))
        for m in self.monitors:
            self.mon_list.append(self._monitor_card(m))
        self._update_monitor_status()

    def _monitor_card(self, m: hypr.Monitor) -> Gtk.Widget:
        card = Gtk.Box(spacing=22)
        card.add_css_class("monitor-card")
        frame, pic = rounded_picture(224, 126, "screen")
        wid = self.settings.monitors.get(m.name)
        if wid in self.thumb_cache:
            pic.set_paintable(self.thumb_cache[wid])
        card.append(frame)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        col.append(label(m.name, "monitor-name"))
        col.append(label(m.description, "dim", wrap=True))
        col.append(label(f"{m.width}×{m.height} · {m.refresh:.0f} Hz", "caption-text"))
        wp = next((w for w in self.wallpapers if w.workshop_id == wid), None)
        col.append(label(f"Wallpaper: {wp.title}" if wp else "Nessun wallpaper assegnato",
                         "row-title", margin_top=8, ellipsize=Pango.EllipsizeMode.END))
        status = Gtk.Box(spacing=8)
        dot = Gtk.Box(css_classes=["status-dot"], valign=Gtk.Align.CENTER)
        st_label = label("", "caption-text")
        status.append(dot)
        status.append(st_label)
        col.append(status)
        self.mon_status[m.name] = (dot, st_label)

        # FlowBox: con finestre strette (tiling) i pulsanti vanno a capo
        btns = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=8,
                           row_spacing=8, margin_top=10, min_children_per_line=1,
                           max_children_per_line=3, homogeneous=False)
        choose = pill("Scegli dalla libreria", icon="view-grid-symbolic")
        choose.connect("clicked", lambda *_: self._choose_for(m.name))
        restart = pill("Riavvia", icon="view-refresh-symbolic", sensitive=bool(wp))
        restart.connect("clicked", lambda *_: self._restart_monitor(m.name))
        stop = pill("Ferma", "danger")
        stop.connect("clicked", lambda *_: self.stop_monitor(m.name, forget=False))
        for b in (choose, restart, stop):
            btns.append(b)
        col.append(btns)
        card.append(col)

        sw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        sw = Gtk.Switch(active=m.name not in self.settings.disabled_monitors,
                        halign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda s, _p: self._set_monitor_enabled(m.name, s.get_active()))
        sw_box.append(sw)
        sw_box.append(label("Animato", "caption-text", xalign=0.5))
        card.append(sw_box)
        pointerize(card)
        return card

    def _update_monitor_status(self) -> None:
        running = engine.running()
        for name, (dot, lbl) in self.mon_status.items():
            cls, text = self._status_of(name, running)
            for c in ("on", "paused"):
                dot.remove_css_class(c)
            if cls:
                dot.add_css_class(cls)
            lbl.set_label(text)

    def _choose_for(self, monitor: str) -> None:
        self.settings.last_monitor = monitor
        config.save(self.settings)
        self._refresh_monitor_dropdown()
        self.goto("library")
        self.toast.pop(f"Scegli un wallpaper per {monitor} (doppio clic per applicarlo)")

    def _restart_monitor(self, monitor: str) -> None:
        s = self.settings
        s.stopped_monitors = [m for m in s.stopped_monitors if m != monitor]
        config.save(s)
        self._run_bg(f"Riavvio su {monitor}…",
                     lambda: engine.restore(s, only={monitor}, include_stopped=True),
                     lambda errs: self.error("Riavvio non riuscito", "\n".join(errs))
                     if errs else self._after_change(f"Wallpaper riavviato su {monitor}"))

    def _set_monitor_enabled(self, monitor: str, enabled: bool) -> None:
        s = self.settings
        if enabled:
            s.disabled_monitors = [m for m in s.disabled_monitors if m != monitor]
        elif monitor not in s.disabled_monitors:
            s.disabled_monitors.append(monitor)
        config.save(s)
        self._refresh_monitor_dropdown()
        if not enabled:
            self._run_bg(f"Arresto su {monitor}…", lambda: engine.stop(monitor),
                         lambda _r: self._after_change(f"Wallpaper animati disattivati su {monitor}"))
        elif monitor in s.monitors:
            self._restart_monitor(monitor)
        self._update_monitor_status()

    # ========================================================= impostazioni

    def _build_settings(self) -> Gtk.Widget:
        header, _ = page_header("Impostazioni", "Riproduzione, sistema e aspetto")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER,
                      width_request=640, margin_bottom=40)
        col.append(header)

        # --- riproduzione
        play = Group("Riproduzione", "FPS, volume, scaling e mouse valgono dal prossimo avvio "
                                     "del wallpaper: usa «Riapplica ora» per vederli subito.")
        self.fps_spin = Gtk.SpinButton.new_with_range(1, 240, 1)
        play.add(Row("FPS massimi", suffix=self.fps_spin, icon="speedometer-symbolic"))
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_scale.set_size_request(220, -1)
        self.volume_scale.set_draw_value(True)
        self.volume_scale.set_value_pos(Gtk.PositionType.LEFT)
        play.add(Row("Volume", suffix=self.volume_scale, icon="audio-volume-high-symbolic",
                     icon_class="green"))
        self.mute_sw = Gtk.Switch()
        play.add(Row("Muto", suffix=self.mute_sw, icon="audio-volume-muted-symbolic",
                     icon_class="gray"))
        # Stesso ordine di config.SCALING_MODES
        self.scaling_dd = Gtk.DropDown.new_from_strings(
            ["Predefinito", "Stira", "Adatta", "Riempi"])
        play.add(Row("Scaling", "Come adattare il wallpaper allo schermo", suffix=self.scaling_dd,
                     icon="view-fullscreen-symbolic", icon_class="gray"))
        self.mouse_sw = Gtk.Switch()
        play.add(Row("Disattiva mouse", "Niente interazione e parallasse",
                     suffix=self.mouse_sw, icon="input-mouse-symbolic", icon_class="gray"))
        reapply = pill("Riapplica ora")
        reapply.connect("clicked", lambda *_: self.reapply_all())
        play.add(Row("Riavvia i wallpaper attivi con queste opzioni", suffix=reapply))
        col.append(play)

        # --- risparmio
        power = Group("Risparmio energetico")
        self.pause_sw = Gtk.Switch()
        power.add(Row("Pausa con schermo intero",
                      "Congela il wallpaper del monitor che ha una finestra a schermo intero. "
                      "Effetto immediato.", suffix=self.pause_sw,
                      icon="media-playback-pause-symbolic", icon_class="orange"))
        self.pause_win_sw = Gtk.Switch()
        power.add(Row("Pausa con finestre aperte",
                      "Congela il wallpaper appena sul monitor c'è una finestra, anche "
                      "flottante; riparte quando il desktop è libero. Massimo risparmio.",
                      suffix=self.pause_win_sw, icon="window-new-symbolic", icon_class="orange"))
        col.append(power)

        # --- affidabilità
        safety = Group("Affidabilità", "Lo sfondo statico sta sotto quello animato: se "
                                       "l'animazione crasha o la fermi, vedi l'immagine "
                                       "giusta invece del vecchio sfondo.")
        self.static_sw = Gtk.Switch()
        self.static_row = safety.add(Row("Imposta anche come sfondo statico",
                                         "", suffix=self.static_sw,
                                         icon="image-x-generic-symbolic", icon_class="green"))
        self.autorestart_sw = Gtk.Switch()
        safety.add(Row("Riavvia se si blocca",
                       "Se il wallpaper animato termina da solo viene riavviato "
                       "(massimo 3 volte in 5 minuti)", suffix=self.autorestart_sw,
                       icon="view-refresh-symbolic", icon_class="green"))
        col.append(safety)

        # --- sistema
        system = Group("Sistema")
        self.autostart_sw = Gtk.Switch()
        self.autostart_row = system.add(Row("Avvia all'accensione", "", suffix=self.autostart_sw,
                                            icon="system-run-symbolic", icon_class="green"))
        self.conflict_sw = Gtk.Switch()
        system.add(Row("Chiudi hyprpaper / swww / awww",
                       "Di solito non serve: il wallpaper animato sta su un livello sopra di "
                       "loro. Ignorato se lo sfondo statico è attivo.",
                       suffix=self.conflict_sw, icon="process-stop-symbolic", icon_class="orange"))
        col.append(system)

        # --- aspetto
        look = Group("Aspetto", "Lo stile è in style.css: «Personalizza» ne crea una copia in "
                                "~/.config/wp-penguin/ che viene ricaricata a ogni salvataggio.")
        self.scheme_seg = Segmented(["Automatico", "Chiaro", "Scuro"])
        look.add(Row("Tema", suffix=self.scheme_seg, icon="weather-clear-night-symbolic",
                     icon_class="gray"))
        self.transp_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 70, 5)
        self.transp_scale.set_size_request(220, -1)
        self.transp_scale.set_draw_value(True)
        self.transp_scale.set_value_pos(Gtk.PositionType.LEFT)
        self.transp_scale.set_format_value_func(lambda _s, v: f"{v:.0f}%")
        for mark in (0, 20, 50):
            self.transp_scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        look.add(Row("Trasparenza", "Effetto vetro: Hyprland sfoca ciò che sta dietro "
                     "la finestra. 0% = opaca", suffix=self.transp_scale,
                     icon="view-reveal-symbolic", icon_class="gray"))
        edit_css = pill("Personalizza")
        edit_css.connect("clicked", lambda *_: self._edit_css())
        look.add(Row("Foglio di stile", str(appearance.USER_CSS), suffix=edit_css,
                     icon="applications-graphics-symbolic"))
        col.append(look)

        # --- steam
        self.steam_group = Group("Steam e Workshop")
        col.append(self.steam_group)

        # --- avanzate
        adv = Group("Avanzate")
        wiz = pill("Apri")
        wiz.connect("clicked", lambda *_: self.app.open_wizard(parent=self))
        adv.add(Row("Procedura guidata", "Rifai la configurazione iniziale", suffix=wiz,
                    icon="starred-symbolic", icon_class="orange"))
        logs = pill("Apri")
        logs.connect("clicked", lambda *_: self._open_path(engine.LOG_DIR, folder=True))
        adv.add(Row("Log di linux-wallpaperengine", str(engine.LOG_DIR), suffix=logs,
                    icon="text-x-generic-symbolic", icon_class="gray"))
        col.append(adv)

        # Collegamenti dei segnali (dopo aver creato tutti i widget)
        for w, sig in ((self.fps_spin, "value-changed"), (self.volume_scale, "value-changed"),
                       (self.mute_sw, "notify::active"), (self.pause_sw, "notify::active"),
                       (self.scaling_dd, "notify::selected"), (self.mouse_sw, "notify::active"),
                       (self.conflict_sw, "notify::active"), (self.pause_win_sw, "notify::active"),
                       (self.static_sw, "notify::active"), (self.autorestart_sw, "notify::active")):
            w.connect(sig, self._on_option_changed)
        self.mute_sw.connect("notify::active",
                             lambda sw, _p: self.volume_scale.set_sensitive(not sw.get_active()))
        self.autostart_sw.connect("notify::active", self._on_autostart)
        self.scheme_seg.connect("changed", self._on_scheme)
        self.transp_scale.connect("value-changed", self._on_transparency)

        scroller = Gtk.ScrolledWindow(child=col, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("page")
        return scroller

    def _load_settings_into_widgets(self) -> None:
        s = self.settings
        self._loading = True
        self.fps_spin.set_value(s.fps)
        self.volume_scale.set_value(s.volume)
        self.mute_sw.set_active(s.muted)
        self.volume_scale.set_sensitive(not s.muted)
        self.pause_sw.set_active(s.pause_on_fullscreen)
        self.scaling_dd.set_selected(config.SCALING_MODES.index(s.scaling))
        self.mouse_sw.set_active(s.disable_mouse)
        self.conflict_sw.set_active(s.kill_conflicting)
        self.pause_win_sw.set_active(s.pause_on_windows)
        self.static_sw.set_active(s.static_wallpaper)
        self.autorestart_sw.set_active(s.autorestart)
        sb = static.backend()
        self.static_row.set_subtitle(
            f"Un fotogramma del wallpaper animato viene impostato con {sb}" if sb else
            "Nessun gestore di sfondi statici attivo (Ambxst, awww, swww o hyprpaper)")
        self.autostart_row.set_subtitle("")  # ricalcolato sotto
        self.scheme_seg.set_selected(appearance.COLOR_SCHEMES.index(s.color_scheme))
        self.transp_scale.set_value(s.transparency)
        path = autostart.config_file()
        self.autostart_sw.set_active(autostart.is_enabled(path))
        self.autostart_sw.set_sensitive(path.exists())
        self.autostart_row.set_subtitle(
            f"Ripristina il wallpaper a ogni accesso · {path}" if path.exists()
            else f"{path} non trovato")
        self._loading = False
        self._refresh_steam_group()

    def _refresh_steam_group(self) -> None:
        g = self.steam_group
        g.clear()
        for d in library.workshop_dirs(self.settings.extra_library_paths):
            g.add(Row("Workshop", str(d), icon="folder-symbolic"))
        assets = library.find_assets_dir(self.settings.extra_library_paths)
        g.add(Row("Assets di Wallpaper Engine", str(assets) if assets else "Non trovati",
                  icon="folder-symbolic", icon_class="green" if assets else "gray"))
        for p in self.settings.extra_library_paths:
            rm = Gtk.Button(icon_name="list-remove-symbolic", css_classes=["flat"],
                            tooltip_text="Rimuovi")
            rm.connect("clicked", lambda _b, p=p: self._remove_path(p))
            g.add(Row("Cartella aggiunta a mano", p, suffix=rm, icon="folder-open-symbolic",
                      icon_class="orange"))
        add = pill("Aggiungi…")
        add.connect("clicked", lambda *_: self._pick_folder())
        g.add(Row("Aggiungi una libreria o una cartella Workshop", suffix=add))

    def _on_option_changed(self, *_a) -> None:
        if self._loading:
            return
        s = self.settings
        s.fps = int(self.fps_spin.get_value())
        s.volume = int(self.volume_scale.get_value())
        s.muted = self.mute_sw.get_active()
        s.pause_on_fullscreen = self.pause_sw.get_active()
        s.scaling = config.SCALING_MODES[self.scaling_dd.get_selected()]
        s.disable_mouse = self.mouse_sw.get_active()
        s.kill_conflicting = self.conflict_sw.get_active()
        s.pause_on_windows = self.pause_win_sw.get_active()
        s.static_wallpaper = self.static_sw.get_active()
        s.autorestart = self.autorestart_sw.get_active()
        config.save(s)  # il daemon la rilegge da solo

    def _on_scheme(self, _seg, i: int) -> None:
        self.settings.color_scheme = appearance.COLOR_SCHEMES[i]
        appearance.apply_color_scheme(self.settings.color_scheme)
        config.save(self.settings)

    def _on_transparency(self, scale: Gtk.Scale) -> None:
        if self._loading:
            return
        # Anteprima immediata; il salvataggio su disco è ritardato (lo slider
        # emette molti eventi mentre lo trascini)
        self.settings.transparency = int(scale.get_value())
        appearance.apply_transparency(self.settings.transparency)
        if getattr(self, "_transp_save", 0):
            GLib.source_remove(self._transp_save)
        self._transp_save = GLib.timeout_add(400, self._save_transparency)

    def _save_transparency(self) -> bool:
        self._transp_save = 0
        config.save(self.settings)
        return False

    def _on_autostart(self, sw: Gtk.Switch, _p) -> None:
        if self._loading:
            return
        try:
            bak = autostart.enable() if sw.get_active() else autostart.disable()
        except OSError as e:
            self._loading = True
            sw.set_active(not sw.get_active())
            self._loading = False
            self.error("Impossibile modificare la configurazione di Hyprland", str(e))
            return
        what = "aggiunto" if sw.get_active() else "rimosso"
        self.toast.pop(f"Avvio automatico {what}" + (f" · backup: {bak.name}" if bak else ""), 5)

    def _pick_folder(self) -> None:
        dialog = Gtk.FileDialog(title="Libreria Steam o cartella …/workshop/content/431960")

        def done(dlg, result):
            try:
                path = dlg.select_folder_finish(result).get_path()
            except GLib.Error:
                return  # annullato
            s = self.settings
            if not path or path in s.extra_library_paths:
                return
            before = len(library.workshop_dirs(s.extra_library_paths))
            s.extra_library_paths.append(path)
            if len(library.workshop_dirs(s.extra_library_paths)) <= before:
                s.extra_library_paths.remove(path)
                self.toast.pop("In quella cartella non c'è un Workshop di Wallpaper Engine")
                return
            config.save(s)
            self._refresh_steam_group()
            self.refresh_library()

        dialog.select_folder(self, None, done)

    def _remove_path(self, p: str) -> None:
        self.settings.extra_library_paths.remove(p)
        config.save(self.settings)
        self._refresh_steam_group()
        self.refresh_library()

    def _edit_css(self) -> None:
        # Copia lo stile predefinito nella config utente (se non c'è) e lo apre
        if not appearance.USER_CSS.exists():
            appearance.create_user_css()
        self._open_path(appearance.USER_CSS)
        self.toast.pop("Modifica e salva: lo stile si aggiorna da solo")

    def _open_path(self, path: Path, folder: bool = False) -> None:
        if folder:
            path.mkdir(parents=True, exist_ok=True)
        Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self, None, None)

    # =============================================================== azioni

    def _run_bg(self, text: str, fn, on_done=None) -> None:
        """Esegue fn() in un thread; errori → finestra di dialogo."""
        self.toast.pop(text, 30)
        self.apply_btn.set_sensitive(False)
        self.apply_all_btn.set_sensitive(False)

        def work():
            try:
                result, err = fn(), None
            except Exception as e:  # noqa: BLE001 - mostriamo qualsiasi errore all'utente
                result, err = None, e
            GLib.idle_add(finish, result, err)

        def finish(result, err):
            sel = self._selected()
            self.apply_btn.set_sensitive(bool(sel and sel.supported))
            self.apply_all_btn.set_sensitive(bool(sel and sel.supported))
            if err:
                self.toast.set_reveal_child(False)
                self.error("Operazione non riuscita", str(err))
                if "non è compatibile" in str(err):
                    self.refresh_library()  # mostra il badge «Non compatibile»
            elif on_done:
                on_done(result)
            return False

        threading.Thread(target=work, daemon=True).start()

    def _after_change(self, text: str) -> None:
        self.toast.pop(text)
        self._mark_active()
        self._update_now_playing()
        if self.stack.get_visible_child_name() == "monitors":
            self._rebuild_monitor_cards()

    def apply_selected(self, all_monitors: bool = False) -> None:
        wp, mon = self._selected(), self.current_monitor
        if not wp:
            return
        if not mon:
            self.error("Nessun monitor attivo", "Attiva almeno un monitor nella sezione Monitor.")
            return
        if all_monitors:
            self._apply_to_all(wp)
            return
        if not wp.supported:
            self.error("Wallpaper non supportato",
                       f"I wallpaper di tipo «{wp.type}» non possono essere riprodotti su Linux.")
            return
        s = self.settings

        def done(_pid):
            s.monitors[mon] = wp.workshop_id
            s.last_monitor = mon
            s.stopped_monitors = [m for m in s.stopped_monitors if m != mon]
            config.save(s)
            self._after_change(f"«{wp.title}» ora è lo sfondo di {mon}")
            ensure_daemon()

        self._run_bg(f"Avvio di «{wp.title}» su {mon}…", lambda: engine.start(s, mon, wp), done)

    def _apply_to_all(self, wp: library.Wallpaper) -> None:
        """Stesso wallpaper su tutti i monitor attivi (uno dopo l'altro)."""
        if not wp.supported:
            self.error("Wallpaper non supportato",
                       f"I wallpaper di tipo «{wp.type}» non possono essere riprodotti su Linux.")
            return
        s = self.settings
        targets = [m.name for m in self._enabled_monitors()]

        def work():
            done, errors = [], []
            for mon in targets:
                try:
                    engine.start(s, mon, wp)
                    done.append(mon)
                except engine.EngineError as e:
                    errors.append(f"{mon}: {e}")
                    if "non è compatibile" in str(e):
                        break  # inutile riprovare sugli altri monitor
            return done, errors

        def finished(result):
            done, errors = result
            for mon in done:
                s.monitors[mon] = wp.workshop_id
            s.stopped_monitors = [m for m in s.stopped_monitors if m not in done]
            config.save(s)
            if errors:
                self.error("Non applicato su tutti i monitor", "\n\n".join(errors))
                self.refresh_library()
            if done:
                self._after_change(f"«{wp.title}» ora è lo sfondo di {', '.join(done)}")
                ensure_daemon()

        self._run_bg(f"Avvio di «{wp.title}» su {len(targets)} monitor…", work, finished)

    def reapply_all(self) -> None:
        self._run_bg("Riavvio dei wallpaper con le nuove opzioni…",
                     lambda: engine.restore(self.settings),
                     lambda errs: self.error("Alcuni wallpaper non sono ripartiti", "\n".join(errs))
                     if errs else self._after_change("Opzioni applicate"))

    def stop_monitor(self, mon: str | None, forget: bool = True) -> None:
        if not mon:
            return

        s = self.settings
        if not forget and mon not in s.stopped_monitors:
            # Prima di fermarlo: così il watchdog del daemon non lo riavvia
            s.stopped_monitors.append(mon)
            config.save(s)

        def done(_r):
            if forget:  # non verrà più ripristinato all'avvio
                s.monitors.pop(mon, None)
                s.stopped_monitors = [m for m in s.stopped_monitors if m != mon]
                config.save(s)
            self._after_change(f"Wallpaper fermato su {mon}")

        self._run_bg(f"Arresto su {mon}…", lambda: engine.stop(mon), done)

    def error(self, title: str, detail: str) -> None:
        Gtk.AlertDialog(message=title, detail=detail).show(self)

    def reload_all(self) -> None:
        """Dopo la procedura guidata: rilegge tutto dalla configurazione."""
        self.settings = config.load()
        appearance.apply_color_scheme(self.settings.color_scheme)
        appearance.apply_transparency(self.settings.transparency)
        self._load_monitors()
        self._load_settings_into_widgets()
        self.refresh_library()
        if self.stack.get_visible_child_name() == "monitors":
            self._rebuild_monitor_cards()


# ------------------------------------------------------------------- app

def ensure_daemon() -> None:
    """Avvia il daemon (solo pausa e watchdog, senza ripristino) se non c'è già,
    staccato dalla GUI così continua a girare dopo la chiusura. Se quello in
    esecuzione è più vecchio del codice installato lo sostituisce."""
    pid = daemon.running_pid()
    if pid:
        try:
            started = os.stat(f"/proc/{pid}").st_ctime  # ≈ avvio del processo
        except OSError:
            started = 0.0
        if started >= daemon.code_mtime():
            return
        os.kill(pid, signal.SIGTERM)  # il daemon riprende i wallpaper in pausa ed esce
        for _ in range(30):
            if not daemon.is_running():
                break
            time.sleep(0.1)
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    subprocess.Popen([sys.executable, "-m", "wp_penguin", "--watch"], cwd=pkg_parent,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


class App(Gtk.Application):
    def __init__(self, force_wizard: bool = False) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.force_wizard = force_wizard
        self.main_window: MainWindow | None = None
        # Il gestore del segnale gira PRIMA della pulizia di GTK (che distrugge
        # le finestre e quindi WebKit): usciamo da qui, vedi _hard_exit.
        self.connect("shutdown", self._hard_exit)

    def quit(self) -> None:  # noqa: A003 - stesso nome di Gio.Application.quit
        self._hard_exit(self)

    @staticmethod
    def _hard_exit(_app) -> None:
        """Uscita senza distruggere WebKitGTK: smontare la WebView (o lasciarlo
        fare alla finalizzazione di Python) può mandare in crash il processo
        con «free(): corrupted unsorted chunks». A questo punto tutto è già
        salvato su disco; aspettiamo solo i thread dello sfondo statico."""
        for th in threading.enumerate():
            if th.name.startswith("static-"):
                th.join(timeout=25)
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        # Icona dell'app dal repository (funziona anche senza install.sh)
        Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
            str(Path(__file__).resolve().parent.parent / "data/icons"))
        Gtk.Window.set_default_icon_name(APP_ID)
        appearance.install_css()
        s = config.load()
        appearance.apply_color_scheme(s.color_scheme)
        appearance.apply_transparency(s.transparency)

    def do_activate(self) -> None:
        # Gtk.Application è single-instance: un secondo avvio riporta in primo piano
        if self.main_window:
            self.main_window.present()
            return
        if self.force_wizard or not config.load().first_run_done:
            self.force_wizard = False
            self.open_wizard()
        else:
            self.open_main()

    def open_main(self) -> None:
        self.main_window = MainWindow(self)
        self.main_window.present()
        self.main_window.goto("library")
        self._restore_missing(self.main_window)

    def open_wizard(self, parent: Gtk.Window | None = None) -> None:
        from .wizard import Wizard

        def finished(_s: config.Settings) -> None:
            if self.main_window:
                self.main_window.reload_all()
            else:
                self.open_main()

        Wizard(self, finished, parent).present()

    def _restore_missing(self, win: MainWindow) -> None:
        """Ripristino all'avvio: rilancia i wallpaper salvati che non sono attivi
        (se il daemon li ha già ripristinati non li tocca, niente sfarfallio)."""
        s = win.settings
        running = engine.running()
        missing = {m for m in s.monitors if m not in running and m not in s.disabled_monitors}
        if not engine.engine_binary(s):
            win.toast.pop(f"«{s.binary}» non trovato: installa linux-wallpaperengine-git "
                          "dall'AUR (vedi la procedura guidata)", 8)
        elif missing:
            win._run_bg("Ripristino dell'ultima scelta…", lambda: engine.restore(s, only=missing),
                        lambda errs: win.toast.pop("Ripristino: " + "; ".join(errs), 8)
                        if errs else win._after_change("Ultima scelta ripristinata"))
        ensure_daemon()


def main(wizard: bool = False) -> int:
    return App(force_wizard=wizard).run([])
