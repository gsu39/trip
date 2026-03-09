# GPX POI Extractor

Estrae automaticamente i punti di interesse (POI) culturali lungo un percorso cicloturistico in formato GPX, interrogando OpenStreetMap tramite l'API Overpass. Arricchisce ogni POI con descrizioni da Wikipedia e genera tre file di output pronti all'uso.

Il progetto è diviso in due script indipendenti:

| Script | Funzione |
|---|---|
| `gpx_poi_extractor.py` | Scarica i POI, arricchisce con Wikipedia, salva GPX + CSV |
| `gpx_poi_to_html.py` | Legge il GPX arricchito e genera la mappa HTML interattiva |

---

## Requisiti

- Python 3.6 o superiore
- Connessione internet durante l'esecuzione
- Nessuna libreria esterna da installare (solo stdlib)

---

## Flusso di lavoro

```bash
# Passo 1 — scarica POI, genera GPX arricchito + CSV  (~5–10 min per 600 km)
PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py percorso.gpx

# Passo 2 — genera la mappa HTML  (istantaneo, nessuna rete necessaria)
python3 gpx_poi_to_html.py percorso_poi.gpx
```

Il prefisso `PYTHONHTTPSVERIFY=0` è necessario su macOS per aggirare la verifica del certificato SSL verso i server Overpass. Non serve per lo script 2.

---

## Script 1 — `gpx_poi_extractor.py`

### Utilizzo

```bash
PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py <percorso.gpx> [--dist METRI]
```

### Argomenti

| Argomento | Tipo | Default | Descrizione |
|---|---|---|---|
| `percorso.gpx` | posizionale | — | File GPX del percorso |
| `--dist` | intero | `1000` | Distanza massima dei POI dalla traccia in metri |

### Percorsi multipli in sequenza

```bash
PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py percorso1.gpx && \
PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py percorso2.gpx && \
PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py percorso3.gpx
```

Con `&&` l'esecuzione si interrompe se uno script fallisce. Usare `;` per eseguirli tutti comunque.

### Parametri configurabili nel codice

| Parametro | Default | Descrizione |
|---|---|---|
| `MAX_DIST_M` | `1000` | Distanza massima di default (sovrascrivibile con `--dist`) |
| `SEGMENT_KM` | `40` | Lunghezza segmenti per le query Overpass |
| `PAUSE_BETWEEN_S` | `12` | Pausa tra le richieste (rate limiting) |

### Cosa cerca

Lo script ricerca i seguenti tipi di POI entro la distanza specificata dalla traccia:

- 🏔️ Punti panoramici
- 🏛️ Musei e gallerie
- ⭐ Attrazioni turistiche
- 🏰 Siti storici: castelli, rovine, siti archeologici, abbazie, torri, monasteri, memoriali, monumenti, porte urbane, mura, croci e edicole votive, pietre miliari

Vengono automaticamente **esclusi** i POI senza nome e le opere d'arte generiche (`artwork`).

### Rimozione doppioni

I POI con lo stesso nome e distanza reciproca inferiore a **250 m** vengono considerati doppioni. Viene mantenuto quello con punteggio più alto:

- +20 punti — link Wikipedia presente
- +10 punti — sito web presente
- +1 punto per ogni tag OSM aggiuntivo

### Descrizioni

Dopo il download, ogni POI viene arricchito con una descrizione testuale:

- POI con tag `wikipedia` → estratto dall'API REST di Wikipedia (fino a ~1000 caratteri)
- POI senza Wikipedia → riga costruita dai tag OSM: tipo storico, orari, biglietto, gestore

### File di output

Il nome base è ricavato automaticamente dal file GPX (prefisso data e ID numerico rimossi, underscore sostituiti da spazi).

| File | Descrizione |
|---|---|
| `<nome>_poi.gpx` | Tracciato + waypoint con tutte le informazioni in `<extensions>` |
| `<nome>_poi.csv` | Tabella con nome, tipo, coordinate, distanza, descrizione, Wikipedia, sito web |

#### Struttura del GPX arricchito

Ogni `<wpt>` contiene tag GPX standard (`<name>`, `<desc>`, `<type>`) e un blocco `<extensions>` con namespace `poi:` che include:

