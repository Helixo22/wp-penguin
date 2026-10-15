# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Piccoli componenti riutilizzati da finestra principale e wizard:
gruppi di impostazioni in stile iOS, righe, controllo segmentato, toast."""

from __future__ import annotations

from gi.repository import GLib, GObject, Gtk, Pango


# Widget cliccabili che ricevono il cursore "a manina" (il CSS di GTK4 non
# supporta la proprietà cursor, quindi lo impostiamo da codice).
CLICKABLE = (Gtk.Button, Gtk.Switch, Gtk.DropDown, Gtk.FlowBoxChild, Gtk.Scale)


def pointerize(root: Gtk.Widget) -> None:
    """Imposta il cursore 'pointer' su root e su tutti i discendenti cliccabili
    (comprese le parti interne, es. i +/− degli SpinButton)."""
    stack = [root]
    while stack:
        w = stack.pop()
        if isinstance(w, CLICKABLE) or (isinstance(w, Gtk.ListBoxRow) and w.get_activatable()):
            w.set_cursor_from_name("pointer")
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()


def label(text: str = "", *classes: str, wrap: bool = False, xalign: float = 0,
          max_chars: int = -1, **kw) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign, wrap=wrap, max_width_chars=max_chars, **kw)
    if wrap:
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in classes:
        lbl.add_css_class(c)
    return lbl


def page_header(title: str, subtitle: str = "") -> tuple[Gtk.Box, Gtk.Label]:
    """Titolo grande stile macOS; ritorna (box, label del sottotitolo)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
    box.append(label(title, "page-title"))
    sub = label(subtitle, "page-subtitle", ellipsize=Pango.EllipsizeMode.END)
    box.append(sub)
    return box, sub


class Group(Gtk.Box):
    """Gruppo arrotondato con righe separate (come le Impostazioni di iOS)."""

    def __init__(self, title: str | None = None, footer: str | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        if title:
            self.append(label(title.upper(), "section-title"))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, show_separators=True)
        self.list.add_css_class("group")
        self.list.connect("row-activated", self._on_activated)
        self.append(self.list)
        self.footer = label(footer or "", "section-footer", wrap=True, visible=bool(footer))
        self.append(self.footer)

    def add(self, row: Gtk.ListBoxRow) -> Gtk.ListBoxRow:
        self.list.append(row)
        pointerize(row)
        return row

    def clear(self) -> None:
        while (r := self.list.get_first_child()) is not None:
            self.list.remove(r)

    @staticmethod
    def _on_activated(_lb, row) -> None:
        # Clic su tutta la riga = clic sull'interruttore (come su iOS)
        if isinstance(getattr(row, "suffix", None), Gtk.Switch):
            row.suffix.set_active(not row.suffix.get_active())


class Row(Gtk.ListBoxRow):
    def __init__(self, title: str, subtitle: str | None = None, suffix: Gtk.Widget | None = None,
                 icon: str | None = None, icon_class: str = "", wrap_subtitle: bool = True) -> None:
        super().__init__(activatable=isinstance(suffix, Gtk.Switch), selectable=False)
        self.suffix = suffix
        box = Gtk.Box(spacing=12)
        if icon:
            img = Gtk.Image(icon_name=icon, pixel_size=16, halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.CENTER)
            frame = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            frame.add_css_class("row-icon")
            if icon_class:
                frame.add_css_class(icon_class)
            img.set_hexpand(True)   # centra l'icona dentro il riquadro…
            frame.set_hexpand(False)  # …senza che il riquadro si allarghi nella riga
            frame.set_size_request(30, 30)
            frame.append(img)
            box.append(frame)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        # max_chars: senza limite un percorso lungo allargherebbe tutta la colonna
        self.title_label = label(title, "row-title", wrap=True, max_chars=48)
        texts.append(self.title_label)
        self.subtitle_label = label(subtitle or "", "row-subtitle", wrap=wrap_subtitle,
                                    visible=bool(subtitle), max_chars=56)
        texts.append(self.subtitle_label)
        box.append(texts)
        if suffix:
            suffix.set_valign(Gtk.Align.CENTER)
            box.append(suffix)
        self.set_child(box)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.set_label(text)
        self.subtitle_label.set_visible(bool(text))


class Segmented(Gtk.Box):
    """Controllo segmentato: ToggleButton raggruppati, uno solo attivo."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

    def __init__(self, labels: list[str], active: int = 0) -> None:
        super().__init__(spacing=2, valign=Gtk.Align.CENTER)
        self.add_css_class("segmented")
        self.buttons: list[Gtk.ToggleButton] = []
        for i, text in enumerate(labels):
            b = Gtk.ToggleButton(label=text)
            if self.buttons:
                b.set_group(self.buttons[0])
            b.connect("toggled", self._on_toggled, i)
            self.buttons.append(b)
            self.append(b)
        self._silent = True
        self.buttons[max(0, min(active, len(labels) - 1))].set_active(True)
        self._silent = False

    @property
    def selected(self) -> int:
        return next((i for i, b in enumerate(self.buttons) if b.get_active()), 0)

    def set_selected(self, i: int, notify: bool = False) -> None:
        self._silent = not notify
        self.buttons[i].set_active(True)
        self._silent = False

    def _on_toggled(self, button: Gtk.ToggleButton, i: int) -> None:
        if button.get_active() and not self._silent:
            self.emit("changed", i)


def pill(text: str, *classes: str, icon: str | None = None, **kw) -> Gtk.Button:
    """Pulsante a pillola; kw vengono passati a Gtk.Button (halign, margin_top…)."""
    b = Gtk.Button(**kw)
    if icon:
        content = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        content.append(Gtk.Image(icon_name=icon))
        content.append(Gtk.Label(label=text))
        b.set_child(content)
    else:
        b.set_label(text)
    b.add_css_class("pill")
    for c in classes:
        b.add_css_class(c)
    return b


class Toast(Gtk.Revealer):
    """Notifica a pillola in basso, scompare da sola (da mettere in un Overlay)."""

    def __init__(self) -> None:
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                         transition_duration=250, halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.END, can_target=False)
        self.lbl = label("", "toast", wrap=True, max_chars=70, xalign=0.5)
        self.set_child(self.lbl)
        self._timeout = 0

    def pop(self, text: str, seconds: float = 3.0) -> None:
        self.lbl.set_label(text)
        self.set_reveal_child(True)
        if self._timeout:
            GLib.source_remove(self._timeout)
        self._timeout = GLib.timeout_add(int(seconds * 1000), self._hide)

    def _hide(self) -> bool:
        self._timeout = 0
        self.set_reveal_child(False)
        return False


def copy_to_clipboard(widget: Gtk.Widget, text: str) -> None:
    widget.get_clipboard().set(text)
