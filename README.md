# GPX POI Extractor

Estrae automaticamente i punti di interesse (POI) culturali lungo un percorso cicloturistico in formato GPX, interrogando OpenStreetMap tramite l'API Overpass. Genera tre file di output pronti all'uso.

---

## Requisiti

- Python 3.6 o superiore
- Connessione internet durante l'esecuzione
- Nessuna libreria esterna da installare (solo stdlib)

---

## Utilizzo

```bash
PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py percorso.gpx
```

Il prefisso `PYTHONHTTPSVERIFY=0` è necessario su macOS per aggirare la verifica del certificato SSL verso i server Overpass.

---

### Parametri configurabili (in testa al file)

| Parametro | Default | Descrizione |
|---|---|---|
| `MAX_DIST_M` | `1000` | Distanza massima dal percorso in metri |
| `SEGMENT_KM` | `40` | Lunghezza dei segmenti per le query Overpass |
| `PAUSE_BETWEEN_S` | `12` | Pausa tra le richieste (rate limiting) |

---

## Cosa cerca

Lo script cerca i seguenti tipi di POI entro `MAX_DIST_M` metri dalla traccia:

- 🔭 Punti panoramici
- 🏛️ Musei e gallerie
- ✨ Attrazioni turistiche
- 🏰 Siti storici: castelli, rovine, siti archeologici, abbazie, torri, monasteri, memoriali, monumenti, porte urbane, mura, croci e edicole votive, pietre miliari

Vengono automaticamente **esclusi** i POI senza nome e le opere d'arte generiche (`artwork`).

I doppioni — stesso nome e distanza reciproca inferiore a 250 m — vengono rimossi mantenendo il POI con più informazioni (priorità: link Wikipedia +20 punti, sito web +10 punti, numero di tag OSM).

---

## File di output

Il nome base è ricavato automaticamente dal file GPX (rimozione del prefisso data e ID numerico, underscore sostituiti da spazi).

### `<nome>_poi.gpx`
Contiene solo Waypoint (no traccia) importabile su Garmin, Komoot, OsmAnd e altri dispositivi GPS.

### `<nome>_poi.csv`
Tabella con tutti i POI (nome, tipo, coordinate, distanza dal percorso, link Wikipedia e sito web). Apribile con Excel o LibreOffice.

### `<nome>_poi.html`
Mappa interattiva — descritta in dettaglio nella sezione successiva.

---

## Mappa interattiva (`_poi.html`)

### Apertura

Il file HTML richiede una connessione internet per caricare i tile della mappa (CyclOSM). Per aprirlo su **iPhone via Safari** è necessario servirlo tramite HTTPS 

### Mappa

La mappa usa il tile layer **CyclOSM**, ottimizzato per la ciclabilità. Il tracciato GPX è visualizzato in rosso. I POI sono rappresentati da marker colorati per categoria.

### Lista POI

La sidebar mostra i POI nell'ordine in cui si incontrano lungo il percorso. Per ciascuno vengono mostrati: nome, categoria, distanza dal percorso.

**Filtri per categoria** — i pulsanti colorati sopra la lista permettono di mostrare o nascondere i POI per tipo.

**Selezione** — cliccando una riga della lista o un marker sulla mappa:
- La riga viene evidenziata con bordo rosso
- La lista scorre automaticamente per renderla visibile
- Compare la descrizione del POI (estratto Wikipedia se disponibile, altrimenti informazioni dai tag OSM: orari, biglietto, gestore…)
- Compare il pulsante **➡️** per aprire Apple Maps con navigazione verso il POI
- La mappa si centra sul POI con zoom ravvicinato

### Pulsanti header

| Pulsante | Funzione |
|---|---|
| **⊙ You** | Attiva/disattiva il tracking della posizione attuale (punto verde con cerchio di accuratezza). Richiede HTTPS e il permesso di localizzazione del browser. Su iOS: *Impostazioni → Privacy → Localizzazione → Siti web di Safari → Durante l'uso* |
| **▲ Mappa** | (solo smartphone) Mostra o nasconde la mappa. Quando la mappa è nascosta la lista POI occupa l'intero schermo. Cliccando un POI dalla lista la mappa si riapre automaticamente. |

### Layout responsive

- **Desktop / tablet** (> 600 px): sidebar a sinistra, mappa a destra. Il pulsante ▲ Mappa non è visibile.
- **Smartphone** (≤ 600 px): lista in alto, mappa in basso (50% dello schermo).
