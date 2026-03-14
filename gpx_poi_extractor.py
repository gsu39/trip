#!/usr/bin/env python3
"""
GPX POI Extractor  —  Script 1 di 2
=====================================
Estrae POI culturali entro MAX_DIST_M metri da un percorso GPX usando
l'API Overpass (OpenStreetMap), arricchisce con descrizioni Wikipedia
e salva tutto in un GPX con <extensions> e in un CSV.

USO:
    PYTHONHTTPSVERIFY=0 python3 gpx_poi_extractor.py percorso.gpx

OUTPUT:
    - <nome>_poi.gpx   → tracciato + waypoint con tutte le informazioni
    - <nome>_poi.csv   → tabella apribile con Excel/LibreOffice

REQUISITI:
    Python 3.6+ — nessuna libreria esterna necessaria

PASSO SUCCESSIVO:
    python3 gpx_poi_to_html.py <nome>_poi.gpx
"""

import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import json
import csv
import math
import time
import sys
import os
import ssl
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Fix SSL su macOS (certificate verify failed)
SSL_CONTEXT = ssl.create_default_context()
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CONTEXT.check_hostname = False
    SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# ─────────────────────────────────────────────
# CONFIGURAZIONE
# ─────────────────────────────────────────────
MAX_DIST_M       = 1000   # distanza massima dal percorso in metri
SEGMENT_KM       = 40     # lunghezza segmento per query Overpass
BUFFER_KM        = 1.3    # buffer bbox oltre il percorso
PAUSE_BETWEEN_S  = 6      # pausa tra query Overpass principali
OVERPASS_TIMEOUT = 60
WIKI_MAX_CHARS   = 1000   # lunghezza massima estratto Wikipedia (nessun taglio per il GPX)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
]

POI_TYPES = {
    "viewpoint":  ("🏔️",  "Panoramico",        "#FF6B35"),
    "museum":     ("🏛️",  "Museo",             "#9B59B6"),
    "gallery":    ("🖼️",  "Galleria d'arte",   "#F44336"),
    "artwork":    ("🎨",  "Arte/Monumento",    "#E91E63"),
    "attraction": ("⭐",  "Attrazione",        "#2196F3"),
    "historic":   ("🏰",  "Sito storico",      "#795548"),
    "komoot":     ("🚴",  "Komoot",            "#6AA84F"),
}

# ─────────────────────────────────────────────
# FUNZIONI GEOMETRICHE
# ─────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def build_track_grid(track, cell_deg=0.01):
    """Indice spaziale a griglia: mappa (row,col) -> lista di indici track.
    cell_deg ~0.01° ≈ 700-1100m — copre abbondantemente MAX_DIST_M=1000m.
    Costruzione O(N), lookup O(1) + scan della cella.
    """
    grid = {}
    for i, (lat, lon) in enumerate(track):
        key = (int(lat / cell_deg), int(lon / cell_deg))
        grid.setdefault(key, []).append(i)
    return grid

def dist_point_to_track(lat, lon, track, grid, cell_deg=0.01):
    """Distanza minima da (lat,lon) al track usando l'indice a griglia.
    Controlla solo le celle adiacenti (3×3), poi fallback lineare se vuote.
    """
    row = int(lat / cell_deg)
    col = int(lon / cell_deg)
    candidates = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            candidates.extend(grid.get((row+dr, col+dc), []))
    if not candidates:
        candidates = range(len(track))   # fallback (rarissimo)
    best = float('inf')
    for i in candidates:
        d = haversine(lat, lon, track[i][0], track[i][1])
        if d < best:
            best = d
    return best

def total_distance_km(track):
    return sum(haversine(track[i-1][0], track[i-1][1], track[i][0], track[i][1])
               for i in range(1, len(track))) / 1000

