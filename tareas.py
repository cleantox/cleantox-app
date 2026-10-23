"""
Tareas automáticas (las corre GitHub Actions, sin que nadie abra la app):

    python tareas.py retrasos   -> cada 15 min: avisa si una coordinadora no ha marcado llegada
    python tareas.py reporte    -> lunes 8:00: llena la pestaña "Reporte semanal"

Lee la configuración del secreto STREAMLIT_SECRETS (el mismo texto que está en Streamlit).
"""

from __future__ import annotations

import os
import sys
import tomllib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import google_api as g
import negocio

TZ = ZoneInfo("America/Mexico_City")


def config() -> dict:
    texto = os.environ.get("STREAMLIT_SECRETS")
    if texto:
        return tomllib.loads(texto)
    with open(".streamlit/secrets.toml", "rb") as f:
        return tomllib.load(f)


def main(modo: str) -> None:
    c = config()
    gg = g.Google(g.credenciales(c.get("google_token")))
    sheet, cal = c["SHEET_ID"], c["CALENDAR_ID"]
    ahora = datetime.now(TZ)

    if modo == "retrasos":
        hoy = ahora.date()
        servicios = negocio.servicios_entre(gg, cal, hoy, hoy + timedelta(days=1))
        bitacora = gg.leer_tabla(sheet, "Bitacora")
        enviadas = {(a.get("Event ID"), a.get("Tipo")) for a in gg.leer_tabla(sheet, "Alertas enviadas")}
        para = list(c.get("ALERTAS_RETRASO", ["operaciones@cleantox.mx", "rrhh@cleantox.mx"]))
        for s in negocio.retrasados(servicios, bitacora, ahora):
            if (s.event_id, "retraso") in enviadas:
                continue
            gg.enviar_correo(para, f"⏰ Sin llegada registrada – {s.cliente}",
                             f"El servicio de {s.cliente} ({s.tipo_servicio}) estaba programado a las "
                             f"{s.inicio.astimezone(TZ):%H:%M} y la coordinadora no ha marcado su llegada.\n\n"
                             f"Dirección: {s.direccion}\nApp: {c.get('APP_URL', '')}\n")
            gg.agregar(sheet, "Alertas enviadas", {"Event ID": s.event_id, "Tipo": "retraso",
                                                   "Fecha": ahora.strftime("%Y-%m-%d %H:%M")})
            print("Alerta de retraso enviada")

    elif modo == "reporte":
        lunes = ahora.date() - timedelta(days=ahora.weekday() + 7)
        servicios = negocio.servicios_entre(gg, cal, lunes, lunes + timedelta(days=7))
        hist = []
        if c.get("SERVICIOS_SHEET_ID"):
            hist += negocio.historial_servicios_sheet(gg, c["SERVICIOS_SHEET_ID"])
        hist += negocio.historial_calendario(gg, cal, ahora.date() - timedelta(days=760), ahora.date() + timedelta(days=1))
        futuros = {x["tel"] for x in negocio.historial_calendario(gg, cal, ahora.date() + timedelta(days=1),
                                                                  ahora.date() + timedelta(days=370))}
        recon = negocio.clientes_a_recontactar(hist, futuros, gg.leer_tabla(sheet, "Cierres"),
                                               gg.leer_tabla(sheet, "Recompra"), ahora.date())
        filas = negocio.reporte_semanal(servicios, gg.leer_pagos(sheet), gg.leer_tabla(sheet, "Extras servicio"),
                                        gg.leer_tabla(sheet, "Bitacora"), gg.leer_tabla(sheet, "Cierres"),
                                        recon, lunes)
        gg.escribir_pestana(sheet, "Reporte semanal", filas)
        print("Reporte semanal actualizado")
    else:
        raise SystemExit("Uso: python tareas.py retrasos|reporte")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
