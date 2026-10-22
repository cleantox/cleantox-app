"""
Todo lo que habla con Google: Calendar (leer), Sheets (pagos) y Gmail (avisos).

La app actúa con la cuenta contacto@cleantox.mx usando el permiso que se genera
una sola vez con `generar_secretos.py`.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import date, datetime, time, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TZ = ZoneInfo("America/Mexico_City")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",   # solo leer el calendario
    "https://www.googleapis.com/auth/spreadsheets",        # escribir en la hoja de pagos
    "https://www.googleapis.com/auth/gmail.send",          # mandar avisos (no puede leer correos)
]

HOJA_PAGOS = "Pagos"
COLUMNAS = [
    "ID", "Registrado", "Fecha servicio", "Event ID", "Cliente", "Monto", "Método",
    "Referencia", "Registró", "Estado", "Confirmó", "Fecha confirmación", "Notas",
]
REPORTADO = "Reportado por coordinadora"
CONFIRMADO = "Confirmado"
RECHAZADO = "Rechazado"


# ------------------------------------------------------------------ conexión

def credenciales(info_token: dict | None = None, token_path: str = "token.json") -> Credentials:
    if info_token:
        return Credentials.from_authorized_user_info(dict(info_token), SCOPES)
    if os.path.exists(token_path):
        return Credentials.from_authorized_user_file(token_path, SCOPES)
    raise RuntimeError("No hay permiso de Google. Corre primero generar_secretos.py (ver GUIA).")


class Google:
    def __init__(self, creds: Credentials):
        self.cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)

    # -------------------------------------------------------------- calendario

    def eventos_del_dia(self, calendar_id: str, dia: date) -> list[dict]:
        inicio = datetime.combine(dia, time.min, tzinfo=TZ)
        fin = inicio + timedelta(days=1)
        eventos, token = [], None
        while True:
            r = self.cal.events().list(
                calendarId=calendar_id, timeMin=inicio.isoformat(), timeMax=fin.isoformat(),
                singleEvents=True, orderBy="startTime", pageToken=token,
            ).execute()
            eventos += r.get("items", [])
            token = r.get("nextPageToken")
            if not token:
                return [e for e in eventos if e.get("status") != "cancelled"]

    def evento(self, calendar_id: str, event_id: str) -> dict | None:
        try:
            e = self.cal.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception:
            return None
        return None if e.get("status") == "cancelled" else e

    # -------------------------------------------------------------- hoja de pagos

    def leer_pagos(self, sheet_id: str) -> list[dict]:
        r = self.sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{HOJA_PAGOS}!A1:M"
        ).execute()
        filas = r.get("values", [])
        if not filas:
            return []
        encabezado = filas[0]
        pagos = []
        for n, fila in enumerate(filas[1:], start=2):
            fila = fila + [""] * (len(encabezado) - len(fila))
            p = dict(zip(encabezado, fila))
            try:
                p["Monto"] = float(str(p.get("Monto", "0")).replace("$", "").replace(",", "") or 0)
            except ValueError:
                p["Monto"] = 0.0
            p["_fila"] = n
            pagos.append(p)
        return pagos

    def agregar_pago(self, sheet_id: str, *, fecha_servicio: str, event_id: str, cliente: str,
                     monto: float, metodo: str, referencia: str, registro: str, estado: str,
                     notas: str = "") -> str:
        ahora = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        pid = uuid.uuid4().hex[:8].upper()
        confirmo = registro if estado == CONFIRMADO else ""
        fecha_conf = ahora if estado == CONFIRMADO else ""
        fila = [pid, ahora, fecha_servicio, event_id, cliente, round(float(monto), 2), metodo,
                referencia, registro, estado, confirmo, fecha_conf, notas]
        self.sheets.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"{HOJA_PAGOS}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [fila]},
        ).execute()
        return pid

    def cambiar_estado(self, sheet_id: str, fila: int, estado: str, quien: str) -> None:
        ahora = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        self.sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"{HOJA_PAGOS}!J{fila}:L{fila}",
            valueInputOption="RAW", body={"values": [[estado, quien, ahora]]},
        ).execute()

    def crear_hoja_pagos(self, titulo: str = "PAGOS APP CleanTox") -> str:
        hoja = self.sheets.spreadsheets().create(body={
            "properties": {"title": titulo, "locale": "es_MX", "timeZone": "America/Mexico_City"},
            "sheets": [{"properties": {"title": HOJA_PAGOS, "gridProperties": {"frozenRowCount": 1}}}],
        }).execute()
        sid = hoja["spreadsheetId"]
        self.sheets.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{HOJA_PAGOS}!A1", valueInputOption="RAW",
            body={"values": [COLUMNAS]},
        ).execute()
        return sid

    # -------------------------------------------------------------- correo

    def enviar_correo(self, para: str, asunto: str, cuerpo: str) -> None:
        msg = MIMEText(cuerpo, "plain", "utf-8")
        msg["to"] = para
        msg["subject"] = asunto
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


# ------------------------------------------------------------------ cálculos

def pagos_de_evento(pagos: list[dict], event_id: str) -> list[dict]:
    return [p for p in pagos if p.get("Event ID") == event_id and p.get("Estado") != RECHAZADO]


def sumar(pagos: list[dict], estado: str | None = None) -> float:
    return round(sum(p["Monto"] for p in pagos if estado is None or p.get("Estado") == estado), 2)
