#!/bin/sh
# SPDX-FileCopyrightText: 2026 wp-penguin contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# Installa wp-penguin per l'utente corrente (niente sudo):
#   - comando  ~/.local/bin/wp-penguin   (symlink al launcher del repo)
#   - icona    ~/.local/share/icons/hicolor/{scalable,48x48,…,512x512}/apps/io.github.wppenguin.*
#   - launcher ~/.local/share/applications/io.github.wppenguin.desktop
# Il file .desktop ha lo stesso nome dell'application id GTK, così su Wayland
# la finestra viene associata alla sua voce e alla sua icona.
# Disinstallazione: ./install.sh --uninstall
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin/wp-penguin"
ICON="$DATA/icons/hicolor/scalable/apps/io.github.wppenguin.svg"
DESKTOP="$DATA/applications/io.github.wppenguin.desktop"
SIZES="32 48 64 128 256 512"

if [ "${1:-}" = "--uninstall" ]; then
    rm -f "$BIN" "$ICON" "$DESKTOP"
    for s in $SIZES; do rm -f "$DATA/icons/hicolor/${s}x${s}/apps/io.github.wppenguin.png"; done
    echo "wp-penguin disinstallato (la configurazione in ~/.config/wp-penguin resta)."
    exit 0
fi

mkdir -p "$(dirname "$BIN")" "$(dirname "$ICON")" "$(dirname "$DESKTOP")"
chmod +x "$DIR/wp-penguin"
ln -sf "$DIR/wp-penguin" "$BIN"
cp "$DIR/data/icons/hicolor/scalable/apps/io.github.wppenguin.svg" "$ICON"
# Versioni PNG per i launcher che non usano gli SVG
if command -v rsvg-convert >/dev/null; then
    for s in $SIZES; do
        mkdir -p "$DATA/icons/hicolor/${s}x${s}/apps"
        rsvg-convert -w "$s" -h "$s" "$ICON" -o "$DATA/icons/hicolor/${s}x${s}/apps/io.github.wppenguin.png"
    done
fi

cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=wp-penguin
GenericName=Wallpaper animati
Comment=Wallpaper Engine come sfondo animato su Hyprland
Exec=$BIN
Icon=io.github.wppenguin
Terminal=false
Categories=Settings;DesktopSettings;GTK;
Keywords=wallpaper;sfondo;wallpaper engine;hyprland;animato;
StartupNotify=true
StartupWMClass=io.github.wppenguin
Actions=wizard;stop;

[Desktop Action wizard]
Name=Procedura guidata
Exec=$BIN --wizard

[Desktop Action stop]
Name=Ferma tutti i wallpaper
Exec=$BIN --stop
DESK

command -v update-desktop-database >/dev/null && update-desktop-database "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t -f "$DATA/icons/hicolor" || true

echo "Installato:"
echo "  comando  $BIN"
echo "  launcher $DESKTOP"
echo "  icona    $ICON"