def parse_gpx_waypoints(filepath):
    """
    Legge i waypoint (<wpt>) presenti nel file GPX sorgente.
    Restituisce una lista di dict compatibili con il formato POI.
    """
    tree = ET.parse(filepath)
    root = tree.getroot()
    ns = root.tag.split('}')[0].strip('{') if '}' in root.tag else ''

    def find_text(el, tag):
        child = el.find(f'{{{ns}}}{tag}') if ns else el.find(tag)
        return child.text.strip() if child is not None and child.text else ''

    waypoints = []
    wpt_tag = f'{{{ns}}}wpt' if ns else 'wpt'
    for wpt in root.findall(wpt_tag):
        try:
            lat = float(wpt.get('lat'))
            lon = float(wpt.get('lon'))
        except (TypeError, ValueError):
            continue
        name = find_text(wpt, 'name') or find_text(wpt, 'desc') or '(senza nome)'
        if name == '(senza nome)':
            continue
        # Leggi tag noti dal GPX
        tags = {}
        for key in ('desc', 'cmt', 'type', 'sym'):
            v = find_text(wpt, key)
            if v:
                tags[key] = v
        # Cerca link (href)
        link_tag = f'{{{ns}}}link' if ns else 'link'
        link_el = wpt.find(link_tag)
        if link_el is not None:
            href = link_el.get('href', '')
            if href:
                tags['website'] = href
        waypoints.append({
            'osm_id':  f'wpt-{len(waypoints)}',
            'lat':     lat,
            'lon':     lon,
            'type':    'komoot',
            'name':    name,
            'dist_m':  0,
            'tags':    tags,
        })
    return waypoints


