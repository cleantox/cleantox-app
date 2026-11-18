"""Sugerencia de rutas para el Plan de mañana.

- Convierte direcciones en coordenadas con OpenStreetMap (gratis) y las guarda en la pestaña
  'Ubicaciones' para no volver a buscarlas.
- Reparte los servicios del turno de mañana entre las camionetas activas por zona (sectores
  alrededor de la bodega) respetando el cupo de personas.
- El turno de tarde lo toma la camioneta cuya última parada de la mañana quedó más cerca
  (es la que recoge al equipo de lavado).
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request

BODEGA_DIRECCION = "Av. Jesús del Monte 271, Jesús del Monte, 52764 Huixquilucan, Estado de México"
BODEGA_RESPALDO = (19.3845, -99.2905)      # aprox. por si no se encuentra la dirección
UA = "CleanToxApp/1.0 (contacto@cleantox.mx)"
HORA_CORTE_TARDE = 13 * 60                  # servicios que empiezan desde la 1 pm = turno tarde


# ------------------------------------------------------------------ ubicaciones

def _get(url: str, timeout: int = 12) -> tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.geturl(), r.read().decode("utf-8", "ignore")


def _coords_en_texto(texto: str) -> tuple[float, float] | None:
    for pat in (r"@(-?\d{1,2}\.\d+),(-?\d{1,3}\.\d+)", r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)",
                r"[?&](?:q|ll|query|destination)=(-?\d{1,2}\.\d+)(?:,|%2C)(-?\d{1,3}\.\d+)",
                r"^\s*(-?\d{1,2}\.\d{3,}),\s*(-?\d{1,3}\.\d{3,})\s*$"):
        m = re.search(pat, texto)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None


def _limpiar(direccion: str) -> str:
    d = re.sub(r"https?://\S+", " ", direccion)
    d = re.sub(r"\b(depto|dpto|departamento|int(erior)?|piso|casa|torre|edificio|edif|lote|mz|manzana)\b\.?\s*\S*",
               " ", d, flags=re.I)
    d = re.sub(r"\s+", " ", d).strip(" ,.")
    return d


def _nominatim(q: str = "", **params) -> tuple[float, float] | None:
    p = {"format": "json", "limit": "1", "countrycodes": "mx"}
    if q:
        p["q"] = q
    p.update(params)
    try:
        _, txt = _get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(p))
        r = json.loads(txt)
        if r:
            return float(r[0]["lat"]), float(r[0]["lon"])
    except Exception:
        pass
    return None


def geocodificar(direccion: str) -> tuple[tuple[float, float] | None, str]:
    """Devuelve ((lat, lon), fuente) o (None, motivo)."""
    if not direccion:
        return None, "sin dirección"
    c = _coords_en_texto(direccion)
    if c:
        return c, "coordenadas"
    link = re.search(r"https?://\S+", direccion)
    if link:                                   # liga de Google Maps: seguir la redirección
        try:
            final, cuerpo = _get(link.group(0))
            c = _coords_en_texto(final) or _coords_en_texto(cuerpo[:20000])
            if c:
                return c, "liga de Maps"
        except Exception:
            pass
    limpia = _limpiar(direccion)
    intentos = [limpia + ", México"]
    partes = [x.strip() for x in limpia.split(",") if x.strip()]
    if len(partes) > 2:
        intentos.append(", ".join(partes[1:]) + ", México")      # sin la calle
    for q in intentos:
        c = _nominatim(q)
        time.sleep(1.1)                         # política de uso de OpenStreetMap
        if c:
            return c, "openstreetmap"
    cp = re.search(r"\b(\d{5})\b", direccion)
    if cp:
        c = _nominatim(postalcode=cp.group(1))
        time.sleep(1.1)
        if c:
            return c, "código postal"
    return None, "no se encontró"


# ------------------------------------------------------------------ cálculo

def distancia(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Km en línea recta."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _rumbo(base, p) -> float:
    return math.atan2(p[0] - base[0], (p[1] - base[1]) * math.cos(math.radians(base[0])))


def _ordenar(paradas: list[dict], inicio) -> list[dict]:
    """Por hora de inicio; dentro de la misma hora, la más cercana a donde va la camioneta."""
    res, pos = [], inicio
    for hora in sorted({p["hora"] for p in paradas}):
        grupo = [p for p in paradas if p["hora"] == hora]
        while grupo:
            sig = min(grupo, key=lambda p: distancia(pos, p["pos"]) if p["pos"] else 999)
            grupo.remove(sig)
            res.append(sig)
            pos = sig["pos"] or pos
    return res


def sugerir(servicios: list[dict], camionetas: list[str], cupo: dict[str, int],
            base: tuple[float, float]) -> tuple[dict[str, list[str]], list[str]]:
    """servicios: [{id, pos (lat,lon)|None, hora (min), personas, nombre}]
    Devuelve ({camioneta: [ids en orden]}, avisos)."""
    avisos: list[str] = []
    rutas = {c: [] for c in camionetas}
    if not camionetas or not servicios:
        return rutas, avisos
    manana = [s for s in servicios if s["hora"] < HORA_CORTE_TARDE]
    tarde = [s for s in servicios if s["hora"] >= HORA_CORTE_TARDE]

    # --- mañana: sectores alrededor de la bodega, con cupo
    ubic = sorted([s for s in manana if s["pos"]], key=lambda s: _rumbo(base, s["pos"]))
    # el corte del círculo va en el hueco más grande entre servicios
    if len(ubic) > 2:
        angs = [_rumbo(base, s["pos"]) for s in ubic]
        huecos = [(angs[(i + 1) % len(angs)] - angs[i]) % (2 * math.pi) for i in range(len(angs))]
        k = huecos.index(max(huecos))
        ubic = ubic[k + 1:] + ubic[:k + 1]
    meta = math.ceil(len(manana) / len(camionetas)) if manana else 0
    grupos = {c: [] for c in camionetas}
    gente = {c: 0 for c in camionetas}
    j = 0
    for s in ubic:
        while j < len(camionetas) - 1 and (len(grupos[camionetas[j]]) >= meta or
                                           gente[camionetas[j]] + s["personas"] > cupo.get(camionetas[j], 9)):
            j += 1
        c = camionetas[j]
        if gente[c] + s["personas"] > cupo.get(c, 9):
            libre = max(camionetas, key=lambda x: cupo.get(x, 9) - gente[x])
            if gente[libre] + s["personas"] <= cupo.get(libre, 9):
                c = libre
            else:
                avisos.append(f"No alcanza el cupo para {s['nombre']}: quedó en {c} con más gente de la que cabe.")
        grupos[c].append(s)
        gente[c] += s["personas"]
    for s in manana:
        if not s["pos"]:
            c = min(camionetas, key=lambda x: (gente[x] + s["personas"] > cupo.get(x, 9), len(grupos[x])))
            grupos[c].append(s)
            gente[c] += s["personas"]
            avisos.append(f"No pude ubicar la dirección de {s['nombre']}: la puse en {c}, revísala.")
    ultimo = {}
    for c in camionetas:
        orden = _ordenar(grupos[c], base)
        rutas[c] = [s["id"] for s in orden]
        con_pos = [s["pos"] for s in orden if s["pos"]]
        ultimo[c] = con_pos[-1] if con_pos else base
        if gente[c]:
            avisos.append(f"{c}: {len(orden)} parada(s) en la mañana con {gente[c]} persona(s) "
                          f"(cupo {cupo.get(c, 9)}).")

    # --- tarde: la camioneta más cercana (recoge al equipo de lavado de la mañana)
    tard = {c: [] for c in camionetas}
    for s in sorted(tarde, key=lambda s: s["hora"]):
        if s["pos"]:
            c = min(camionetas, key=lambda x: (distancia(ultimo[x], s["pos"]) + 4 * len(tard[x])))
        else:
            c = min(camionetas, key=lambda x: len(tard[x]))
            avisos.append(f"No pude ubicar la dirección de {s['nombre']} (tarde): la puse en {c}, revísala.")
        tard[c].append(s)
    for c in camionetas:
        if tard[c]:
            rutas[c] += [s["id"] for s in _ordenar(tard[c], ultimo[c])]
            avisos.append(f"{c}: {len(tard[c])} parada(s) en la tarde.")
    return rutas, avisos
