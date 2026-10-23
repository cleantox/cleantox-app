"""
Lógica de negocio sin pantalla (la usan la app y las tareas programadas):
historial de clientes, recompra, retrasos y reporte semanal.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import calculos
import google_api as g
from parser_servicios import es_servicio, parsear_evento

TZ = ZoneInfo("America/Mexico_City")
CARAS = ["😊", "😐", "☹️"]
INTERVALOS = {"3 meses": 3, "6 meses": 6, "1 año": 12, "No gracias": None}
MESES_DEFAULT = 6


def tel10(t: str) -> str:
    d = re.sub(r"\D", "", str(t or ""))
    return d[-10:] if len(d) >= 10 else ""


def sumar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    y, m = d.year + m // 12, m % 12 + 1
    dias = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return date(y, m, min(d.day, dias[m - 1]))


def _fecha(txt: str) -> date | None:
    txt = str(txt or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            pass
    return None


# ------------------------------------------------------------------ historial

def historial_servicios_sheet(gg: g.Google, sheet_id: str) -> list[dict]:
    """Lee SERVICIOS2026: todas las pestañas, buscando las columnas Fecha / Nombre / Concepto / Contacto."""
    hist = []
    for pestana in sorted(gg.pestanas(sheet_id)):
        try:
            filas = gg.valores(sheet_id, f"'{pestana}'!A1:AF")
        except Exception:
            continue
        enc_i = None
        for i, f in enumerate(filas[:10]):
            norm = [str(c).strip().lower() for c in f]
            if "fecha" in norm and "nombre" in norm and "contacto" in norm:
                enc_i = i
                break
        if enc_i is None:
            continue
        norm = [str(c).strip().lower() for c in filas[enc_i]]
        i_f, i_n, i_t = norm.index("fecha"), norm.index("nombre"), norm.index("contacto")
        i_c = norm.index("concepto") if "concepto" in norm else None
        for f in filas[enc_i + 1:]:
            f = list(f) + [""] * (len(norm) - len(f))
            t, d = tel10(f[i_t]), _fecha(f[i_f])
            if t and d:
                hist.append({"tel": t, "cliente": str(f[i_n]).strip(), "fecha": d,
                             "tipo": str(f[i_c]).strip() if i_c is not None else "", "fuente": pestana})
    return hist


def historial_calendario(gg: g.Google, cal_id: str, desde: date, hasta: date) -> list[dict]:
    a = datetime.combine(desde, datetime.min.time(), tzinfo=TZ)
    b = datetime.combine(hasta, datetime.min.time(), tzinfo=TZ)
    hist = []
    for ev in gg.eventos(cal_id, a, b):
        if not es_servicio(ev):
            continue
        s = parsear_evento(ev)
        t = tel10(s.telefono)
        if t and s.inicio:
            hist.append({"tel": t, "cliente": s.cliente, "fecha": s.inicio.astimezone(TZ).date(),
                         "tipo": s.tipo_servicio, "fuente": "calendario"})
    return hist


def clientes_a_recontactar(historial: list[dict], futuros: set, cierres: list[dict],
                           contactados: list[dict], hoy: date) -> list[dict]:
    ultimo: dict[str, dict] = {}
    for h in historial:
        u = ultimo.get(h["tel"])
        if not u or h["fecha"] > u["fecha"]:
            ultimo[h["tel"]] = h
    # lo que el cliente pidió en su último cierre
    pidio: dict[str, tuple] = {}
    for c in cierres:
        t, d = tel10(c.get("Teléfono")), _fecha(c.get("Fecha servicio"))
        if t and d and (t not in pidio or d > pidio[t][0]):
            pidio[t] = (d, c.get("Agendar de nuevo", ""))
    recientes = {}
    for c in contactados:
        d = _fecha(str(c.get("Contactado", ""))[:10])
        t = tel10(c.get("Teléfono"))
        if t and d:
            recientes[t] = max(d, recientes.get(t, d))

    lista = []
    for t, h in ultimo.items():
        if t in futuros:
            continue
        meses = MESES_DEFAULT
        if t in pidio and pidio[t][1] in INTERVALOS:
            meses = INTERVALOS[pidio[t][1]]
            if meses is None:
                continue
        vence = sumar_meses(h["fecha"], meses)
        if vence > hoy:
            continue
        if t in recientes and (hoy - recientes[t]).days < 30:
            continue
        lista.append({"tel": t, "cliente": h["cliente"], "ultimo": h["fecha"], "tipo": h["tipo"],
                      "meses": meses, "vence": vence})
    return sorted(lista, key=lambda x: x["vence"])


# ------------------------------------------------------------------ bitácora / retrasos

def horas_bitacora(bitacora: list[dict], event_id: str) -> dict:
    out = {"llegada": None, "salida": None, "coordinadora": ""}
    for b in bitacora:
        if b.get("Event ID") != event_id:
            continue
        try:
            h = datetime.fromisoformat(b.get("Hora", ""))
        except ValueError:
            continue
        clave = "llegada" if b.get("Evento") == "Llegada" else "salida"
        if out[clave] is None:
            out[clave] = h
            out["coordinadora"] = out["coordinadora"] or b.get("Coordinadora", "")
    return out


def retrasados(servicios: list, bitacora: list[dict], ahora: datetime, minutos: int = 15) -> list:
    res = []
    for s in servicios:
        if not s.inicio:
            continue
        ini = s.inicio.astimezone(TZ)
        fin = (s.fin or s.inicio).astimezone(TZ)
        if ini + timedelta(minutes=minutos) <= ahora <= fin and not horas_bitacora(bitacora, s.event_id)["llegada"]:
            res.append(s)
    return res


# ------------------------------------------------------------------ reporte semanal

def reporte_semanal(servicios: list, pagos: list[dict], extras: list[dict], bitacora: list[dict],
                    cierres: list[dict], recontactar: list[dict], inicio: date) -> list[list]:
    fin = inicio + timedelta(days=6)
    filas = [[f"REPORTE SEMANAL CleanTox · {inicio:%d/%m/%Y} al {fin:%d/%m/%Y}"],
             [f"Generado: {datetime.now(TZ):%d/%m/%Y %H:%M}"], []]

    tipos: dict[str, dict] = {}
    venta = pendiente = 0.0
    a_tiempo = con_llegada = 0
    for s in servicios:
        ec = calculos.estado_cuenta(s, g.pagos_de_evento(pagos, s.event_id), g.de_evento(extras, s.event_id))
        b = ec["base"] or 0
        venta += b
        pendiente += ec["pendiente"] or 0
        tipo = s.tipo_servicio.split(" · ")[0]
        t = tipos.setdefault(tipo, {"n": 0, "venta": 0.0, "horas_ag": [], "horas_real": []})
        t["n"] += 1
        t["venta"] += b
        hb = horas_bitacora(bitacora, s.event_id)
        if s.inicio and s.fin:
            t["horas_ag"].append((s.fin - s.inicio).total_seconds() / 3600)
        if hb["llegada"]:
            con_llegada += 1
            if s.inicio and hb["llegada"] <= s.inicio.astimezone(TZ) + timedelta(minutes=15):
                a_tiempo += 1
            if hb["salida"]:
                t["horas_real"].append((hb["salida"] - hb["llegada"]).total_seconds() / 3600)

    n = len(servicios)
    filas += [["RESUMEN", "", ""],
              ["Servicios", n],
              ["Venta (sin IVA)", round(venta, 2)],
              ["Ticket promedio (sin IVA)", round(venta / n, 2) if n else 0],
              ["Por cobrar de estos servicios (sin IVA)", round(pendiente, 2)],
              ["Efectivo reportado sin confirmar (todas las fechas)",
               g.sumar([p for p in pagos if p.get("Estado") == g.REPORTADO])],
              ["Puntualidad (llegó ≤15 min tarde)", f"{a_tiempo}/{con_llegada}" if con_llegada else "sin registros"],
              []]

    filas.append(["POR TIPO DE SERVICIO", "Servicios", "Venta sin IVA", "Ticket promedio",
                  "Horas agendadas (prom.)", "Horas reales (prom.)", "Venta por hora real"])
    for tipo, t in sorted(tipos.items(), key=lambda x: -x[1]["venta"]):
        hag = sum(t["horas_ag"]) / len(t["horas_ag"]) if t["horas_ag"] else ""
        hre = sum(t["horas_real"]) / len(t["horas_real"]) if t["horas_real"] else ""
        vph = round((t["venta"] / t["n"]) / hre, 2) if hre else ""
        filas.append([tipo, t["n"], round(t["venta"], 2), round(t["venta"] / t["n"], 2),
                      round(hag, 1) if hag != "" else "", round(hre, 1) if hre != "" else "", vph])
    filas.append([])

    ids = {s.event_id for s in servicios}
    cs = [c for c in cierres if c.get("Event ID") in ids]
    filas.append(["CALIFICACIONES", "😊", "😐", "☹️"])
    filas.append(["Satisfacción"] + [sum(1 for c in cs if c.get("Satisfacción") == x) for x in CARAS])
    filas.append(["Trato del operador"] + [sum(1 for c in cs if c.get("Trato") == x) for x in CARAS])
    malos = [c for c in cs if c.get("Satisfacción") in CARAS[1:] or c.get("Trato") in CARAS[1:]]
    for c in malos:
        filas.append(["  ⚠️ " + c.get("Cliente", ""), c.get("Satisfacción", ""), c.get("Trato", ""),
                      c.get("Comentarios", "")])
    filas.append([])

    filas.append([f"CLIENTES A RECONTACTAR ({len(recontactar)})", "Teléfono", "Último servicio", "Tipo"])
    for r in recontactar[:200]:
        filas.append([r["cliente"], r["tel"], r["ultimo"].strftime("%d/%m/%Y"), r["tipo"]])
    return filas


def servicios_entre(gg: g.Google, cal_id: str, desde: date, hasta: date) -> list:
    a = datetime.combine(desde, datetime.min.time(), tzinfo=TZ)
    b = datetime.combine(hasta, datetime.min.time(), tzinfo=TZ)
    return [parsear_evento(e, "Servicios CleanTox") for e in gg.eventos(cal_id, a, b) if es_servicio(e)]
