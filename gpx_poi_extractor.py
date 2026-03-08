#!/usr/bin/env python3
"""
GPX POI Extractor
==================================
Estrae POI culturali entro 1 km da un percorso GPX usando l'API Overpass (OpenStreetMap).

USO:
    python3 gpx_poi_extractor.py percorso.gpx

OUTPUT:
    - <nome>_poi.gpx   → importabile su Garmin, Komoot, OsmAnd, ecc.
    - <nome>_poi.csv   → apribile con Excel/LibreOffice
    - <nome>_poi.html  → mappa interattiva nel browser

REQUISITI:
    Python 3.6+ — nessuna libreria esterna necessaria
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
MAX_DIST_M       = 1000    # distanza massima dal percorso in metri
SEGMENT_KM       = 40      # lunghezza segmento per query Overpass
BUFFER_KM        = 1.3     # buffer bbox oltre il percorso
PAUSE_BETWEEN_S  = 12         # pausa tra query (rispetta rate limit Overpass)
OVERPASS_TIMEOUT = 60

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
    """Distanza in metri tra due coordinate."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def dist_point_to_track(lat, lon, track, coarse_step=8):
    """Distanza minima in metri da un punto alla traccia GPX."""
    # Passata grossolana
    best_dist = float('inf')
    best_idx = 0
    for i in range(0, len(track), coarse_step):
        d = haversine(lat, lon, track[i][0], track[i][1])
        if d < best_dist:
            best_dist = d
            best_idx = i
    # Raffinamento attorno al punto migliore
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
    ns_map = {'gpx': ns} if ns else {}
    
    def find_all(tag):
        if ns:
            return root.findall(f'.//{{' + ns + f'}}{tag}')
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
                min(lats) - lat_buf,
                min(lons) - lon_buf,
                max(lats) + lat_buf,
                max(lons) + lon_buf
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
                    print(f"\n    ⏳ Rate limit (429) — attendo {wait}s prima di riprovare...")
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
# ESPORTAZIONE
# ─────────────────────────────────────────────
def get_osm_description(tags):
    """Extract a description from OSM tags."""
    for key in ("description", "description:it", "inscription", "subject:wikipedia"):
        if tags.get(key):
            return tags[key].strip()
    # Build from available structured tags
    parts = []
    if tags.get("historic"):
        parts.append(tags["historic"].replace("_", " ").capitalize())
    if tags.get("heritage"):
        parts.append("Patrimonio UNESCO" if tags["heritage"] == "1" else "Patrimonio livello " + tags["heritage"])
    if tags.get("opening_hours"):
        parts.append("Orari: " + tags["opening_hours"])
    if tags.get("fee"):
        parts.append("Biglietto: " + ("gratuito" if tags["fee"].lower() in ("no","free") else tags["fee"]))
    if tags.get("operator"):
        parts.append("Gestito da: " + tags["operator"])
    return " · ".join(parts) if parts else ""


def fetch_wikipedia_extract(wiki_tag, max_chars=300):
    """Fetch first paragraph from Wikipedia API. Returns string or empty."""
    if not wiki_tag:
        return ""
    # wiki_tag is like "it:Torre Normanna" or "Torre Normanna"
    if ":" in wiki_tag:
        lang, title = wiki_tag.split(":", 1)
    else:
        lang, title = "it", wiki_tag
    title = title.replace(" ", "_")
    url = (f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(title)}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GPX-POI-Extractor/1.0"})
        with urllib.request.urlopen(req, timeout=8, context=SSL_CONTEXT) as r:
            data = json.loads(r.read().decode("utf-8"))
            extract = data.get("extract", "").strip()
            # Trim to max_chars at sentence boundary
            if len(extract) > max_chars:
                cut = extract.rfind(".", 0, max_chars)
                extract = extract[:cut+1] if cut > 0 else extract[:max_chars] + "…"
            return extract
    except Exception:
        return ""


def enrich_descriptions(pois):
    """Add 'desc' field to each POI: Wikipedia extract or OSM tags fallback."""
    wiki_pois = [(i, p) for i, p in enumerate(pois) if p["tags"].get("wikipedia")]
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


