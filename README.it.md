<p align="center">
  <img src="data/banner.svg" alt="wp-penguin — wallpaper animati di Wallpaper Engine su Hyprland" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="Licenza: GPL-3.0-or-later" src="https://img.shields.io/badge/licenza-GPL--3.0--or--later-blue"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="GTK 4" src="https://img.shields.io/badge/GTK-4-4A86CF?logo=gtk&logoColor=white">
  <img alt="Hyprland" src="https://img.shields.io/badge/Hyprland-Wayland-58E1FF">
  <img alt="Arch Linux" src="https://img.shields.io/badge/Arch_Linux-supportato-1793D1?logo=archlinux&logoColor=white">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>Italiano</b>
</p>

**wp-penguin** è un'app nativa GTK 4 che porta i tuoi wallpaper di
[Wallpaper Engine](https://store.steampowered.com/app/431960/) su **Hyprland**,
su Linux. Sfogli la tua libreria del Workshop di Steam, scegli un wallpaper per
ogni monitor e wp-penguin fa il resto: lo avvia, lo mette in pausa quando giochi,
lo riavvia se crasha e lo ripristina a ogni accesso.

A disegnare i wallpaper è l'ottimo motore open source
[linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine).
wp-penguin è l'interfaccia comoda che ci sta intorno.

<p align="center">
  <a href="https://github.com/Helixo22/wp-penguin/raw/main/docs/media/wp-penguin-demo.mp4">
    <img src="docs/media/wp-penguin-demo.webp" alt="wp-penguin — video di presentazione" width="100%">
  </a>
  <br>
  <sub>▶ <a href="https://github.com/Helixo22/wp-penguin/raw/main/docs/media/wp-penguin-demo.mp4"><b>Guarda il video completo in HD</b></a> (MP4, 35 s) · i wallpaper mostrati sono grafiche dimostrative originali</sub>
</p>

> [!NOTE]
> wp-penguin è un progetto **non ufficiale** della comunità. Non è affiliato,
> approvato né sponsorizzato da Wallpaper Engine, dai suoi sviluppatori o da
> Valve. Serve una tua copia di Wallpaper Engine su Steam. Vedi
> [Note legali](#note-legali).

---

## Indice

- [Funzioni](#funzioni)
- [Cosa funziona](#cosa-funziona)
- [Requisiti](#requisiti)
- [Installazione](#installazione)
- [Uso](#uso)
- [Avvio all'accesso](#avvio-allaccesso)
- [Come funziona](#come-funziona)
- [Risoluzione dei problemi](#risoluzione-dei-problemi)
- [Privacy](#privacy)
- [Note legali](#note-legali)
- [Licenza](#licenza)
- [Contribuire](#contribuire)

## Funzioni

**Libreria**
- **Libreria a griglia** con anteprime grandi di tutti i wallpaper a cui sei
  iscritto. Trova Steam nativo, Steam Flatpak e le librerie su altri dischi.
- **Nuovi**: i wallpaper arrivati dopo la configurazione, dal più recente, con un
  contatore nella sidebar e il badge «Nuovo» su quelli appena scaricati.
- **Workshop integrato**: ti iscrivi ai wallpaper senza uscire dall'app. Steam
  li scarica e compaiono da soli nella Libreria. L'interruttore
  **Nascondi Mature**, attivo di default, filtra i contenuti per adulti.

**Monitor e riproduzione**
- **Un wallpaper per monitor**, oppure *Applica a tutti i monitor* con un clic.
- **Pausa con schermo intero** o anche **pausa con finestre aperte** (flottanti
  comprese), monitor per monitor. Il processo in pausa non usa né CPU né GPU
  (`SIGSTOP`).
- **Sfondo statico abbinato**: un fotogramma a piena risoluzione del wallpaper
  animato diventa anche lo sfondo statico (con Ambxst, awww/swww o hyprpaper). Se
  l'animazione si ferma o crasha, vedi comunque l'immagine giusta.
- **Riavvio automatico** se il wallpaper si blocca, al massimo 3 volte in 5
  minuti. I wallpaper che fanno crashare il motore all'avvio vengono segnati come
  *non compatibili*.
- **Muto vero**: con il muto attivo il motore non apre alcuno stream audio.
  Con il solo `--silent` li apre comunque, vedi
  [Risoluzione dei problemi](#risoluzione-dei-problemi).

**Configurazione e aspetto**
- **Procedura guidata al primo avvio**: controlla le dipendenze e mostra i comandi
  esatti per installarle, trova Steam, ti fa scegliere i monitor e le preferenze e,
  se vuoi, aggiunge l'avvio automatico alla configurazione di Hyprland, con backup.
- **Grafica ispirata a macOS**: tema scuro e chiaro, trasparenza regolabile con la
  sfocatura di Hyprland, interruttori in stile iOS. Tutto lo stile sta in un unico
  file CSS modificabile.

## Cosa funziona

wp-penguin riproduce tutto ciò che riproduce linux-wallpaperengine:

| Tipo di wallpaper | Stato | Note |
|---|---|---|
| **Video** | ✅ Buono | Decodifica hardware con mpv. È il tipo più affidabile. |
| **Scene** | 🟡 Per lo più | La maggior parte funziona. Alcuni effetti, particellari, animazioni «puppet» e script possono mancare o essere diversi. |
| **Web** | 🟡 Parziale | Renderizzati con CEF. Le pagine che usano API specifiche di Wallpaper Engine possono non funzionare. |
| **Preset** | 🟡 Come la base | Usano il wallpaper base, a cui vengono applicate le proprietà del preset con `--set-property`. |
| **Application** | ❌ No | Sono eseguibili Windows. |

## Requisiti

- **Arch Linux** o derivata. Su altre distribuzioni funziona installando le stesse
  dipendenze.
- **Hyprland**. wp-penguin usa l'IPC di Hyprland per monitor, schermo intero ed
  eventi delle finestre.
- **Wallpaper Engine** acquistato e installato da Steam (nativo o Flatpak). Servono
  solo i suoi file, in particolare `assets/`; non lo avvii mai.
- **linux-wallpaperengine**, dall'AUR.
- **Python 3.10+**, **PyGObject**, **GTK 4**.
- Facoltativi: **WebKitGTK 6** per il Workshop integrato, il font **Inter**,
  **webp-pixbuf-loader** per le anteprime WebP.

## Installazione

### 1. Dipendenze

```bash
# Motore (AUR). La prima compilazione include CEF e richiede un po' di tempo.
yay -S linux-wallpaperengine-git

# Ambiente dell'app (repository ufficiali)
sudo pacman -S --needed python python-gobject gtk4 gdk-pixbuf2

# Extra facoltativi
sudo pacman -S --needed webkitgtk-6.0 inter-font webp-pixbuf-loader
```

> [!TIP]
> Senza `MAKEFLAGS` la compilazione AUR usa un solo core. Per usarli tutti:
> `MAKEFLAGS="-j$(nproc)" CMAKE_BUILD_PARALLEL_LEVEL=$(nproc) yay -S linux-wallpaperengine-git`

**Wallpaper Engine con Steam su Linux:** apri *Proprietà → Compatibilità*, forza
*Proton* e installalo. Non serve avviarlo.

### 2. wp-penguin

```bash
git clone https://github.com/Helixo22/wp-penguin.git
cd wp-penguin
./install.sh
```

`install.sh` installa **solo per il tuo utente**, senza `sudo`. Aggiunge:
- il comando `wp-penguin` in `~/.local/bin`;
- l'icona dell'app;
- la voce nel launcher, con le azioni rapide *Procedura guidata* e
  *Ferma tutti i wallpaper*.

`./install.sh --uninstall` li rimuove; le impostazioni restano.

## Uso

Apri **wp-penguin** dal launcher delle app. La prima volta parte la procedura
guidata.

```text
wp-penguin             apre l'app (al primo avvio: procedura guidata)
wp-penguin --wizard    riapre la procedura guidata
wp-penguin --daemon    ripristina i wallpaper e resta attivo (pausa, watchdog)
wp-penguin --restore   ripristina i wallpaper salvati ed esce
wp-penguin --stop      ferma tutti i wallpaper
```

Nell'app:
- **Libreria**: seleziona un wallpaper per aprire il pannello di dettaglio,
  scegli il monitor e premi *Imposta come sfondo* o *Applica a tutti i monitor*.
  Con il doppio clic lo applichi subito.
- **Nuovi**: quello che hai scaricato di recente.
- **Workshop**: sfoglia e iscriviti. Accedi a Steam una volta e il login resta
  salvato.
- **Monitor**: stato di ogni schermo, wallpaper animati sì o no, riavvia o ferma.
- **Impostazioni**: FPS, volume e muto, scaling, pause, sfondo statico, riavvio
  automatico, avvio all'accesso, tema, trasparenza, cartelle di Steam.

## Avvio all'accesso

La procedura guidata, oppure *Impostazioni → Sistema → Avvia all'accensione*,
aggiunge alla configurazione di Hyprland il blocco qui sotto. Prima crea un backup
con data e ora. Il blocco è racchiuso tra due marcatori, così si può togliere in
modo pulito.

```lua
-- hyprland.lua (configurazione Lua)
hl.on("hyprland.start", function ()
    hl.exec_cmd([[/home/tu/.local/bin/wp-penguin --daemon]])
end)
```

```ini
# hyprland.conf (configurazione classica)
exec-once = ~/.local/bin/wp-penguin --daemon
```

Il wallpaper animato viene disegnato sul livello `bottom` di layer-shell,
**sopra** il livello `background` usato da hyprpaper, awww/swww e dalle shell
desktop. Puoi quindi lasciare attivo il tuo gestore di sfondi, che fa anche da
immagine di riserva (vedi *Sfondo statico abbinato*).

## Come funziona

```text
wp-penguin (app GTK 4) ──► config.json ◄── wp-penguin --daemon
        │                                      │  eventi IPC di Hyprland
        ▼                                      ▼
 linux-wallpaperengine --screen-root <monitor> ...   (un processo per monitor)
```

- **I wallpaper attivi vengono cercati in `/proc`**, senza file PID: app e daemon
  vedono sempre lo stato reale.
- **La pausa** usa `SIGSTOP` e `SIGCONT`, comandati dagli eventi di Hyprland. Il
  daemon controlla se il workspace attivo di ogni monitor (o uno special
  workspace visibile) ha una finestra a schermo intero, oppure una finestra
  qualsiasi se hai scelto quell'opzione.
- **Il muto** avvia il motore con `--silent --noautomute --no-audio-processing` e
  in un ambiente senza server audio (`SDL_AUDIODRIVER=dummy`, PipeWire e
  PulseAudio irraggiungibili, ALSA su un dispositivo nullo). Così non si apre
  nessuno stream audio.
- **I «Nuovi»**: wp-penguin registra quando vede per la prima volta ogni
  wallpaper. Tutto ciò che c'era già prima della configurazione non conta come
  nuovo.

| File | A cosa serve |
|---|---|
| `~/.config/wp-penguin/config.json` | Impostazioni e wallpaper scelto per ogni monitor |
| `~/.config/wp-penguin/style.css` | Stile personalizzato facoltativo. Si crea da *Impostazioni → Aspetto → Personalizza*. |
| `~/.local/state/wp-penguin/` | Log del motore, wallpaper non compatibili, registro dei «visti», stato dello sfondo statico |
| `~/.cache/wp-penguin/` | Fotogrammi dello sfondo statico, cache di WebKit |
| `~/.local/share/wp-penguin/webkit/` | Dati del Workshop integrato, compresi i cookie del login a Steam |

## Risoluzione dei problemi

<details>
<summary><b>Un wallpaper resta nero o si chiude subito</b></summary>

Apri *Impostazioni → Avanzate → Log*, oppure guarda
`~/.local/state/wp-penguin/<monitor>.log`.

Se il motore crasha all'avvio, wp-penguin mostra l'errore e segna il wallpaper
come **Non compatibile**. Di solito è un limite o un bug di linux-wallpaperengine
con quel wallpaper: segnalalo
[al progetto del motore](https://github.com/Almamu/linux-wallpaperengine/issues).

Verifica anche che Wallpaper Engine sia installato da Steam, perché le scene
hanno bisogno della sua cartella `assets/`.
</details>

<details>
<summary><b>Tutto l'audio del PC si ammutolisce mentre gira un wallpaper (EasyEffects)</b></summary>

linux-wallpaperengine apre stream audio anche con `--silent`. Con PipeWire questi
stream possono andare continuamente in xrun. EasyEffects li cattura insieme alle
altre app e la sua uscita può diventare muta.

- **Wallpaper in muto:** wp-penguin evita il problema non aprendo alcuno stream.
- **Wallpaper con l'audio:** escludi il motore da EasyEffects in
  `~/.config/easyeffects/db/easyeffectsrc`:

```ini
[StreamOutputs]
blocklist=linux-wallpaperengine,mpv
```
</details>

<details>
<summary><b>Un wallpaper a cui mi sono iscritto non compare</b></summary>

Steam deve essere **aperto** per scaricare gli elementi del Workshop. A download
finito il wallpaper compare da solo in **Nuovi**, perché la Libreria si aggiorna
automaticamente.
</details>

<details>
<summary><b>Problemi grafici con NVIDIA</b></summary>

Prova ad avviare con `__GL_THREADED_OPTIMIZATIONS=0` nell'ambiente.
</details>

## Privacy

- wp-penguin non ha **telemetria né analytics**. Impostazioni, log e cache restano
  sul tuo computer.
- L'unico traffico di rete viene dal **Workshop integrato**, che carica
  `steamcommunity.com` come un normale browser. Se lì accedi a Steam, i cookie di
  sessione restano solo in `~/.local/share/wp-penguin/webkit/`. Cancella quella
  cartella per uscire.
- I link `steam://` o da aprire nel browser vengono passati a quelle app.

## Note legali

- **Nessuna affiliazione.** wp-penguin è un progetto indipendente e non ufficiale.
  Non è affiliato, autorizzato, gestito, sponsorizzato né approvato da Wallpaper
  Engine o dai suoi sviluppatori, né da Valve Corporation.
- **Marchi.** «Wallpaper Engine» è un marchio del rispettivo titolare. «Steam» e
  il logo di Steam sono marchi e/o marchi registrati di Valve Corporation negli
  Stati Uniti e/o in altri Paesi. «Hyprland» appartiene ai rispettivi titolari.
  I marchi sono citati solo per indicare la compatibilità (uso descrittivo).
- **Serve una tua copia.** wp-penguin non include, non scarica e non
  ridistribuisce Wallpaper Engine, i suoi assets o contenuti del Workshop. Usa solo
  i file che Steam ha già installato sul tuo computer, grazie al tuo acquisto e
  alle tue iscrizioni.
- **I contenuti del Workshop appartengono ai loro autori.** I wallpaper sono opere
  dei rispettivi autori e sono soggetti al
  [Contratto di Sottoscrizione di Steam](https://store.steampowered.com/subscriber_agreement/?l=italian)
  e ai diritti degli autori. wp-penguin non li carica, non li condivide e non li
  ridistribuisce. I fotogrammi creati per lo sfondo statico restano sul tuo
  computer, per uso personale.
- **Contenuti per adulti.** Il filtro *Nascondi Mature* si basa sulla
  classificazione scelta da chi pubblica il wallpaper. Gli elementi classificati
  male possono passare, quindi il filtro è una comodità, non un controllo
  parentale garantito.
- **Nessuna garanzia.** wp-penguin è fornito «così com'è», senza garanzie di alcun
  tipo. Vedi le sezioni 15–16 della [licenza](LICENSE).

## Licenza

wp-penguin segue la convenzione [REUSE](https://reuse.software): ogni file dichiara
la sua licenza in un'intestazione SPDX.

| Parte | Licenza |
|---|---|
| Codice sorgente (`wp_penguin/`, `wp-penguin`, `install.sh`, `style.css`) | [GPL-3.0-or-later](LICENSES/GPL-3.0-or-later.txt) |
| Grafica (icona, logo, banner in `data/`) e video dimostrativo (`docs/media/`) | [CC BY-SA 4.0](LICENSES/CC-BY-SA-4.0.txt) |

Copyright © 2026 contributori di wp-penguin.

**Software di terze parti.** wp-penguin non ne include. Questi programmi e
librerie si installano a parte con il gestore di pacchetti, e
linux-wallpaperengine gira come programma separato:

| Componente | Licenza | Ruolo |
|---|---|---|
| [linux-wallpaperengine](https://github.com/Almamu/linux-wallpaperengine) | GPL-3.0-only | Motore di rendering (processo separato) |
| [GTK 4](https://gtk.org) | LGPL-2.1-or-later | Toolkit grafico |
| [PyGObject](https://pygobject.gnome.org) | LGPL-2.1-or-later | Binding Python |
| [WebKitGTK](https://webkitgtk.org) | LGPL-2.1 / BSD (misto) | Workshop integrato (facoltativo) |
| [gdk-pixbuf](https://gitlab.gnome.org/GNOME/gdk-pixbuf) | LGPL-2.0-or-later | Decodifica delle anteprime |
| [Inter](https://rsms.me/inter/) | OFL-1.1 | Font dell'interfaccia (facoltativo) |
| [mpv](https://mpv.io), [FFmpeg](https://ffmpeg.org) | GPL / LGPL | Riproduzione video ed estrazione dei fotogrammi (usati dal motore e dallo sfondo statico) |

## Contribuire

Segnalazioni e pull request sono benvenute. Dove serve più aiuto:

- **traduzioni** (l'interfaccia per ora è solo in italiano);
- pacchetti (un `PKGBUILD` per l'AUR, Flatpak);
- supporto ad altri compositor (Sway e niri hanno un IPC simile);
- segnalazioni di wallpaper che si comportano diversamente che su Windows, con
  il log del motore.

Prima di aprire una pull request:
- mantieni lo stile del codice circostante;
- tieni tutto lo stile grafico in `style.css`;
- aggiungi l'intestazione SPDX ai file nuovi.

Contribuendo accetti che i tuoi contributi siano rilasciati con le licenze del
progetto (GPL-3.0-or-later per il codice, CC BY-SA 4.0 per la grafica).
