#!/usr/bin/env python3
"""
GPX POI → HTML  —  Script 2 di 2
===================================
Legge un file GPX arricchito prodotto da gpx_poi_extractor.py
e genera una mappa HTML interattiva.

USO:
    python3 gpx_poi_to_html.py <nome>_poi.gpx

OUTPUT:
    - <nome>_poi.html  → mappa interattiva nel browser

REQUISITI:
    Python 3.6+ — nessuna libreria esterna necessaria
"""

import xml.etree.ElementTree as ET
import json
import math
import sys
import os
from datetime import datetime

# ─────────────────────────────────────────────
# POI TYPES — deve coincidere con lo script 1
# ─────────────────────────────────────────────
POI_TYPES = {
    "viewpoint":  ("🏔️",  "Panoramico",        "#FF6B35"),
    "museum":     ("🏛️",  "Museo",             "#9B59B6"),
    "gallery":    ("🖼️",  "Galleria d'arte",   "#F44336"),
    "artwork":    ("🎨",  "Arte/Monumento",    "#E91E63"),
    "attraction": ("⭐",  "Attrazione",        "#2196F3"),
    "historic":   ("🏰",  "Sito storico",      "#795548"),
}

POI_NS = "https://github.com/gpx-poi-extractor"

# ─────────────────────────────────────────────
# PARSING GPX ARRICCHITO
# ─────────────────────────────────────────────
def parse_enriched_gpx(filepath):
    """
    Legge il GPX prodotto da gpx_poi_extractor.py.
    Restituisce (title, track, pois).
    - track: lista di [lat, lon]
    - pois:  lista di dict con tutti i campi
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    # Namespace handling
    gpx_ns  = root.tag.split('}')[0].strip('{') if '}' in root.tag else ''
    poi_ns  = POI_NS

    def gpx(tag):
        return f'{{{gpx_ns}}}{tag}' if gpx_ns else tag

    def poi(tag):
        return f'{{{poi_ns}}}{tag}'

    def text(el, tag, default=""):
        child = el.find(tag)
        return child.text.strip() if child is not None and child.text else default

    # Title from metadata
    title = "POI"
    meta = root.find(gpx('metadata'))
    if meta is not None:
        n = meta.find(gpx('name'))
        if n is not None and n.text:
            title = n.text.strip()

    # Track points
    track = []
    for trkpt in root.findall(f'.//{gpx("trkpt")}'):
        try:
            track.append([float(trkpt.get('lat')), float(trkpt.get('lon'))])
        except (TypeError, ValueError):
            pass

    # Waypoints (POI)
    pois = []
    for wpt in root.findall(gpx('wpt')):
        try:
            lat = float(wpt.get('lat'))
            lon = float(wpt.get('lon'))
        except (TypeError, ValueError):
            continue

        name = text(wpt, gpx('name'), "(senza nome)")
        poi_type = text(wpt, gpx('type'), "attraction")

        # Read extensions
        ext = wpt.find(gpx('extensions'))
        desc      = ""
        wikipedia = ""
        website   = ""
        osm_id    = ""
        dist_m    = 0
        emoji     = POI_TYPES.get(poi_type, POI_TYPES["attraction"])[0]
        color     = POI_TYPES.get(poi_type, POI_TYPES["attraction"])[2]
        label     = POI_TYPES.get(poi_type, POI_TYPES["attraction"])[1]
        osm_tags  = {}

        if ext is not None:
            desc      = text(ext, poi('description'))
            wikipedia = text(ext, poi('wikipedia'))
            website   = text(ext, poi('website'))
            osm_id    = text(ext, poi('osm_id'))
            emoji     = text(ext, poi('emoji')) or emoji
            color     = text(ext, poi('color')) or color
            label     = text(ext, poi('label')) or label
            try:
                dist_m = int(text(ext, poi('dist_m'), "0"))
            except ValueError:
                dist_m = 0
            # OSM tags
            osm_tags_el = ext.find(poi('osm_tags'))
            if osm_tags_el is not None:
                for tag_el in osm_tags_el.findall(poi('tag')):
                    k = tag_el.get('k', '')
                    v = tag_el.get('v', '')
                    if k:
                        osm_tags[k] = v

        pois.append({
            "lat":       lat,
            "lon":       lon,
            "name":      name,
            "type":      poi_type,
            "dist_m":    dist_m,
            "desc":      desc,
            "emoji":     emoji,
            "color":     color,
            "label":     label,
            "wiki":      wikipedia,
            "web":       website,
            "osm_id":    osm_id,
            "tags":      osm_tags,
        })

    return title, track, pois

# ─────────────────────────────────────────────
# GEOMETRIA
# ─────────────────────────────────────────────
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(min(1, a)))

def total_distance_km(track):
    return sum(haversine(track[i-1][0], track[i-1][1], track[i][0], track[i][1])
               for i in range(1, len(track))) / 1000

# ─────────────────────────────────────────────
# ESPORTAZIONE HTML
# ─────────────────────────────────────────────
def export_html(pois, track, filepath, title="POI", nav="APPLE", near=5000):
    track_dec = [[round(p[0], 6), round(p[1], 6)] for p in track[::10]]
    poi_list  = [{
        "lat":   p["lat"],
        "lon":   p["lon"],
        "name":  p["name"],
        "type":  p["type"],
        "dist":  p["dist_m"],
        "label": p["label"],
        "emoji": p["emoji"],
        "color": p["color"],
        "wiki":  p["wiki"],
        "web":   p["web"],
        "desc":  p["desc"],
    } for p in pois]
    types_dict = {k: {"emoji": v[0], "label": v[1], "color": v[2]} for k, v in POI_TYPES.items()}

    if nav.upper() == "GOOGLE":
        nav_js = (
            "window.NAV_LABEL = \"Naviga con Google Maps\";"  + "\n" +
            "window.NAV_URL   = function(p) {"
            + " return \"https://www.google.com/maps/dir/?api=1\""
            + "+\"&destination=\"+p.lat+\",\"+p.lon"
            + "+\"&travelmode=bicycling\"; };" + "\n"
        )
    else:
        nav_js = (
            "window.NAV_LABEL = \"Naviga con Apple Maps\";" + "\n" +
            "window.NAV_URL   = function(p) {"
            + " return \"https://maps.apple.com/?ll=\"+p.lat+\",\"+p.lon"
            + "+\"&q=\"+encodeURIComponent(p.name)+\"&dirflg=w\"; };" + "\n"
        )
    data_block = (
        "window.APP_TRACK = " + json.dumps(track_dec)  + ";\n" +
        "window.APP_POIS  = " + json.dumps(poi_list)   + ";\n" +
        "window.APP_TYPES = " + json.dumps(types_dict) + ";\n" +
        "window.APP_NEAR  = " + str(near)              + ";\n" +
        nav_js
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
  var TYPES     = window.APP_TYPES;
  var NAV_URL   = window.NAV_URL;
  var NAV_LABEL = window.NAV_LABEL;
  var mapEl       = document.getElementById('map');
  var NEAR_M      = window.APP_NEAR;
  var nearActive  = false;
  var nearFilter  = null;
  var lastRenderLat = null;
  var lastRenderLon = null;

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
      if (!active[POIS[i].type]) continue;
      if (nearFilter !== null) {
        var d = geoDistM(POIS[i].lat, POIS[i].lon, nearFilter.lat, nearFilter.lon);
        if (d > NEAR_M) continue;
      }
      visible.push(POIS[i]);
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

        var nav  = document.createElement("a");
        nav.className = "inav";
        nav.href   = NAV_URL(p);
        nav.target = "_blank";
        nav.title  = NAV_LABEL;
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

        m.on("click", function() {
          selectPOI(row, m, p);
        });

        row.addEventListener("click", function() {
          selectPOI(row, m, p);
          if (!mapEl.classList.contains("collapsed")) {
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

  // Near — geolocation + proximity filter
  var locationMarker  = null;
  var locationCircle  = null;
  var locationWatcher = null;

  function geoDistM(lat1, lon1, lat2, lon2) {
    var R = 6371000;
    var dLat = (lat2 - lat1) * Math.PI / 180;
    var dLon = (lon2 - lon1) * Math.PI / 180;
    var a = Math.sin(dLat/2)*Math.sin(dLat/2)
          + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)
          * Math.sin(dLon/2)*Math.sin(dLon/2);
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  }

  function updateLocationMarker(latlng, accuracy) {
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

  function applyNearFilter(lat, lon, panMap) {
    nearFilter = {lat: lat, lon: lon};
    lastRenderLat = lat;
    lastRenderLon = lon;
    render();
    if (panMap && !mapEl.classList.contains("collapsed")) {
      // Fit map to visible markers
      var bounds = [];
      markers.forEach(function(m) { bounds.push(m.getLatLng()); });
      if (bounds.length > 0) {
        map.fitBounds(L.latLngBounds(bounds), {padding: [30, 30]});
      } else {
        map.setView([lat, lon], map.getZoom());
      }
    }
  }

  var locBtn = document.getElementById("loc-btn");
  locBtn.addEventListener("click", function() {
    if (!navigator.geolocation) {
      alert("Geolocalizzazione non supportata da questo browser.");
      return;
    }
    if (nearActive) {
      // Deactivate
      if (locationWatcher !== null) { navigator.geolocation.clearWatch(locationWatcher); locationWatcher = null; }
      if (locationMarker)  { locationMarker.remove();  locationMarker  = null; }
      if (locationCircle)  { locationCircle.remove();  locationCircle  = null; }
      nearActive    = false;
      nearFilter    = null;
      lastRenderLat = null;
      lastRenderLon = null;
      locBtn.classList.remove("active");
      locBtn.title = "Filtra POI vicini";
      render();
    } else {
      // Activate — show full list until first fix
      nearActive = true;
      locBtn.classList.add("active");
      locBtn.title = "Disattiva filtro vicini";
      locationWatcher = navigator.geolocation.watchPosition(
        function(pos) {
          var lat = pos.coords.latitude;
          var lon = pos.coords.longitude;
          updateLocationMarker(L.latLng(lat, lon), pos.coords.accuracy);
          var isFirstFix = (lastRenderLat === null);
          if (isFirstFix) {
            // First fix: always apply filter + pan map
            applyNearFilter(lat, lon, true);
          } else {
            // Subsequent fixes: re-render only if moved > NEAR_M/10
            var moved = geoDistM(lat, lon, lastRenderLat, lastRenderLon);
            if (moved > NEAR_M / 10) {
              applyNearFilter(lat, lon, false);
            } else {
              // Just update marker position, no re-render
              updateLocationMarker(L.latLng(lat, lon), pos.coords.accuracy);
            }
          }
        },
        function(err) {
          nearActive = false;
          locBtn.classList.remove("active");
          var errMsg = err.message || "";
          var errCodes = {1:"Permesso negato (abilita la localizzazione in Impostazioni \\u2192 Privacy \\u2192 Siti web di Safari)",
                          2:"Posizione non disponibile", 3:"Timeout"};
          alert("Errore geolocalizzazione: " + (errCodes[err.code] || errMsg || "codice " + err.code));
        },
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 10000 }
      );
    }
  });

  // Map toggle (mobile only)
  var toggleBtn = document.getElementById('map-toggle');
  var mapVisible = true;
  toggleBtn.addEventListener('click', function() {
    mapVisible = !mapVisible;
    if (mapVisible) {
      mapEl.classList.remove('collapsed');
      toggleBtn.setAttribute('title', 'Nascondi mappa');
      toggleBtn.innerHTML = '&#x25B2;Mappa';
      setTimeout(function() {
        map.invalidateSize();
        if (selectedRow) selectedRow.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 310);
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
    lines.append('      <h1>' + title + '</h1>')
    lines.append('      <p>Generato il ' + now_str + ' &middot; entro 1 km</p>')
    lines.append('    </div>')
    lines.append('    <div style="display:flex;gap:6px;align-items:center;flex-shrink:0">')
    lines.append('      <button id="loc-btn" class="hdr-btn" title="Filtra POI vicini">&#x2299; Near</button>')
    lines.append('      <button id="map-toggle" class="hdr-btn" title="Nascondi mappa">&#x25B2;Mappa</button>')
    lines.append('    </div>')
    lines.append('  </div>')
    lines.append('  <div class="stats">')
    lines.append('    <div class="stat"><div class="v">' + str(poi_count) + '</div><div class="l">POI</div></div>')
    lines.append('    <div class="stat"><div class="v" id="vis-count">' + str(poi_count) + '</div><div class="l">Visibili</div></div>')
    lines.append('    <div class="stat"><div class="v">' + str(total_km)  + '</div><div class="l">Km</div></div>')
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
    import argparse

    parser = argparse.ArgumentParser(description="Genera mappa HTML da un GPX POI arricchito.")
    parser.add_argument("gpx_file", help="File GPX prodotto da gpx_poi_extractor.py")
    parser.add_argument("--dist", type=int, default=None,
                        metavar="METRI",
                        help="Mostra solo i POI entro questa distanza dalla traccia (filtra il GPX)")
    parser.add_argument("--nav", choices=["APPLE", "GOOGLE"], default="APPLE",
                        help="Navigatore per il pulsante ➡️ (default: APPLE)")
    parser.add_argument("--near", type=int, default=5000,
                        metavar="METRI",
                        help="Raggio in metri per il pulsante Near (default: 5000)")
    args = parser.parse_args()

    gpx_file = args.gpx_file
    if not os.path.exists(gpx_file):
        print(f"❌ File non trovato: {gpx_file}")
        sys.exit(1)

    print(f"\n🗺️  GPX POI → HTML — {gpx_file}")
    print("=" * 50)

    print("📂 Parsing GPX arricchito...")
    title, track, pois = parse_enriched_gpx(gpx_file)

    if not track:
        print("❌ Nessun trackpoint trovato nel file GPX.")
        sys.exit(1)

    # Optional distance filter
    if args.dist is not None:
        before = len(pois)
        pois = [p for p in pois if p["dist_m"] <= args.dist]
        print(f"   Filtro distanza: {args.dist} m → {len(pois)}/{before} POI mantenuti")

    # Sort POIs by progressive position along track
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

    pois.sort(key=track_progress)

    dist_km = round(total_distance_km(track))
    print(f"   Titolo:       {title}")
    print(f"   Tracciato:    {len(track)} punti · {dist_km} km")
    print(f"   POI:          {len(pois)}")
    print(f"   Navigatore:   {args.nav}")
    print(f"   Raggio Near:  {args.near} m")

    base     = os.path.splitext(gpx_file)[0]
    html_out = f"{base}.html"

    export_html(pois, track, html_out, title=title, nav=args.nav, near=args.near)
    print(f"\n💾 HTML → {html_out}")
    print(f"\n🎉 Fatto! Apri {html_out} nel browser.\n")

if __name__ == "__main__":
    main()