def export_gpx(pois, filepath, title="POI"):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="GPX-POI-Extractor" xmlns="http://www.topografix.com/GPX/1/1">',
             f'  <metadata><name>POI {title} — {len(pois)} punti</name></metadata>']
    for p in pois:
        cfg = POI_TYPES.get(p["type"], POI_TYPES["attraction"])
        name_esc = p["name"].replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")
        lines += [
            f'  <wpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">',
            f'    <name>{name_esc}</name>',
            f'    <desc>{cfg[1]} — {p["dist_m"]} m dal percorso</desc>',
            f'    <type>{p["type"]}</type>',
            f'  </wpt>'
        ]
    lines.append('</gpx>')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

def export_csv(pois, filepath):
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=[
            "nome","tipo","tipo_etichetta","latitudine","longitudine",
            "distanza_m","wikipedia","website","osm_id"
        ])
        w.writeheader()
        for p in pois:
            cfg = POI_TYPES.get(p["type"], POI_TYPES["attraction"])
            w.writerow({
                "nome": p["name"],
                "tipo": p["type"],
                "tipo_etichetta": cfg[1],
                "latitudine": p["lat"],
                "longitudine": p["lon"],
                "distanza_m": p["dist_m"],
                "wikipedia": p["tags"].get("wikipedia",""),
                "website": p["tags"].get("website",""),
                "osm_id": p["osm_id"],
            })



