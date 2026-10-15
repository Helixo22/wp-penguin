# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pagina «Workshop»: il Workshop di Wallpaper Engine dentro l'app.

Usa WebKitGTK 6 (pacchetto webkitgtk-6.0) per mostrare
steamcommunity.com/app/431960/workshop. Il login a Steam resta salvato in
~/.local/share/wp-penguin/webkit, così ti iscrivi ai wallpaper direttamente da
qui: il client Steam (che deve essere aperto) li scarica e la Libreria li
mostra da sola, grazie al monitoraggio della cartella del Workshop in gui.py.

Senza WebKitGTK la pagina ripiega su pulsanti che aprono il Workshop in Steam
o nel browser.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import gi
from gi.repository import Gio, GLib, Gtk

from .widgets import label, page_header, pill

try:
    gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
except (ValueError, ImportError):
    WebKit = None

WORKSHOP_URL = "https://steamcommunity.com/app/431960/workshop/"
ITEM_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id={}"
STEAM_WORKSHOP_URI = "steam://url/SteamWorkshopPage/431960"
STEAM_ITEM_URI = "steam://url/CommunityFilePage/{}"

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "wp-penguin/webkit"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wp-penguin/webkit"

BROWSE_URL = ("https://steamcommunity.com/workshop/browse/?appid=431960"
              "&browsesort=trend&section=readytouseitems")
MATURE_TAG = "Mature"

_ID_RE = re.compile(r"[?&]id=(\d+)")

# Script iniettato nelle pagine dei singoli wallpaper: se la classificazione
# è "Mature" copre la pagina con un avviso (la ricerca è già filtrata, questo
# copre i link diretti e i suggerimenti).
MATURE_BLOCKER_JS = """
(function () {
  if (!/filedetails/.test(location.href)) return;
  var mature = Array.prototype.some.call(document.querySelectorAll('.workshopTags'), function (el) {
    var title = el.querySelector('.workshopTagsTitle');
    return title && /Age Rating/i.test(title.textContent) && /\\bMature\\b/.test(el.textContent);
  });
  if (!mature) return;
  var cover = document.createElement('div');
  cover.id = 'wpp-mature-cover';
  cover.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:#1b2838;' +
    'display:flex;flex-direction:column;align-items:center;justify-content:center;' +
    'color:#c7d5e0;font:16px Arial,sans-serif;text-align:center;padding:40px';
  var icon = document.createElement('div');
  icon.style.cssText = 'font-size:48px;margin-bottom:12px';
  icon.textContent = '\U0001F51E';
  var title = document.createElement('div');
  title.style.cssText = 'font-size:22px;color:#fff;margin-bottom:8px';
  title.textContent = 'Contenuto per adulti nascosto';
  var text = document.createElement('div');
  text.textContent = 'Questo wallpaper è classificato «Mature». ' +
    'Disattiva «Nascondi Mature» in wp-penguin per vederlo.';
  var back = document.createElement('button');
  back.textContent = 'Torna indietro';
  back.style.cssText = 'margin-top:22px;padding:10px 22px;border:0;border-radius:999px;' +
    'background:#0a84ff;color:#fff;font-size:15px;cursor:pointer';
  back.addEventListener('click', function () {
    if (history.length > 1) history.back(); else location.assign(BROWSE_URL);
  });
  [icon, title, text, back].forEach(function (n) { cover.appendChild(n); });
  document.documentElement.appendChild(cover);
  document.documentElement.style.overflow = 'hidden';
})();
""".replace("BROWSE_URL", "'" + BROWSE_URL + "'")


def with_mature_filter(uri: str) -> str | None:
    """Se uri è una pagina del Workshop di Wallpaper Engine che può mostrare
    contenuti Mature, ritorna l'URI filtrato; altrimenti None (va bene così)."""
    parts = urlsplit(uri)
    if parts.hostname != "steamcommunity.com":
        return None
    path = parts.path.rstrip("/")
    # La home del Workshop ha caroselli non filtrabili: la sostituiamo con la ricerca
    if path == "/app/431960/workshop":
        return with_mature_filter(BROWSE_URL)
    if path != "/workshop/browse":
        return None
    query = parse_qsl(parts.query, keep_blank_values=True)
    if dict(query).get("appid") != "431960":
        return None
    if ("excludedtags[]", MATURE_TAG) in query:
        return None
    # Chi chiede esplicitamente i Mature viene riportato alla ricerca filtrata
    query = [(k, v) for k, v in query if not (k == "requiredtags[]" and v == MATURE_TAG)]
    query.append(("excludedtags[]", MATURE_TAG))
    return urlunsplit(parts._replace(query=urlencode(query)))


