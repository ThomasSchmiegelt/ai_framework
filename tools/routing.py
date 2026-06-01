"""Routenplanung über OpenStreetMap.

Geocoding via Nominatim, Routenberechnung via OSRM (öffentliche Demo-Server).
Beides benötigt eine Internetverbindung. Es werden keine API-Schlüssel benötigt.
"""

import json

import httpx

# Öffentliche OSM-Dienste (kein API-Key nötig)
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OSRM_URL = "https://router.project-osrm.org/route/v1"

# Nominatim verlangt laut Nutzungsrichtlinie einen aussagekräftigen User-Agent
_HEADERS = {"User-Agent": "AI_Framework_Thomas-EngineeringChat/1.0 (local engineering assistant)"}

# OSRM-Profil-Mapping (Demo-Server unterstützt im Wesentlichen "driving")
_PROFILES = {
    "driving": "driving",
    "auto": "driving",
    "car": "driving",
    "walking": "walking",
    "foot": "walking",
    "cycling": "cycling",
    "bike": "cycling",
}


async def _geocode(client: httpx.AsyncClient, query: str) -> dict | None:
    """Wandelt einen Ortsnamen in Koordinaten um. Gibt None zurück bei Misserfolg."""
    resp = await client.get(
        _NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1},
        headers=_HEADERS,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None
    hit = data[0]
    return {
        "name": hit.get("display_name", query),
        "lat": float(hit["lat"]),
        "lon": float(hit["lon"]),
    }


def _downsample(coords: list, max_points: int = 1500) -> list:
    """Reduziert eine sehr lange Polyline auf max_points Stützpunkte."""
    if len(coords) <= max_points:
        return coords
    step = len(coords) / max_points
    out = [coords[int(i * step)] for i in range(max_points)]
    out[-1] = coords[-1]  # Endpunkt immer beibehalten
    return out


async def plan_route(origin: str, destination: str, profile: str = "driving") -> str:
    """Berechnet eine Route von origin nach destination.

    Rückgabe ist ein JSON-String. Bei Erfolg type="map" mit Route-Geometrie für
    die Leaflet-Anzeige im Frontend, sonst type="error" oder reiner Fehlertext.
    """
    osrm_profile = _PROFILES.get(profile.lower().strip(), "driving")

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            start = await _geocode(client, origin)
            if start is None:
                return f"Startort '{origin}' konnte nicht gefunden werden."
            end = await _geocode(client, destination)
            if end is None:
                return f"Zielort '{destination}' konnte nicht gefunden werden."

            coord_str = f"{start['lon']},{start['lat']};{end['lon']},{end['lat']}"
            route_resp = await client.get(
                f"{_OSRM_URL}/{osrm_profile}/{coord_str}",
                params={"overview": "full", "geometries": "geojson"},
                headers=_HEADERS,
            )
            route_resp.raise_for_status()
            route_data = route_resp.json()
    except httpx.HTTPError as exc:
        return f"Routenberechnung fehlgeschlagen (Netzwerkfehler): {exc}"

    if route_data.get("code") != "Ok" or not route_data.get("routes"):
        return (
            f"Keine Route von '{origin}' nach '{destination}' gefunden "
            f"(Profil: {osrm_profile})."
        )

    route = route_data["routes"][0]
    # GeoJSON liefert [lon, lat] – Leaflet erwartet [lat, lon]
    geo_coords = route["geometry"]["coordinates"]
    latlng = _downsample([[c[1], c[0]] for c in geo_coords])

    distance_km = round(route["distance"] / 1000.0, 1)
    duration_min = round(route["duration"] / 60.0)
    hours, mins = divmod(duration_min, 60)
    dur_text = f"{hours} h {mins} min" if hours else f"{mins} min"

    payload = {
        "type": "map",
        "profile": osrm_profile,
        "start": start,
        "end": end,
        "distance_km": distance_km,
        "duration_min": duration_min,
        "duration_text": dur_text,
        "coordinates": latlng,
    }
    return json.dumps(payload, ensure_ascii=False)