def export_html(pois, track, filepath, title="POI"):
    track_dec = [[round(p[0],6), round(p[1],6)] for p in track[::10]]
    poi_list  = [{
        "lat":   p["lat"],
        "lon":   p["lon"],
        "name":  p["name"],
        "type":  p["type"],
        "dist":  p["dist_m"],
        "label": POI_TYPES.get(p["type"], POI_TYPES["attraction"])[1],
        "emoji": POI_TYPES.get(p["type"], POI_TYPES["attraction"])[0],
        "color": POI_TYPES.get(p["type"], POI_TYPES["attraction"])[2],
        "wiki":  p["tags"].get("wikipedia", ""),
        "web":   p["tags"].get("website",   ""),
        "desc":  p.get("desc", ""),
    } for p in pois]
    types_dict = {k: {"emoji": v[0], "label": v[1], "color": v[2]} for k, v in POI_TYPES.items()}

    data_block = (
        "window.APP_TRACK = " + json.dumps(track_dec)  + ";\n" +
        "window.APP_POIS  = " + json.dumps(poi_list)   + ";\n" +
        "window.APP_TYPES = " + json.dumps(types_dict) + ";\n"
    )

    poi_count = len(pois)
    total_km  = round(total_distance_km(track))
    now_str   = datetime.now().strftime("%d/%m/%Y %H:%M")
    first_lat = track[0][0]
    first_lon = track[0][1]

    static_js = """
(function() {
  var TRACK = window.APP_TRACK;
  var POIS  = window.APP_POIS;
  var TYPES = window.APP_TYPES;

  var map = L.map("map", {zoomControl: true});
  map.setView([APP_START_LAT, APP_START_LON], 8);

  L.tileLayer(
    "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
    {
      attribution: "&copy; CyclOSM &copy; OpenStreetMap contributors",
      maxZoom: 20
    }
  ).addTo(map);

  L.polyline(TRACK, {color: "#e94560", weight: 3, opacity: 0.85}).addTo(map);

  var markers = [];
  var active  = {};
  Object.keys(TYPES).forEach(function(t) { active[t] = true; });

  function makeIcon(type) {
    var c = TYPES[type] || TYPES["attraction"];
    return L.divIcon({
      html: "<div style=\\"background:" + c.color + ";width:26px;height:26px;"
          + "border-radius:50%;display:flex;align-items:center;justify-content:center;"
          + "font-size:13px;border:2px solid white;box-shadow:0 2px 5px rgba(0,0,0,0.35)\\">"
          + c.emoji + "</div>",
      iconSize:   [26, 26],
      iconAnchor: [13, 13],
      className:  ""
    });
  }

  function makePopupHTML(p) {
    var s = "<b>" + p.emoji + " " + p.name + "</b>"
          + "<br><small style='color:#555'>" + p.label + "</small>"
          + "<br><small style='color:#2a9d8f'>" + p.dist + " m dal percorso</small>";
    if (p.wiki) {
      var wt = p.wiki.replace("it:", "");
      s += "<br><a href='https://it.wikipedia.org/wiki/" + encodeURIComponent(wt)
        +  "' target='_blank' style='font-size:0.8em'>Wikipedia</a>";
    }
    if (p.web) {
      s += "<br><a href='" + p.web + "' target='_blank' style='font-size:0.8em'>Sito web</a>";
    }
    return s;
  }

  var selectedRow = null;

  function selectPOI(row, m, p) {
    // Deselect previous
    if (selectedRow) selectedRow.classList.remove("selected");
    selectedRow = row;
    row.classList.add("selected");
    row.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function render() {
    markers.forEach(function(m) { m.remove(); });
    markers = [];
    selectedRow = null;
    var list    = document.getElementById("list");
    var counter = document.getElementById("vis-count");
    list.innerHTML = "";
    var visible = [];
    for (var i = 0; i < POIS.length; i++) {
      if (active[POIS[i].type]) visible.push(POIS[i]);
    }
    counter.textContent = visible.length;
    for (var j = 0; j < visible.length; j++) {
      (function(p) {
        var m = L.marker([p.lat, p.lon], {icon: makeIcon(p.type)})
                  .bindPopup(makePopupHTML(p))
                  .addTo(map);
        markers.push(m);

        var row  = document.createElement("div");  row.className  = "item";
        var ico  = document.createElement("div");  ico.className  = "ico";
        var body = document.createElement("div");  body.style.flex = "1"; body.style.minWidth = "0";
        var nm   = document.createElement("div");  nm.className   = "iname";
        var mt   = document.createElement("div");  mt.className   = "imeta";
        var ds   = document.createElement("div");  ds.className   = "idist";
        var dc   = document.createElement("div");  dc.className   = "idesc";

        // Navigate button (Apple Maps) — visible only when row is selected
        var nav  = document.createElement("a");
        nav.className = "inav";
        nav.href      = "https://maps.apple.com/?ll=" + p.lat + "," + p.lon
                      + "&q=" + encodeURIComponent(p.name) + "&dirflg=w";
        nav.target    = "_blank";
        nav.title     = "Naviga con Apple Maps";
        nav.innerHTML = "&#x27A1;";
        nav.addEventListener("click", function(e) { e.stopPropagation(); });

        ico.textContent = p.emoji;
        nm.textContent  = p.name;
        mt.textContent  = p.label;
        ds.textContent  = p.dist + " m dal percorso";
        if (p.desc) dc.textContent = p.desc;
        body.appendChild(nm); body.appendChild(mt); body.appendChild(ds);
        if (p.desc) body.appendChild(dc);
        row.appendChild(ico); row.appendChild(body); row.appendChild(nav);

        // Marker click → highlight row + scroll
        m.on("click", function() {
          selectPOI(row, m, p);
        });

        row.addEventListener("click", function() {
          selectPOI(row, m, p);
          // Reopen map if collapsed (mobile)
          if (mapEl.classList.contains("collapsed")) {
            mapEl.classList.remove("collapsed");
            toggleBtn.innerHTML = "&#x25B2;Mappa";
            toggleBtn.setAttribute("title", "Nascondi mappa");
            mapVisible = true;
            setTimeout(function() {
              map.invalidateSize();
              map.setView([p.lat, p.lon], 16);
              m.openPopup();
            }, 320);
          } else {
            map.setView([p.lat, p.lon], 16);
            m.openPopup();
          }
        });
        list.appendChild(row);
      })(visible[j]);
    }
  }

  var filterDiv = document.getElementById("filters");
  Object.keys(TYPES).forEach(function(type) {
    var cfg = TYPES[type];
    var btn = document.createElement("button");
    btn.className            = "fbtn";
    btn.style.borderColor    = cfg.color;
    btn.style.background     = cfg.color;
    btn.style.color          = "#111";
    btn.textContent          = cfg.emoji + " " + cfg.label;
    btn.addEventListener("click", function() {
      active[type]         = !active[type];
      btn.style.background = active[type] ? cfg.color : "transparent";
      btn.style.color      = active[type] ? "#111"    : "#eee";
      render();
    });
    filterDiv.appendChild(btn);
  });

  render();

  // Geolocation — current position
  var locationMarker  = null;
  var locationCircle  = null;
  var locationWatcher = null;

  function updateLocation(e) {
    var latlng   = e.latlng;
    var accuracy = e.accuracy;
    if (locationMarker) {
      locationMarker.setLatLng(latlng);
      locationCircle.setLatLng(latlng).setRadius(accuracy);
    } else {
      locationCircle = L.circle(latlng, {
        radius: accuracy, color: "#4ecca3", fillColor: "#4ecca3",
        fillOpacity: 0.15, weight: 1
      }).addTo(map);
      locationMarker = L.circleMarker(latlng, {
        radius: 7, color: "white", weight: 2,
        fillColor: "#4ecca3", fillOpacity: 1
      }).addTo(map);
    }
  }

  var locBtn = document.getElementById("loc-btn");
  var locTracking = false;
  locBtn.addEventListener("click", function() {
    if (!navigator.geolocation) {
      alert("Geolocalizzazione non supportata da questo browser.");
      return;
    }
    if (locTracking) {
      // Stop tracking
      if (locationWatcher !== null) navigator.geolocation.clearWatch(locationWatcher);
      if (locationMarker)  { locationMarker.remove();  locationMarker  = null; }
      if (locationCircle)  { locationCircle.remove();  locationCircle  = null; }
      locTracking = false;
      locBtn.classList.remove("active");
      locBtn.title = "Mostra posizione";
    } else {
      // Start tracking
      locTracking = true;
      locBtn.classList.add("active");
      locBtn.title = "Nascondi posizione";
      locationWatcher = navigator.geolocation.watchPosition(
        function(pos) {
          var e = {
            latlng:   L.latLng(pos.coords.latitude, pos.coords.longitude),
            accuracy: pos.coords.accuracy
          };
          updateLocation(e);
          // Pan to position on first fix
          if (!locationMarker || locationMarker._firstFix === undefined) {
            map.setView(e.latlng, map.getZoom());
            if (locationMarker) locationMarker._firstFix = true;
          }
        },
        function(err) {
          locTracking = false;
          locBtn.classList.remove("active");
          var errMsg = err.message || "";
          var errCodes = {1:"Permesso negato (abilita la localizzazione in Impostazioni → Privacy → Siti web di Safari)",
                          2:"Posizione non disponibile", 3:"Timeout"};
          alert("Errore geolocalizzazione: " + (errCodes[err.code] || errMsg || "codice " + err.code));
        },
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 10000 }
      );
    }
  });

  // Map toggle — only active on mobile (button visible via CSS media query)
  var mapEl     = document.getElementById('map');
  var toggleBtn = document.getElementById('map-toggle');
  var mapVisible = true;
  toggleBtn.addEventListener('click', function() {
    mapVisible = !mapVisible;
    if (mapVisible) {
      mapEl.classList.remove('collapsed');
      toggleBtn.setAttribute('title', 'Nascondi mappa');
      toggleBtn.innerHTML = '&#x25B2;Mappa';
      setTimeout(function() { map.invalidateSize(); }, 310);
    } else {
      mapEl.classList.add('collapsed');
      toggleBtn.setAttribute('title', 'Mostra mappa');
      toggleBtn.innerHTML = '&#x25BC;Mappa';
    }
  });
})();
"""

    static_js = static_js.replace("APP_START_LAT", str(first_lat))
    static_js = static_js.replace("APP_START_LON", str(first_lon))

    lines = []
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="it">')
    lines.append('<head>')
    lines.append('<meta charset="UTF-8">')
    lines.append('<meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append('<title>' + title + ' (' + str(poi_count) + ' punti)</title>')
    lines.append('<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>')
    lines.append('<style>')
    lines.append('* { box-sizing: border-box; margin: 0; padding: 0; }')
    lines.append('html { height: 100%; }')
    lines.append('body { height: 100%; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;')
    lines.append('       background: #1a1a2e; color: #eee; display: flex; flex-direction: row; overflow: hidden; }')
    lines.append('#sidebar { width: 300px; flex-shrink: 0; background: #16213e; display: flex;')
    lines.append('           flex-direction: column; overflow: hidden; border-right: 1px solid #0f3460; }')
    lines.append('#map { flex: 1; min-width: 0; min-height: 0; }')
    lines.append('.hdr-btn { background: #0f3460; border: 1px solid #4ecca3;')
    lines.append('  color: #4ecca3; padding: 4px 0; font-size: 0.72rem; cursor: pointer;')
    lines.append('  -webkit-appearance: none; appearance: none; border-radius: 4px;')
    lines.append('  white-space: nowrap; flex-shrink: 0; width: 58px; text-align: center; }')
    lines.append('#map-toggle { display: none; }')
    lines.append('.hdr { padding: 12px 14px; background: #0f3460; flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 8px; }')
    lines.append('.hdr h1 { font-size: 0.95rem; color: #e94560; }')
    lines.append('.hdr p  { font-size: 0.72rem; color: #aaa; margin-top: 3px; }')
    lines.append('.stats { padding: 8px 12px; border-bottom: 1px solid #0f3460; flex-shrink: 0;')
    lines.append('         display: flex; gap: 6px; }')
    lines.append('.stat  { background: #0f3460; border-radius: 5px; padding: 6px 8px;')
    lines.append('         text-align: center; flex: 1; }')
    lines.append('.stat .v { font-size: 0.95rem; font-weight: 700; color: #4ecca3; }')
    lines.append('.stat .l { font-size: 0.62rem; color: #888; }')
    lines.append('.filters { padding: 8px 12px; border-bottom: 1px solid #0f3460; flex-shrink: 0;')
    lines.append('           display: flex; flex-wrap: wrap; gap: 5px; }')
    lines.append('.fbtn { padding: 4px 9px; border-radius: 20px; border: 1px solid;')
    lines.append('        font-size: 0.7rem; cursor: pointer; appearance: none; -webkit-appearance: none; }')
    lines.append('#list { flex: 1; overflow-y: auto; -webkit-overflow-scrolling: touch; }')
    lines.append('.item { padding: 10px 12px; border-bottom: 1px solid #0f3460; cursor: pointer;')
    lines.append('        display: flex; gap: 8px; align-items: flex-start; transition: background 0.15s; }')
    lines.append('.item.selected { background: rgba(233,69,96,0.18); border-left: 3px solid #e94560; padding-left: 9px; }')
    lines.append('.ico   { font-size: 1.1rem; flex-shrink: 0; }')
    lines.append('.iname { font-size: 0.78rem; font-weight: 600; line-height: 1.3; }')
    lines.append('.imeta { font-size: 0.67rem; color: #888; }')
    lines.append('.idist { font-size: 0.67rem; color: #4ecca3; }')
    lines.append('.idesc { display: none; font-size: 0.7rem; color: #bbb; margin-top: 5px; line-height: 1.45; border-top: 1px solid #0f3460; padding-top: 5px; }')
    lines.append('.item.selected .idesc { display: block; }')
    lines.append('#loc-btn { transition: background 0.2s, color 0.2s; }')
    lines.append('#loc-btn.active { background: #4ecca3; color: #111; }')
    lines.append('.inav  { display: none; margin-left: auto; flex-shrink: 0; align-self: center;')
    lines.append('         background: #e94560; border: none; border-radius: 50%; width: 28px; height: 28px;')
    lines.append('         font-size: 0.85rem; cursor: pointer; color: white; text-decoration: none;')
    lines.append('         align-items: center; justify-content: center; }')
    lines.append('.item.selected .inav { display: flex; }')
    lines.append('@media (max-width: 600px) {')
    lines.append('  body { flex-direction: column; }')
    lines.append('  #sidebar { width: 100%; flex: 1; min-height: 0; border-right: none; border-bottom: 1px solid #0f3460; display: flex; flex-direction: column; }')
    lines.append('  #list { flex: 1; min-height: 0; }')
    lines.append('  #map { height: 50vh; flex: none; transition: height 0.3s ease; }')
    lines.append('  #map.collapsed { height: 0 !important; min-height: 0; overflow: hidden; }')
    lines.append('  #map-toggle { display: inline-block; }')
    lines.append('}')
    lines.append('</style>')
    lines.append('</head>')
    lines.append('<body>')
    lines.append('<div id="sidebar">')
    lines.append('  <div class="hdr">')
    lines.append('    <div>')
    lines.append('      <h1>&#x1F6B4; ' + title + '</h1>')
    lines.append('      <p>Generato il ' + now_str + ' &middot; entro 1 km</p>')
    lines.append('    </div>')
    lines.append('    <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">')
    lines.append('      <button id="loc-btn" class="hdr-btn" title="Mostra posizione">&#x2299; You</button>')
    lines.append('      <button id="map-toggle" class="hdr-btn" title="Nascondi mappa">&#x25B2;Mappa</button>')
    lines.append('    </div>')
    lines.append('  </div>')
    lines.append('  <div class="stats">')
    lines.append('    <div class="stat"><div class="v">'   + str(poi_count) + '</div><div class="l">POI</div></div>')
    lines.append('    <div class="stat"><div class="v" id="vis-count">' + str(poi_count) + '</div><div class="l">Visibili</div></div>')
    lines.append('    <div class="stat"><div class="v">'   + str(total_km)  + '</div><div class="l">Km</div></div>')
    lines.append('  </div>')
    lines.append('  <div class="filters" id="filters"></div>')
    lines.append('  <div id="list"></div>')
    lines.append('</div>')
    lines.append('<div id="map"></div>')
    lines.append('<script id="app-data">')
    lines.append(data_block)
    lines.append('</script>')
    lines.append('<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>')
    lines.append('<script>')
    lines.append(static_js)
    lines.append('</script>')
    lines.append('</body>')
    lines.append('</html>')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))



# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    gpx_file = sys.argv[1] if len(sys.argv) > 1 else "percorso.gpx"
    
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
            query = build_query(bbox)
            data  = query_overpass(query)

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

    # Sort by progressive distance along track (exact: find closest trackpoint)
    cum_dist = [0.0]
    for k in range(1, len(track)):
        cum_dist.append(cum_dist[-1] + haversine(track[k-1][0], track[k-1][1], track[k][0], track[k][1]))

    def track_progress(p):
        best_d = float("inf")
        best_k = 0
        for k, (tlat, tlon) in enumerate(track):
            d = haversine(p["lat"], p["lon"], tlat, tlon)
            if d < best_d:
                best_d = d
                best_k = k
        return cum_dist[best_k]

    all_pois.sort(key=track_progress)

    # Remove duplicates: same name + within 50m of each other
    def poi_score(p):
        """Higher score = better candidate to keep."""
        score = len(p["tags"])
        if p["tags"].get("wikipedia"): score += 20
        if p["tags"].get("website"):   score += 10
        return score

    deduped = []
    for p in all_pois:
        merged = False
        for i, kept in enumerate(deduped):
            if p["name"].strip().lower() == kept["name"].strip().lower():
                if haversine(p["lat"], p["lon"], kept["lat"], kept["lon"]) < 50:
                    # Keep the one with higher score
                    if poi_score(p) > poi_score(kept):
                        deduped[i] = p
                    merged = True
                    break
        if not merged:
            deduped.append(p)

    removed = len(all_pois) - len(deduped)
    if removed:
        print(f"   🔁 Rimossi {removed} doppioni (stesso nome, distanza < 50 m)")
    all_pois = deduped

    print(f"\n\n✅ Completato! {len(all_pois)} POI trovati entro {MAX_DIST_M} m dal percorso\n")

    # Type breakdown
    from collections import Counter
    counts = Counter(p["type"] for p in all_pois)
    for ptype, cfg in POI_TYPES.items():
        if counts[ptype]:
            print(f"   {cfg[0]}  {cfg[1]:<22} {counts[ptype]:>3}")

    # Enrich with descriptions
    all_pois = enrich_descriptions(all_pois)

    # Export
    import re as _re
    base = os.path.splitext(gpx_file)[0]
    gpx_title = os.path.splitext(os.path.basename(gpx_file))[0].replace("_", " ")
    gpx_title = _re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", gpx_title)
    gpx_title = _re.sub(r"^\d+\s+", "", gpx_title).strip()
    gpx_out  = f"{base}_poi.gpx"
    csv_out  = f"{base}_poi.csv"
    html_out = f"{base}_poi.html"

    export_gpx(all_pois, gpx_out, title=gpx_title)
    print(f"\n💾 GPX  → {gpx_out}")

    export_csv(all_pois, csv_out)
    print(f"💾 CSV  → {csv_out}")

    export_html(all_pois, track, html_out, title=gpx_title)
    print(f"💾 HTML → {html_out}")

    print(f"\n🎉 Fatto! Apri {html_out} nel browser per la mappa interattiva.\n")

if __name__ == "__main__":
    main()