# ─────────────────────────────────────────────
# PARSING GPX
# ─────────────────────────────────────────────
def parse_gpx(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()
    ns = root.tag.split('}')[0].strip('{') if '}' in root.tag else ''

    def find_all(tag):
        if ns:
            return root.findall(f'.//{{{ns}}}{tag}')
        return root.findall(f'.//{tag}')

    points = []
    for pt in find_all('trkpt'):
        try:
            points.append((float(pt.get('lat')), float(pt.get('lon'))))
        except (TypeError, ValueError):
            pass
    return points

# ─────────────────────────────────────────────
# SEGMENTAZIONE BBOX
# ─────────────────────────────────────────────
def get_segment_bboxes(track, seg_km=SEGMENT_KM, buf_km=BUFFER_KM):
    bboxes = []
    seg_start = 0
    cum_dist = 0.0
    for i in range(1, len(track)):
        cum_dist += haversine(track[i-1][0], track[i-1][1], track[i][0], track[i][1]) / 1000
        if cum_dist >= seg_km or i == len(track) - 1:
            seg = track[seg_start:i+1]
            lats = [p[0] for p in seg]
            lons = [p[1] for p in seg]
            mid_lat = (min(lats) + max(lats)) / 2
            lat_buf = buf_km / 111.0
            lon_buf = buf_km / (111.0 * math.cos(math.radians(mid_lat)))
            bboxes.append((
                min(lats) - lat_buf, min(lons) - lon_buf,
                max(lats) + lat_buf, max(lons) + lon_buf
            ))
            seg_start = i
            cum_dist = 0.0
    return bboxes

# ─────────────────────────────────────────────
# OVERPASS API
# ─────────────────────────────────────────────
def build_query(bbox):
    s, w, n, e = bbox
    b = f"{s:.6f},{w:.6f},{n:.6f},{e:.6f}"
    return f"""[out:json][timeout:{OVERPASS_TIMEOUT}];
(
  node["tourism"="viewpoint"]({b});
  node["tourism"="museum"]({b});
  way["tourism"="museum"]({b});
  node["tourism"="gallery"]({b});
  way["tourism"="gallery"]({b});
  node["tourism"="artwork"]({b});
  node["tourism"="attraction"]({b});
  way["tourism"="attraction"]({b});
  node["historic"~"^(castle|ruins|archaeological_site|memorial|monument|fort|tower|manor|church|abbey|monastery|city_gate|citywalls|pillory|wayside_cross|wayside_shrine|milestone|boundary_stone)$"]({b});
  way["historic"~"^(castle|ruins|archaeological_site|fort|tower|manor|abbey|monastery|city_gate|citywalls)$"]({b});
);
out center tags;"""

def query_overpass(query, retries=5):
    data = urllib.parse.urlencode({"data": query}).encode()
    headers = {"User-Agent": "GPX-POI-Extractor/1.0"}
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(retries):
            try:
                req = urllib.request.Request(endpoint, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=OVERPASS_TIMEOUT+10, context=SSL_CONTEXT) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 30 * (attempt + 1)
                    print(f"\n    ⏳ Rate limit (429) — attendo {wait}s...")
                    time.sleep(wait)
                elif e.code == 504:
                    wait = 15 * (attempt + 1)
                    print(f"\n    ⏳ Timeout server (504) — attendo {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"\n    ⚠️  {endpoint} tentativo {attempt+1}: {e}")
                    time.sleep(5)
            except Exception as e:
                print(f"\n    ⚠️  {endpoint} tentativo {attempt+1}: {e}")
                time.sleep(5 * (attempt + 1))
        print(f"    ↩️  Provo endpoint successivo...")
    raise RuntimeError("Tutti gli endpoint Overpass non raggiungibili")

# ─────────────────────────────────────────────
# CLASSIFICAZIONE POI
# ─────────────────────────────────────────────
def classify(tags):
    t = tags.get("tourism", "")
    h = tags.get("historic", "")
    if t == "viewpoint":  return "viewpoint"
    if t == "museum":     return "museum"
    if t == "gallery":    return "gallery"
    if t == "artwork":    return "artwork"
    if h in ("memorial", "monument", "wayside_cross", "wayside_shrine", "pillory", "milestone"):
        return "artwork"
    if t == "attraction": return "attraction"
    if h:                 return "historic"
    return "attraction"

def get_name(tags):
    for key in ("name", "name:it", "name:en", "name:la", "official_name"):
        if tags.get(key):
            return tags[key]
    return "(senza nome)"

# ─────────────────────────────────────────────
# ARRICCHIMENTO WAYPOINT KOMOOT
# ─────────────────────────────────────────────
# Tag OSM utili da copiare sul waypoint se trovati
_WPT_COPY_TAGS = (
    "website", "contact:website", "url",
    "phone", "contact:phone",
    "opening_hours",
    "wikipedia", "wikidata",
    "description", "description:it", "description:en",
    "fee", "cuisine", "stars", "rooms",
    "operator", "brand",
)

def _name_similarity(a, b):
    """Similarità semplice tra due nomi (0-1): parole in comune / parole totali."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)

def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def _fetch_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "GPX-POI-Extractor/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
        return json.loads(r.read().decode("utf-8"))

def enrich_waypoint_overpass(wp, radius=100):
    """Cerca nodi/way OSM entro `radius` m dal waypoint.
    Se trova un elemento con nome simile, copia i tag utili.
    Usa query_overpass() per retry/backoff automatico su 504/429.
    Ritorna True se ha arricchito il waypoint."""
    lat, lon = wp["lat"], wp["lon"]
    query = (
        f"[out:json][timeout:10];"
        f"(node(around:{radius},{lat:.6f},{lon:.6f})[name];"
        f" way(around:{radius},{lat:.6f},{lon:.6f})[name];);"
        f"out center tags;"
    )
    try:
        result = query_overpass(query, retries=3)
    except Exception:
        return False

    best_score = 0.25         # soglia minima
    best_tags  = None
    best_dist  = float('inf')
    for el in result.get("elements", []):
        tags = el.get("tags", {})
        osm_name = tags.get("name", "")
        if not osm_name:
            continue
        score = _name_similarity(wp["name"], osm_name)
        # coordinate elemento
        if el["type"] == "node":
            elat, elon = el.get("lat", lat), el.get("lon", lon)
        else:
            c = el.get("center", {})
            elat, elon = c.get("lat", lat), c.get("lon", lon)
        dist = _haversine_m(lat, lon, elat, elon)
        # boost: se molto vicino (< 50m) abbassa la soglia
        effective_score = score if dist > 50 else max(score, 0.2 if score > 0.15 else 0)
        if effective_score > best_score or (effective_score == best_score and dist < best_dist):
            best_score = effective_score
            best_tags  = tags
            best_dist  = dist

    if not best_tags:
        return False

    enriched = False
    for key in _WPT_COPY_TAGS:
        if key in best_tags and key not in wp["tags"]:
            # Normalizza website
            if key in ("contact:website", "url") and "website" not in wp["tags"]:
                wp["tags"]["website"] = best_tags[key]
            elif key == "contact:phone" and "phone" not in wp["tags"]:
                wp["tags"]["phone"] = best_tags[key]
            else:
                wp["tags"][key] = best_tags[key]
            enriched = True
    return enriched

def enrich_waypoint_nominatim(wp, radius=300):
    """Fallback: ricerca Nominatim per nome + coordinate.
    Copia website e altri tag se trovati."""
    name  = urllib.parse.quote(wp["name"])
    lat, lon = wp["lat"], wp["lon"]
    url = (
        f"https://nominatim.openstreetmap.org/search"
        f"?q={name}&format=json&limit=3&addressdetails=0&extratags=1"
        f"&viewbox={lon-0.03:.4f},{lat+0.02:.4f},{lon+0.03:.4f},{lat-0.02:.4f}&bounded=1"
    )
    try:
        results = _fetch_json(url, timeout=8)
    except Exception:
        return False

    if not results:
        return False

    # Prendi il risultato con nome più simile
    best_score = 0.3
    best = None
    for r in results:
        score = _name_similarity(wp["name"], r.get("display_name", ""))
        if score > best_score:
            best_score = score
            best = r

    if not best:
        return False

    enriched = False
    extra = best.get("extratags", {}) or {}
    for key in _WPT_COPY_TAGS:
        if key in extra and key not in wp["tags"]:
            if key in ("contact:website", "url") and "website" not in wp["tags"]:
                wp["tags"]["website"] = extra[key]
            else:
                wp["tags"][key] = extra[key]
            enriched = True
    return enriched

# ─────────────────────────────────────────────
# DESCRIZIONI
# ─────────────────────────────────────────────
def get_osm_description(tags):
    for key in ("description", "description:it", "inscription", "subject:wikipedia"):
        if tags.get(key):
            return tags[key].strip()
    parts = []
    if tags.get("historic"):
        parts.append(tags["historic"].replace("_", " ").capitalize())
    if tags.get("heritage"):
        parts.append("Patrimonio UNESCO" if tags["heritage"] == "1" else "Patrimonio livello " + tags["heritage"])
    if tags.get("opening_hours"):
        parts.append("Orari: " + tags["opening_hours"])
    if tags.get("fee"):
        parts.append("Biglietto: " + ("gratuito" if tags["fee"].lower() in ("no", "free") else tags["fee"]))
    if tags.get("operator"):
        parts.append("Gestito da: " + tags["operator"])
    return " · ".join(parts) if parts else ""

def fetch_wikipedia_extract(wiki_tag, max_chars=WIKI_MAX_CHARS):
    if not wiki_tag:
        return ""
    if ":" in wiki_tag:
        lang, title = wiki_tag.split(":", 1)
    else:
        lang, title = "it", wiki_tag
    title = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GPX-POI-Extractor/1.0"})
        with urllib.request.urlopen(req, timeout=8, context=SSL_CONTEXT) as r:
            data = json.loads(r.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            if max_chars and len(extract) > max_chars:
                cut = extract.rfind(".", 0, max_chars)
                extract = extract[:cut+1] if cut > 0 else extract[:max_chars] + "…"
            return extract
    except Exception:
        return ""

def enrich_descriptions(pois):
    """Scarica descrizioni Wikipedia per tutti i POI che hanno il tag wikipedia
    e non hanno ancora una descrizione. Completa con dati OSM per gli altri."""
    wiki_pois  = [(i, p) for i, p in enumerate(pois)
                  if p["tags"].get("wikipedia") and not p.get("desc")]
    other_pois = [(i, p) for i, p in enumerate(pois)
                  if not p["tags"].get("wikipedia") and not p.get("desc")]

    WIKI_THREADS = 4
    if wiki_pois:
        print(f"\n📖 Scarico {len(wiki_pois)} descrizioni Wikipedia ({WIKI_THREADS} thread)...")
        import threading
        lock  = threading.Lock()
        done  = [0]
        total = len(wiki_pois)
        def _fetch_one(item):
            i, p = item
            desc = fetch_wikipedia_extract(p["tags"]["wikipedia"])
            result = desc if desc else get_osm_description(p["tags"])
            with lock:
                done[0] += 1
                print(f"   {done[0]}/{total} {p['name'][:45]}", end="\r")
            return i, result
        with ThreadPoolExecutor(max_workers=WIKI_THREADS) as ex:
            for i, desc in ex.map(_fetch_one, wiki_pois):
                pois[i]["desc"] = desc

    for i, p in other_pois:
        pois[i]["desc"] = get_osm_description(p["tags"])

    wiki_ok  = sum(1 for p in pois if p.get("desc") and p["tags"].get("wikipedia"))
    osm_ok   = sum(1 for p in pois if p.get("desc") and not p["tags"].get("wikipedia"))
    print(f"\n   ✅ Descrizioni: {wiki_ok} da Wikipedia, {osm_ok} da OSM")
    return pois

# ─────────────────────────────────────────────
# ESPORTAZIONE
# ─────────────────────────────────────────────
def xml_esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def export_gpx(pois, track, filepath, title="POI", km=None):
    """
    Genera un GPX con:
    - <trk> con il tracciato completo
    - <wpt> per ogni POI con <name>, <desc>, <type> e <extensions>
      contenente TUTTE le informazioni: desc, wikipedia, website, osm_id,
      dist_m, emoji, color, e tutti i tag OSM originali.
    """
    if km is None:
        km = round(total_distance_km(track)) if track else 0
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="GPX-POI-Extractor"',
        '     xmlns="http://www.topografix.com/GPX/1/1"',
        '     xmlns:poi="https://github.com/gpx-poi-extractor">',
        f'  <metadata>',
        f'    <name>{xml_esc(title)}</name>',
        f'    <desc>{len(pois)} POI entro {MAX_DIST_M} m dal percorso</desc>',
        f'    <time>{datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}</time>',
        f'    <extensions><poi:length_km>{km}</poi:length_km></extensions>',
        f'  </metadata>',
    ]

    # Waypoints (POI)
    for p in pois:
        cfg = POI_TYPES.get(p["type"], POI_TYPES["attraction"])
        is_wpt   = p["type"] == "komoot"
        has_wiki = bool(p["tags"].get("wikipedia", ""))
        has_web  = bool(p["tags"].get("website", ""))
        # <desc>: label+dist for OSM POI; always empty for waypoints (info in extensions)
        desc_tag = "" if is_wpt else \
                   (cfg[1] + (" — " + str(p["dist_m"]) + " m dal percorso" if p["dist_m"] > 0 else ""))
        # <type>/<poi:type>: human label for waypoints, key for OSM POI
        type_tag = cfg[1] if is_wpt else p["type"]
        lines += [
            f'  <wpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">',
            f'    <name>{xml_esc(p["name"])}</name>',
            f'    <desc>{xml_esc(desc_tag)}</desc>',
            f'    <type>{xml_esc(type_tag)}</type>',
            f'    <extensions>',
            f'      <poi:type>{xml_esc(p["type"])}</poi:type>',
            f'      <poi:label>{xml_esc(cfg[1])}</poi:label>',
            f'      <poi:emoji>{xml_esc(cfg[0])}</poi:emoji>',
            f'      <poi:color>{xml_esc(cfg[2])}</poi:color>',
            f'      <poi:dist_m>{p["dist_m"]}</poi:dist_m>',
            f'      <poi:osm_id>{xml_esc(p["osm_id"])}</poi:osm_id>',
        ]
        if p.get("desc"):
            lines.append(f'      <poi:description>{xml_esc(p["desc"])}</poi:description>')
        wiki = p["tags"].get("wikipedia", "")
        if wiki:
            lines.append(f'      <poi:wikipedia>{xml_esc(wiki)}</poi:wikipedia>')
        website = p["tags"].get("website", "")
        if website:
            lines.append(f'      <poi:website>{xml_esc(website)}</poi:website>')
        # All original OSM tags
        lines.append(f'      <poi:osm_tags>')
        for k, v in sorted(p["tags"].items()):
            safe_k = k.replace(":", "_").replace("-", "_")
            lines.append(f'        <poi:tag k="{xml_esc(k)}" v="{xml_esc(v)}"/>')
        lines.append(f'      </poi:osm_tags>')
        lines += [
            f'    </extensions>',
            f'  </wpt>',
        ]

    # Track
    lines += [
        '  <trk>',
        f'    <name>{xml_esc(title)}</name>',
        '    <trkseg>',
    ]
    for lat, lon in track:
        lines.append(f'      <trkpt lat="{lat:.7f}" lon="{lon:.7f}"/>')
    lines += [
        '    </trkseg>',
        '  </trk>',
        '</gpx>',
    ]

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

def export_csv(pois, filepath):
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=[
            "nome", "tipo", "tipo_etichetta", "latitudine", "longitudine",
            "distanza_m", "descrizione", "wikipedia", "website", "osm_id"
        ])
        w.writeheader()
        for p in pois:
            cfg = POI_TYPES.get(p["type"], POI_TYPES["attraction"])
            w.writerow({
                "nome":          p["name"],
                "tipo":          p["type"],
                "tipo_etichetta": cfg[1],
                "latitudine":    p["lat"],
                "longitudine":   p["lon"],
                "distanza_m":    p["dist_m"],
                "descrizione":   p.get("desc", ""),
                "wikipedia":     p["tags"].get("wikipedia", ""),
                "website":       p["tags"].get("website", ""),
                "osm_id":        p["osm_id"],
            })

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    import re as _re

    if len(sys.argv) < 2:
        print("Uso: python3 gpx_poi_extractor.py percorso.gpx")
        sys.exit(1)

    gpx_file = sys.argv[1]
    if not os.path.exists(gpx_file):
        print(f"❌ File non trovato: {gpx_file}")
        sys.exit(1)

    print(f"\n🚴 GPX POI Extractor — {gpx_file}")
    print("=" * 50)

    print("📂 Parsing GPX...")
    track = parse_gpx(gpx_file)
    dist  = total_distance_km(track)
    grid  = build_track_grid(track)
    print(f"   {len(track)} trackpoint · {dist:.1f} km  (griglia: {len(grid)} celle)")
    print(f"   Start: {track[0][0]:.5f}, {track[0][1]:.5f}")
    print(f"   End:   {track[-1][0]:.5f}, {track[-1][1]:.5f}")

    # Leggi waypoint dal GPX sorgente
    gpx_waypoints = parse_gpx_waypoints(gpx_file)
    if gpx_waypoints:
        print(f"   📍 {len(gpx_waypoints)} waypoint trovati nel file GPX")

    bboxes = get_segment_bboxes(track)
    print(f"\n📦 {len(bboxes)} segmenti da interrogare (~{SEGMENT_KM} km ciascuno)")
    print(f"   Buffer bbox: {BUFFER_KM} km · Filtro finale: {MAX_DIST_M} m\n")

    all_pois = []
    seen_ids = set()

    for i, bbox in enumerate(bboxes):
        pct = int((i / len(bboxes)) * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"[{bar}] {pct:3d}%  Segmento {i+1:2d}/{len(bboxes)}  —  POI finora: {len(all_pois)}", end="\r")
        try:
            data = query_overpass(build_query(bbox))
            for el in data.get("elements", []):
                eid = f"{el['type']}-{el['id']}"
                if eid in seen_ids:
                    continue
                lat = el.get("lat") or (el.get("center") or {}).get("lat")
                lon = el.get("lon") or (el.get("center") or {}).get("lon")
                if not lat or not lon:
                    continue
                dist_m = dist_point_to_track(lat, lon, track, grid)
                if dist_m > MAX_DIST_M:
                    continue
                tags = el.get("tags", {})
                poi_type = classify(tags)
                poi_name = get_name(tags)
                if poi_name == "(senza nome)":
                    continue
                if poi_type == "artwork":
                    continue
                seen_ids.add(eid)
                all_pois.append({
                    "osm_id": eid,
                    "lat": lat, "lon": lon,
                    "type": poi_type,
                    "name": poi_name,
                    "dist_m": round(dist_m),
                    "tags": tags,
                })
            time.sleep(PAUSE_BETWEEN_S)
        except Exception as e:
            print(f"\n   ⚠️  Segmento {i+1} fallito: {e}")
            time.sleep(3)

    # Sort by progressive position along track
    cum_dist = [0.0]
    for k in range(1, len(track)):
        cum_dist.append(cum_dist[-1] + haversine(track[k-1][0], track[k-1][1], track[k][0], track[k][1]))

    def track_progress(p):
        row, col = int(p["lat"]/0.01), int(p["lon"]/0.01)
        cands = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                cands.extend(grid.get((row+dr, col+dc), []))
        if not cands:
            cands = range(len(track))
        best_d, best_k = float("inf"), 0
        for k in cands:
            d = haversine(p["lat"], p["lon"], track[k][0], track[k][1])
            if d < best_d:
                best_d = d; best_k = k
        return cum_dist[best_k]

    all_pois.sort(key=track_progress)

    # Deduplicate: same name + within 250m
    def poi_score(p):
        score = len(p["tags"])
        if p["tags"].get("wikipedia"): score += 20
        if p["tags"].get("website"):   score += 10
        return score

    deduped = []
    for p in all_pois:
        merged = False
        for i, kept in enumerate(deduped):
            if p["name"].strip().lower() == kept["name"].strip().lower():
                if haversine(p["lat"], p["lon"], kept["lat"], kept["lon"]) < 250:
                    if poi_score(p) > poi_score(kept):
                        deduped[i] = p
                    merged = True
                    break
        if not merged:
            deduped.append(p)

    removed = len(all_pois) - len(deduped)
    if removed:
        print(f"   🔁 Rimossi {removed} doppioni (stesso nome, distanza < 250 m)")
    all_pois = deduped

    print(f"\n\n✅ {len(all_pois)} POI trovati entro {MAX_DIST_M} m dal percorso\n")
    from collections import Counter
    counts = Counter(p["type"] for p in all_pois)
    for ptype, cfg in POI_TYPES.items():
        if counts[ptype]:
            print(f"   {cfg[0]}  {cfg[1]:<22} {counts[ptype]:>3}")

    # Aggiungi waypoint GPX (Komoot) — arricchisci con OSM/Nominatim + Wikipedia
    if gpx_waypoints:
        print(f"\n🚴 Waypoint GPX ({len(gpx_waypoints)}) — arricchimento OSM/Wikipedia...")
        wpt_seen_names = {p["name"].strip().lower() for p in all_pois}
        added = []
        osm_hits = wiki_hits = 0
        # Overpass+Nominatim in sequenza (evita 504 da troppe query parallele)
        # Wikipedia in parallelo (API diversa, nessun conflitto)
        wp_candidates = [wp for wp in gpx_waypoints
                         if wp["name"].strip().lower() not in wpt_seen_names]

        for n, wp in enumerate(wp_candidates, 1):
            print(f"   {n}/{len(wp_candidates)} {wp['name'][:40]}", end="\r")
            if enrich_waypoint_overpass(wp):
                osm_hits += 1
            else:
                if enrich_waypoint_nominatim(wp):
                    osm_hits += 1
            added.append(wp)
            wpt_seen_names.add(wp["name"].strip().lower())
            time.sleep(3)

        # Wikipedia in parallelo sui waypoint senza tag wikipedia.
        # Strategia: prima prova titolo esatto (veloce), poi search API (robusta).
        def _wp_wiki(wp):
            name = wp["name"]
            for lang in ("it", "en"):
                # 1) Titolo esatto
                url = (f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                       f"{urllib.parse.quote(name.replace(' ', '_'))}")
                try:
                    data = _fetch_json(url, timeout=6)
                    if data.get("extract", "").strip():
                        wp["tags"]["wikipedia"] = f"{lang}:{data.get('title', name)}"
                        return True
                except Exception:
                    pass
                # 2) Search API — trova il titolo più simile al nome del waypoint
                search_url = (
                    f"https://{lang}.wikipedia.org/w/api.php"
                    f"?action=query&list=search&srsearch={urllib.parse.quote(name)}"
                    f"&srlimit=3&format=json&utf8=1"
                )
                try:
                    sdata = _fetch_json(search_url, timeout=8)
                    results = (sdata.get("query") or {}).get("search", [])
                    for hit in results:
                        hit_title = hit.get("title", "")
                        if _name_similarity(name, hit_title) >= 0.25:
                            sum_url = (f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                                       f"{urllib.parse.quote(hit_title.replace(' ', '_'))}")
                            try:
                                sum_data = _fetch_json(sum_url, timeout=6)
                                if sum_data.get("extract", "").strip():
                                    wp["tags"]["wikipedia"] = f"{lang}:{sum_data.get('title', hit_title)}"
                                    return True
                            except Exception:
                                pass
                except Exception:
                    pass
            return False

        wiki_cands = [wp for wp in added if not wp["tags"].get("wikipedia")]
        if wiki_cands:
            with ThreadPoolExecutor(max_workers=4) as ex:
                for wp, hit in zip(wiki_cands, ex.map(_wp_wiki, wiki_cands)):
                    if hit: wiki_hits += 1

        print(f"   ✅ {len(added)} waypoint aggiunti"
              f" ({osm_hits} con dati OSM, {wiki_hits} con Wikipedia)")
        all_pois.extend(added)

    # Enrich with descriptions
    all_pois = enrich_descriptions(all_pois)

    # Derive clean title from filename
    gpx_title = os.path.splitext(os.path.basename(gpx_file))[0].replace("_", " ")
    gpx_title = _re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", gpx_title)
    gpx_title = _re.sub(r"^\d+\s+", "", gpx_title).strip()

    base    = os.path.splitext(gpx_file)[0]
    gpx_out = f"{base}_poi.gpx"
    csv_out = f"{base}_poi.csv"

    export_gpx(all_pois, track, gpx_out, title=gpx_title, km=round(dist))
    print(f"\n💾 GPX  → {gpx_out}")

    export_csv(all_pois, csv_out)
    print(f"💾 CSV  → {csv_out}")

    print(f"\n🎉 Fatto! Per generare la mappa HTML:")
    print(f"   python3 gpx_poi_to_html.py {gpx_out}\n")

if __name__ == "__main__":
    main()
