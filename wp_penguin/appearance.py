# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Caricamento dello stile (style.css) e gestione del tema chiaro/scuro."""

from __future__ import annotations

import hashlib
from pathlib import Path

from gi.repository import Gdk, Gio, Gtk

from .config import CONFIG_DIR

BUILTIN_CSS = Path(__file__).with_name("style.css")
USER_CSS = CONFIG_DIR / "style.css"   # override facoltativo dell'utente
# Impronta della copia appena creata da «Personalizza»: finché il file è
# identico non lo carichiamo, altrimenti una copia mai toccata "congelerebbe"
# lo stile vecchio e nasconderebbe i miglioramenti degli aggiornamenti.
USER_CSS_STAMP = CONFIG_DIR / ".style.css.sha256"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def user_css_is_customized() -> bool:
    if not USER_CSS.is_file():
        return False
    try:
        return USER_CSS_STAMP.read_text().strip() != _sha(USER_CSS)
    except OSError:
        return True  # nessuna impronta: lo consideriamo personalizzato


def create_user_css() -> None:
    """Copia lo stile predefinito in ~/.config/wp-penguin/style.css."""
    USER_CSS.parent.mkdir(parents=True, exist_ok=True)
    USER_CSS.write_bytes(BUILTIN_CSS.read_bytes())
    USER_CSS_STAMP.write_text(_sha(USER_CSS))

COLOR_SCHEMES = ["auto", "light", "dark"]

_providers: list[Gtk.CssProvider] = []
_monitors: list[Gio.FileMonitor] = []  # riferimenti: altrimenti il GC li ferma
_system_scheme = None   # tema di sistema rilevato all'avvio
_current = "dark"
_self_setting = False


def _load(path: Path, priority: int, only_if=lambda: True) -> None:
    provider = Gtk.CssProvider()
    provider.connect("parsing-error", lambda _p, section, err:
                     print(f"[wp-penguin] CSS {section.to_string()}: {err.message}"))

    def reload(*_a) -> None:
        if path.is_file() and only_if():
            provider.load_from_path(str(path))
        else:
            provider.load_from_string("")

    reload()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, priority)
    _providers.append(provider)

    # Ricarica automatica quando il file viene salvato: comodo per ritoccare lo stile
    mon = Gio.File.new_for_path(str(path)).monitor_file(Gio.FileMonitorFlags.NONE, None)
    mon.connect("changed", reload)
    _monitors.append(mon)


def install_css() -> None:
    """Va chiamata una volta, dopo Gtk.init / in do_startup."""
    if _providers:
        return
    _load(BUILTIN_CSS, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _load(USER_CSS, Gtk.STYLE_PROVIDER_PRIORITY_USER, only_if=user_css_is_customized)


_transparency_provider: Gtk.CssProvider | None = None


def apply_transparency(percent: int) -> None:
    """Trasparenza delle finestre: 0 = opache, 70 = molto trasparenti.

    Ridefinisce --window-bg/--sidebar-bg partendo dai colori pieni della
    palette (così vale sia per il tema chiaro sia per quello scuro). La
    sfocatura dietro la finestra la fa Hyprland (decoration:blur)."""
    global _transparency_provider
    percent = max(0, min(int(percent), 70))
    window_alpha = 1 - percent / 100
    sidebar_alpha = window_alpha * 0.65  # la sidebar è un po' più trasparente
    css = (f":root {{ --window-bg: alpha(var(--window-solid), {window_alpha:.2f});"
           f" --sidebar-bg: alpha(var(--sidebar-solid), {sidebar_alpha:.2f}); }}")
    if _transparency_provider is None:
        _transparency_provider = Gtk.CssProvider()
        # Priorità più alta di tutto, anche dello style.css dell'utente: lo
        # slider deve funzionare sempre, anche con una copia personalizzata
        # (che di solito ridefinisce --window-bg/--sidebar-bg)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), _transparency_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        _providers.append(_transparency_provider)
    _transparency_provider.props.prefers_color_scheme = _effective(_current)
    _transparency_provider.load_from_string(css)


def _effective(scheme: str) -> Gtk.InterfaceColorScheme:
    if scheme == "dark":
        return Gtk.InterfaceColorScheme.DARK
    if scheme == "light":
        return Gtk.InterfaceColorScheme.LIGHT
    return (Gtk.InterfaceColorScheme.DARK if _system_scheme == Gtk.InterfaceColorScheme.DARK
            else Gtk.InterfaceColorScheme.LIGHT)


def _on_system_change(settings: Gtk.Settings, _pspec) -> None:
    """Il tema di sistema è cambiato (portale XDG): in "auto" lo seguiamo."""
    global _system_scheme
    if _self_setting:
        return
    _system_scheme = settings.props.gtk_interface_color_scheme
    if _current == "auto":
        for p in _providers:
            p.props.prefers_color_scheme = _effective("auto")


def apply_color_scheme(scheme: str) -> None:
    """'dark' (predefinito) / 'light' forzano il tema, 'auto' segue il sistema.

    Ogni Gtk.CssProvider ha la sua proprietà prefers-color-scheme (default:
    chiaro), che decide quale ramo di @media (prefers-color-scheme: …) di
    style.css viene usato: la impostiamo su tutti i nostri provider. In più
    allineiamo gtk-interface-color-scheme, così anche il tema GTK di base
    (menu, popover, dialoghi) resta coerente.
    """
    global _system_scheme, _current, _self_setting
    settings = Gtk.Settings.get_default()
    if _system_scheme is None:
        # Valore arrivato dal sistema, da ripristinare in "auto"
        _system_scheme = settings.props.gtk_interface_color_scheme
        settings.connect("notify::gtk-interface-color-scheme", _on_system_change)
    _current = scheme
    target = _effective(scheme) if scheme != "auto" else _system_scheme
    _self_setting = True
    try:
        if settings.props.gtk_interface_color_scheme != target:
            settings.props.gtk_interface_color_scheme = target
    finally:
        _self_setting = False
    for p in _providers:
        p.props.prefers_color_scheme = _effective(scheme)