```xml
<extensions>
  <poi:type>historic</poi:type>
  <poi:label>Sito storico</poi:label>
  <poi:emoji>🏰</poi:emoji>
  <poi:color>#795548</poi:color>
  <poi:dist_m>320</poi:dist_m>
  <poi:osm_id>node-123456</poi:osm_id>
  <poi:description>Estratto Wikipedia o descrizione OSM…</poi:description>
  <poi:wikipedia>it:Torre_Normanna</poi:wikipedia>
  <poi:website>https://example.com</poi:website>
  <poi:osm_tags>
    <poi:tag k="historic" v="tower"/>
    <poi:tag k="opening_hours" v="Mo-Su 09:00-18:00"/>
  </poi:osm_tags>
</extensions>
```

---

## Script 2 — `gpx_poi_to_html.py`

### Utilizzo

```bash
python3 gpx_poi_to_html.py <nome>_poi.gpx [--dist METRI] [--nav APPLE|GOOGLE]
```

### Argomenti

| Argomento | Tipo | Default | Descrizione |
|---|---|---|---|
| `<nome>_poi.gpx` | posizionale | — | File GPX prodotto dallo script 1 |
| `--dist` | intero | — | Mostra solo i POI entro questa distanza (filtra senza riscaricare) |
| `--nav` | `APPLE` o `GOOGLE` | `APPLE` | Navigatore per il pulsante ➡️ |

#### Comportamento `--nav`

- **`APPLE`** — apre Apple Maps con navigazione a piedi verso il POI
- **`GOOGLE`** — apre Google Maps con navigazione **in bici** dalla posizione attuale al POI

### File di output

| File | Descrizione |
|---|---|
| `<nome>_poi.html` | Mappa interattiva nel browser |

---

## Mappa interattiva (`_poi.html`)

### Apertura

Il file HTML richiede connessione internet per caricare i tile della mappa (CyclOSM via CDN). I dati dei POI e del tracciato sono **embedded** nel file — nessuna rete necessaria per visualizzarli.

**Su iPhone via Safari** è necessario servire il file tramite HTTPS. Il modo più semplice è pubblicarlo su **GitHub Pages**:

1. Creare un repository pubblico su GitHub
2. Caricare il file HTML
3. Attivare GitHub Pages: *Settings → Pages → Branch: main*
4. Aprire: `https://USERNAME.github.io/REPO/nome_poi.html`

> **Nota cache su iOS Safari:** per forzare il ricaricamento completo senza cache, andare in *Impostazioni → App → Safari → Avanzate → Dati dei siti web* e rimuovere il sito. In alternativa usare la navigazione privata.

### Mappa

Tile layer **CyclOSM** ottimizzato per la ciclabilità. Il tracciato GPX è visualizzato in rosso. I POI sono rappresentati da marker colorati per categoria.

### Lista POI

I POI sono mostrati nella sidebar nell'ordine in cui si incontrano lungo il percorso (ordinamento per posizione progressiva sulla traccia). Per ciascuno: nome, categoria, distanza dal percorso.

### Filtri per categoria

I pulsanti colorati sopra la lista permettono di mostrare o nascondere i POI per tipo. Ogni filtro è un toggle indipendente.

### Selezione di un POI

Cliccando una riga della lista **o** un marker sulla mappa:

- La riga viene evidenziata con bordo rosso
- La lista scorre automaticamente per renderla visibile
- Compare la **descrizione** (estratto Wikipedia o informazioni OSM)
- Compare il pulsante **➡️** per navigare con il navigatore scelto
- La mappa si centra sul POI con zoom ravvicinato (solo se la mappa è visibile)

### Pulsanti header

| Pulsante | Funzione |
|---|---|
| **⊙ You** | Attiva/disattiva il tracking della posizione attuale. Mostra un punto verde con cerchio di accuratezza che si aggiorna in tempo reale. Richiede HTTPS. Su iOS: *Impostazioni → Privacy → Localizzazione → Siti web di Safari → Durante l'uso* |
| **▲ Mappa** *(solo smartphone)* | Mostra o nasconde la mappa. Quando nascosta, la lista POI occupa l'intero schermo. Se c'è un POI selezionato, alla riapertura la lista vi scrolla automaticamente. |

### Layout responsive

| Schermo | Layout |
|---|---|
| Desktop / tablet (> 600 px) | Sidebar a sinistra, mappa a destra. Il pulsante ▲ Mappa non è visibile. |
| Smartphone (≤ 600 px) | Lista in alto (espandibile), mappa in basso (50% dello schermo). |

### Note tecniche

- Nessuna libreria Python necessaria — solo stdlib
- HTML autocontenuto: dati POI e traccia embedded come JSON
- Leaflet 1.9.4 + tile CyclOSM caricati da CDN (richiedono connessione)
- La geolocalizzazione richiede HTTPS — non funziona su `file://` o `http://` locale in Safari