def item_id(uri: str | None) -> str | None:
    """ID del Workshop se l'URI è la pagina di un singolo elemento."""
    if uri and "filedetails" in uri:
        m = _ID_RE.search(uri)
        return m.group(1) if m else None
    return None


def open_external(uri: str) -> None:
    """Apre un URI con l'app predefinita (steam:// → Steam, https:// → browser)."""
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error as e:
        print(f"[wp-penguin] impossibile aprire {uri}: {e.message}")


def steam_running() -> bool:
    return subprocess.run(["pgrep", "-x", "steam"], capture_output=True).returncode == 0


class WorkshopPage(Gtk.Box):
    def __init__(self, on_use: Callable[[str], None], known_ids: Callable[[], set[str]],
                 toast: Callable[[str], None], get_hide_mature: Callable[[], bool],
                 set_hide_mature: Callable[[bool], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_use = on_use
        self.get_hide_mature = get_hide_mature
        self.set_hide_mature = set_hide_mature
        self.content_manager = None
        self.known_ids = known_ids
        self.toast = toast
        self.view = None  # creato al primo accesso: WebKit è pesante da inizializzare
        self._pending_uri: str | None = None

        # --- intestazione + barra di navigazione
        header, self.subtitle = page_header("Workshop", "Wallpaper Engine su Steam Community")
        # Due righe: titolo + azioni, poi navigazione + filtro (finestre strette)
        row1 = Gtk.Box(spacing=8)
        row1.append(header)
        row2 = Gtk.Box(spacing=8, margin_top=12)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.add_css_class("page")
        top.append(row1)
        top.append(row2)

        nav = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        nav.add_css_class("segmented")
        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Indietro",
                                   sensitive=False)
        self.back_btn.connect("clicked", lambda *_: self.view and self.view.go_back())
        self.fwd_btn = Gtk.Button(icon_name="go-next-symbolic", tooltip_text="Avanti",
                                  sensitive=False)
        self.fwd_btn.connect("clicked", lambda *_: self.view and self.view.go_forward())
        home = Gtk.Button(icon_name="go-home-symbolic", tooltip_text="Home del Workshop")
        home.connect("clicked", lambda *_: self.load(self._home()))
        reload_ = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Ricarica")
        reload_.connect("clicked", lambda *_: self.view and self.view.reload())
        for b in (self.back_btn, self.fwd_btn, home, reload_):
            nav.append(b)
        row2.append(nav)

        # Interruttore «Nascondi Mature»
        mature_box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER, margin_start=10, hexpand=True,
                             halign=Gtk.Align.END,
                             tooltip_text="Nasconde i wallpaper classificati «Mature» "
                                          "(contenuti per adulti) nel Workshop")
        mature_box.append(label("Nascondi Mature", "row-title"))
        self.mature_sw = Gtk.Switch(active=get_hide_mature(), valign=Gtk.Align.CENTER)
        self.mature_sw.connect("notify::active", self._on_mature_toggled)
        mature_box.append(self.mature_sw)
        row2.append(mature_box)

        steam_btn = pill("Apri in Steam", icon="folder-download-symbolic", valign=Gtk.Align.CENTER)
        steam_btn.connect("clicked", lambda *_: self._open_in_steam())
        row1.append(steam_btn)
        browser_btn = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Apri nel browser",
                                 css_classes=["flat"], valign=Gtk.Align.CENTER)
        browser_btn.connect("clicked", lambda *_: open_external(self._current_uri()))
        row1.append(browser_btn)
        self.append(top)

        # --- striscia informativa: stato di Steam / elemento aperto
        info = Gtk.Box(spacing=10, margin_start=30, margin_end=30, margin_top=12)
        info.add_css_class("info-strip")
        self.info_icon = Gtk.Image(icon_name="dialog-information-symbolic")
        info.append(self.info_icon)
        self.info_label = label("", wrap=True, hexpand=True, max_chars=90)
        info.append(self.info_label)
        self.info_btn = pill("", "accent", visible=False, valign=Gtk.Align.CENTER)
        self.info_btn.connect("clicked", self._on_info_btn)
        info.append(self.info_btn)
        self.append(info)
        self._info_action: Callable[[], None] | None = None

        self.progress = Gtk.ProgressBar(margin_start=44, margin_end=44, margin_top=10, opacity=0)
        self.progress.add_css_class("web-progress")
        self.append(self.progress)

        # --- contenitore del browser (arrotondato, con ombra)
        self.frame = Gtk.Box(vexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self.frame.add_css_class("webframe")
        self.append(self.frame)

        self.connect("map", lambda *_: (self._ensure_view(), self.refresh_info()))

    # ------------------------------------------------------------ browser

    def _ensure_view(self) -> None:
        if self.view is not None:
            return
        if WebKit is None:
            self._fallback()
            return
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        session = WebKit.NetworkSession.new(str(DATA_DIR), str(CACHE_DIR))
        # Cookie persistenti: il login a Steam sopravvive al riavvio dell'app
        session.get_cookie_manager().set_persistent_storage(
            str(DATA_DIR / "cookies.sqlite"), WebKit.CookiePersistentStorage.SQLITE)

        self.content_manager = WebKit.UserContentManager()
        self._apply_mature_script()
        self.view = WebKit.WebView(network_session=session, user_content_manager=self.content_manager,
                                   hexpand=True, vexpand=True)
        settings = self.view.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_enable_back_forward_navigation_gestures(True)

        self.view.connect("notify::uri", lambda *_: self.refresh_info())
        self.view.connect("notify::title", self._on_title)
        self.view.connect("notify::estimated-load-progress", self._on_progress)
        self.view.connect("load-changed", self._on_load_changed)
        self.view.connect("decide-policy", self._on_decide_policy)
        # target=_blank / window.open: apriamo nella stessa vista invece di nuove finestre
        self.view.connect("create", self._on_create)
        self.frame.append(self.view)
        self.view.load_uri(self._pending_uri or self._home())
        self._pending_uri = None

    def _fallback(self) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        box.append(Gtk.Image(icon_name="web-browser-symbolic", pixel_size=64,
                             css_classes=["hero-icon"]))
        box.append(label("Per sfogliare il Workshop dentro l'app serve WebKitGTK:",
                         xalign=0.5, wrap=True))
        box.append(label("sudo pacman -S webkitgtk-6.0", "mono", xalign=0.5, selectable=True))
        row = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=8)
        b1 = pill("Apri in Steam", "accent")
        b1.connect("clicked", lambda *_: open_external(STEAM_WORKSHOP_URI))
        b2 = pill("Apri nel browser")
        b2.connect("clicked", lambda *_: open_external(WORKSHOP_URL))
        row.append(b1)
        row.append(b2)
        box.append(row)
        self.frame.append(box)
        self.view = False  # non riprovare a crearla

    def load(self, uri: str) -> None:
        if self.view:
            self.view.load_uri(uri)
        else:
            self._pending_uri = uri  # verrà caricato alla creazione della vista

    def open_item(self, workshop_id: str) -> None:
        self.load(ITEM_URL.format(workshop_id))

    def _current_uri(self) -> str:
        return (self.view.get_uri() if self.view else None) or WORKSHOP_URL

    def _open_in_steam(self) -> None:
        wid = item_id(self._current_uri())
        open_external(STEAM_ITEM_URI.format(wid) if wid else STEAM_WORKSHOP_URI)

    # ------------------------------------------------------------- segnali

    def _on_title(self, view, _p) -> None:
        title = view.get_title() or ""
        self.subtitle.set_label(title.replace("Steam Workshop::", "").strip()
                                or "Wallpaper Engine su Steam Community")

    def _on_progress(self, view, _p) -> None:
        p = view.get_estimated_load_progress()
        self.progress.set_fraction(p)
        self.progress.set_opacity(0 if p >= 1.0 else 1)

    def _on_load_changed(self, view, _event) -> None:
        self.back_btn.set_sensitive(view.can_go_back())
        self.fwd_btn.set_sensitive(view.can_go_forward())

    def _on_decide_policy(self, _view, decision, kind) -> bool:
        if kind != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        # I link steam:// li gestisce il client Steam
        if uri.startswith("steam://"):
            open_external(uri)
            decision.ignore()
            return True
        # Con «Nascondi Mature» le ricerche del Workshop vengono filtrate
        if self.get_hide_mature():
            filtered = with_mature_filter(uri)
            if filtered and filtered != uri:
                decision.ignore()
                GLib.idle_add(lambda: (self.view.load_uri(filtered), False)[1])
                return True
        return False

    def _on_create(self, view, action):
        view.load_uri(action.get_request().get_uri())
        return None

    # ----------------------------------------------------- filtro Mature

    def _home(self) -> str:
        return (with_mature_filter(WORKSHOP_URL) or WORKSHOP_URL) if self.get_hide_mature() \
            else WORKSHOP_URL

    def _apply_mature_script(self) -> None:
        if not self.content_manager:
            return
        self.content_manager.remove_all_scripts()
        if self.get_hide_mature():
            self.content_manager.add_script(WebKit.UserScript.new(
                MATURE_BLOCKER_JS, WebKit.UserContentInjectedFrames.TOP_FRAME,
                WebKit.UserScriptInjectionTime.END, None, None))

    def _on_mature_toggled(self, sw: Gtk.Switch, _p) -> None:
        hide = sw.get_active()
        self.set_hide_mature(hide)
        self._apply_mature_script()
        if self.view:
            uri = self.view.get_uri() or ""
            filtered = with_mature_filter(uri) if hide else None
            if filtered:
                self.view.load_uri(filtered)
            elif not hide and "excludedtags" in uri:
                self.view.load_uri(uri.replace("&excludedtags%5B%5D=Mature", "")
                                      .replace("&excludedtags[]=Mature", ""))
            else:
                self.view.reload()
        self.toast("Contenuti Mature nascosti" if hide else "Contenuti Mature visibili")

    # ------------------------------------------------------ striscia info

    def _set_info(self, icon: str, text: str, button: str | None = None,
                  action: Callable[[], None] | None = None) -> None:
        self.info_icon.set_from_icon_name(icon)
        self.info_label.set_label(text)
        self.info_btn.set_visible(bool(button))
        if button:
            self.info_btn.set_label(button)
        self._info_action = action

    def _on_info_btn(self, *_a) -> None:
        if self._info_action:
            self._info_action()

    def refresh_info(self) -> None:
        """Aggiorna la striscia: Steam spento, elemento già scaricato, ecc.
        Chiamata anche da gui.py quando la Libreria cambia (nuovo download)."""
        if not steam_running():
            self._set_info("dialog-warning-symbolic",
                           "Steam non è aperto: puoi sfogliare e iscriverti, ma il download "
                           "parte solo con Steam in esecuzione.",
                           "Avvia Steam", lambda: (open_external("steam://open/main"),
                                                   self.toast("Avvio di Steam…"),
                                                   GLib.timeout_add_seconds(8, self._refresh_once)))
            return
        wid = item_id(self.view.get_uri() if self.view else None)
        if wid and wid in self.known_ids():
            self._set_info("emblem-ok-symbolic", "Questo wallpaper è già nella tua Libreria.",
                           "Usa come sfondo", lambda: self.on_use(wid))
        elif wid:
            self._set_info("folder-download-symbolic",
                           "Premi «Iscriviti» qui sotto: Steam lo scarica e comparirà nella "
                           "Libreria da solo, di solito in pochi secondi.")
        else:
            self._set_info("dialog-information-symbolic",
                           "Accedi a Steam (in alto a destra nella pagina) e iscriviti ai "
                           "wallpaper che ti piacciono: compariranno da soli nella Libreria.")

    def _refresh_once(self) -> bool:
        self.refresh_info()
        return False
