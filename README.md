# trip
Creates a list of POI along a GPX travel, with an interactive viewer.

*Autore: CLAUDE e Gabriele Unterberger*

## gpx_poi_extractor.py

GPX POI Extractor è uno script Python standalone (nessuna libreria esterna) che, dato un file GPX, estrae automaticamente i punti di interesse culturali lungo il percorso interrogando l'API Overpass (OpenStreetMap).

## Come funziona:
Divide il tracciato in segmenti da ~40 km e invia una query Overpass per ciascuno, rispettando i rate limit con backoff automatico su errore 429. 
Filtra i risultati con calcolo Haversine punto-per-punto, tenendo solo i POI entro 1 km dalla traccia

Ignora i POI senza nome e le opere d'arte generiche (artwork)

Cerca: punti panoramici, musei, gallerie, attrazioni, monumenti, siti storici (castelli, rovine, siti archeologici, abbazie, torri…)

## Output generati (nome derivato automaticamente dal file GPX):
GPX — contiene solo i POI (waypoints) non la traccia; importabile su Garmin, Adze, Avenue

CSV — apribile con Excel/LibreOffice

HTML — mappa interattiva con tile layer CyclOSM (ottimizzato per ciclisti); sidebar con lista POI filtrabile per categoria; su smartphone la mappa occupa il 50% dello schermo e può essere nascosta per leggere la lista; cliccando un POI dalla lista la mappa si riapre e centra il punto; ogni POI mostra nel popup il link a Wikipedia e al sito web ufficiale, se disponibili nei dati OpenStreetMap

**Requisiti:** Python 3.6+, connessione internet durante l'esecuzione. Tempo stimato: 5–10 minuti per 600 km.

**Importante:** Per vedere la propria posizione sulla mappa in iOS bisogna abilitare la geolocalizzazione per safari: Impostazioni->Privacy e sicurezza->Localizzazione->Siti web di safari->Chiedi la prossima volta 

## Dipendenze esterne a runtime:
API Overpass (internet) — per scaricare i POI da OpenStreetMap durante l'esecuzione dello script
CDN Cloudflare (internet) — per caricare Leaflet e i tile CyclOSM quando si apre l'HTML nel browser
Lo script Python in sé non richiede librerie da installare, ma l'HTML generato richiede connessione per funzionare.

## Uso: 
In Terminal, mettere script e file .gpx nella stessa directory:

**PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py (nomefile).gpx**
