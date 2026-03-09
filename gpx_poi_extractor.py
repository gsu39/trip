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
PAUSE_BETWEEN_S  = 12     # pausa tra query (rispetta rate limit Overpass)
OVERPASS_TIMEOUT = 60
WIKI_MAX_CHARS   = 1000   # lunghezza massima estratto Wikipedia (nessun taglio per il GPX)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

POI_TYPES = {
    "viewpoint":  ("🏔️",  "Panoramico",        "#FF6B35"),
    "museum":     ("🏛️",  "Museo",             "#9B59B6"),
    "gallery":    ("🖼️",  "Galleria d'arte",   "#F44336"),
    "artwork":    ("🎨",  "Arte/Monumento",    "#E91E63"),
    "attraction": ("⭐",  "Attrazione",        "#2196F3"),
    "historic":   ("🏰",  "Sito storico",      "#795548"),
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

def dist_point_to_track(lat, lon, track, coarse_step=8):
    best_dist = float('inf')
    best_idx = 0
    for i in range(0, len(track), coarse_step):
        d = haversine(lat, lon, track[i][0], track[i][1])
        if d < best_dist:
            best_dist = d
            best_idx = i
    start = max(0, best_idx - coarse_step)
    end   = min(len(track) - 1, best_idx + coarse_step)
    for i in range(start, end + 1):
        d = haversine(lat, lon, track[i][0], track[i][1])
        if d < best_dist:
            best_dist = d
    return best_dist

def total_distance_km(track):
    return sum(haversine(track[i-1][0], track[i-1][1], track[i][0], track[i][1])
               for i in range(1, len(track))) / 1000

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
    wiki_pois  = [(i, p) for i, p in enumerate(pois) if p["tags"].get("wikipedia")]
    other_pois = [(i, p) for i, p in enumerate(pois) if not p["tags"].get("wikipedia")]

    if wiki_pois:
        print(f"\n📖 Scarico descrizioni Wikipedia per {len(wiki_pois)} POI...")
    for idx, (i, p) in enumerate(wiki_pois):
        print(f"   {idx+1}/{len(wiki_pois)} {p['name'][:40]}", end="\r")
        desc = fetch_wikipedia_extract(p["tags"]["wikipedia"])
        pois[i]["desc"] = desc if desc else get_osm_description(p["tags"])
        time.sleep(0.3)

    for i, p in other_pois:
        pois[i]["desc"] = get_osm_description(p["tags"])

    filled = sum(1 for p in pois if p.get("desc"))
    print(f"\n   ✅ Descrizioni: {filled}/{len(pois)} POI con testo")
    return pois

# ─────────────────────────────────────────────
# ESPORTAZIONE
# ─────────────────────────────────────────────
def xml_esc(s):
    return str(s).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def export_gpx(pois, track, filepath, title="POI"):
    """
    Genera un GPX con:
    - <trk> con il tracciato completo
    - <wpt> per ogni POI con <name>, <desc>, <type> e <extensions>
      contenente TUTTE le informazioni: desc, wikipedia, website, osm_id,
      dist_m, emoji, color, e tutti i tag OSM originali.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="GPX-POI-Extractor"',
        '     xmlns="http://www.topografix.com/GPX/1/1"',
        '     xmlns:poi="https://github.com/gpx-poi-extractor">',
        f'  <metadata>',
        f'    <name>{xml_esc(title)}</name>',
        f'    <desc>{len(pois)} POI entro {MAX_DIST_M} m dal percorso</desc>',
        f'    <time>{datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}</time>',
        f'  </metadata>',
    ]

    # Waypoints (POI)
    for p in pois:
        cfg = POI_TYPES.get(p["type"], POI_TYPES["attraction"])
        lines += [
            f'  <wpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">',
            f'    <name>{xml_esc(p["name"])}</name>',
            f'    <desc>{xml_esc(cfg[1])} — {p["dist_m"]} m dal percorso</desc>',
            f'    <type>{xml_esc(p["type"])}</type>',
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
    print(f"   {len(track)} trackpoint · {dist:.1f} km")
    print(f"   Start: {track[0][0]:.5f}, {track[0][1]:.5f}")
    print(f"   End:   {track[-1][0]:.5f}, {track[-1][1]:.5f}")

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
                dist_m = dist_point_to_track(lat, lon, track)
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
        best_d, best_k = float("inf"), 0
        for k, (tlat, tlon) in enumerate(track):
            d = haversine(p["lat"], p["lon"], tlat, tlon)
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

    # Enrich with descriptions
    all_pois = enrich_descriptions(all_pois)

    # Derive clean title from filename
    gpx_title = os.path.splitext(os.path.basename(gpx_file))[0].replace("_", " ")
    gpx_title = _re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", gpx_title)
    gpx_title = _re.sub(r"^\d+\s+", "", gpx_title).strip()

    base    = os.path.splitext(gpx_file)[0]
    gpx_out = f"{base}_poi.gpx"
    csv_out = f"{base}_poi.csv"

    export_gpx(all_pois, track, gpx_out, title=gpx_title)
    print(f"\n💾 GPX  → {gpx_out}")

    export_csv(all_pois, csv_out)
    print(f"💾 CSV  → {csv_out}")

    print(f"\n🎉 Fatto! Per generare la mappa HTML:")
    print(f"   python3 gpx_poi_to_html.py {gpx_out}\n")

if __name__ == "__main__":
    main()
