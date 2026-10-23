"""
Todo lo que habla con Google: Calendar (leer), Sheets (hoja PAGOS APP), Gmail (avisos)
y Drive (solo las carpetas/archivos que la propia app crea).

La app actúa con la cuenta contacto@cleantox.mx usando el permiso generado con
`generar_secretos.py`.
"""

from __future__ import annotations

import base64
import io
import os
import uuid
from datetime import date, datetime, time, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

TZ = ZoneInfo("America/Mexico_City")

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",   # solo leer el calendario
    "https://www.googleapis.com/auth/spreadsheets",        # hojas de cálculo
    "https://www.googleapis.com/auth/gmail.send",          # mandar avisos (no lee correos)
    "https://www.googleapis.com/auth/drive.file",          # SOLO archivos que crea la app
]

# ------------------------------------------------------------------ pestañas de la hoja
HOJA_PAGOS = "Pagos"
TABLAS = {
    "Pagos": ["ID", "Registrado", "Fecha servicio", "Event ID", "Cliente", "Monto", "Método",
              "Referencia", "Registró", "Estado", "Confirmó", "Fecha confirmación", "Notas",
              "Abono sin IVA", "IVA a declarar"],
    "Bitacora": ["ID", "Registrado", "Event ID", "Fecha servicio", "Cliente", "Evento", "Hora",
                 "Coordinadora", "Hora programada"],
    "Extras servicio": ["ID", "Registrado", "Event ID", "Fecha servicio", "Cliente", "Concepto", "Cantidad",
                        "Precio sin IVA", "Importe sin IVA", "Tipo", "Estado", "Registró", "Aprobó",
                        "Fecha aprobación"],
    "Cierres": ["ID", "Registrado", "Event ID", "Fecha servicio", "Cliente", "Teléfono", "Tipo de servicio",
                "Metros", "Baños", "Amueblado", "Cristales", "Otros", "Tareas hechas", "Tareas pendientes",
                "Operadores", "Coordinadora", "Satisfacción", "Trato", "Agendar de nuevo", "Comentarios",
                "Nombre de quien firma", "Firma (archivo)", "Total sin IVA", "Pendiente sin IVA"],
    "Recompra": ["Teléfono", "Cliente", "Contactado", "Por", "Notas"],
    "Alertas enviadas": ["Event ID", "Tipo", "Fecha"],
}
REPORTADO = "Reportado por coordinadora"
CONFIRMADO = "Confirmado"
RECHAZADO = "Rechazado"
CARPETA = "application/vnd.google-apps.folder"


def ahora() -> datetime:
    return datetime.now(TZ)


def credenciales(info_token: dict | None = None, token_path: str = "token.json") -> Credentials:
    if info_token:
        return Credentials.from_authorized_user_info(dict(info_token), SCOPES)
    if os.path.exists(token_path):
        return Credentials.from_authorized_user_file(token_path, SCOPES)
    raise RuntimeError("No hay permiso de Google. Corre primero generar_secretos.py (ver GUIA).")


def _num(v) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


class Google:
    def __init__(self, creds: Credentials):
        self.cal = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        self.gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._pestanas: dict[str, set] = {}

    # ============================================================== calendario

    def eventos(self, calendar_id: str, desde: datetime, hasta: datetime) -> list[dict]:
        eventos, token = [], None
        while True:
            r = self.cal.events().list(
                calendarId=calendar_id, timeMin=desde.isoformat(), timeMax=hasta.isoformat(),
                singleEvents=True, orderBy="startTime", pageToken=token, maxResults=2500,
            ).execute()
            eventos += r.get("items", [])
            token = r.get("nextPageToken")
            if not token:
                return [e for e in eventos if e.get("status") != "cancelled"]

    def eventos_del_dia(self, calendar_id: str, dia: date) -> list[dict]:
        inicio = datetime.combine(dia, time.min, tzinfo=TZ)
        return self.eventos(calendar_id, inicio, inicio + timedelta(days=1))

    def evento(self, calendar_id: str, event_id: str) -> dict | None:
        try:
            e = self.cal.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception:
            return None
        return None if e.get("status") == "cancelled" else e

    # ============================================================== hojas (genérico)

    def valores(self, sheet_id: str, rango: str) -> list[list]:
        r = self.sheets.spreadsheets().values().get(spreadsheetId=sheet_id, range=rango).execute()
        return r.get("values", [])

    def pestanas(self, sheet_id: str) -> set:
        if sheet_id not in self._pestanas:
            meta = self.sheets.spreadsheets().get(
                spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
            self._pestanas[sheet_id] = {s["properties"]["title"] for s in meta.get("sheets", [])}
        return self._pestanas[sheet_id]

    def asegurar_tabla(self, sheet_id: str, tabla: str) -> None:
        """Crea la pestaña si no existe y agrega columnas que falten al encabezado."""
        columnas = TABLAS[tabla]
        if tabla not in self.pestanas(sheet_id):
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
                {"addSheet": {"properties": {"title": tabla, "gridProperties": {"frozenRowCount": 1}}}}
            ]}).execute()
            self._pestanas[sheet_id].add(tabla)
            actual = []
        else:
            v = self.valores(sheet_id, f"'{tabla}'!1:1")
            actual = v[0] if v else []
        if actual[:len(columnas)] != columnas:
            nuevo = list(actual) + [c for c in columnas if c not in actual]
            self.sheets.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=f"'{tabla}'!A1", valueInputOption="RAW",
                body={"values": [nuevo]}).execute()

    def leer_tabla(self, sheet_id: str, tabla: str) -> list[dict]:
        if tabla not in self.pestanas(sheet_id):
            return []
        filas = self.valores(sheet_id, f"'{tabla}'!A1:AZ")
        if not filas:
            return []
        enc = [h.strip() for h in filas[0]]
        datos = []
        for n, fila in enumerate(filas[1:], start=2):
            if not any(str(c).strip() for c in fila):
                continue
            fila = list(fila) + [""] * (len(enc) - len(fila))
            d = dict(zip(enc, fila))
            d["_fila"] = n
            datos.append(d)
        return datos

    def agregar(self, sheet_id: str, tabla: str, datos: dict) -> str:
        self.asegurar_tabla(sheet_id, tabla)
        enc = self.valores(sheet_id, f"'{tabla}'!1:1")[0]
        datos = dict(datos)
        if "ID" in enc and not datos.get("ID"):
            datos["ID"] = uuid.uuid4().hex[:8].upper()
        if "Registrado" in enc and not datos.get("Registrado"):
            datos["Registrado"] = ahora().strftime("%Y-%m-%d %H:%M")
        fila = [datos.get(c, "") for c in enc]
        self.sheets.spreadsheets().values().append(
            spreadsheetId=sheet_id, range=f"'{tabla}'!A1", valueInputOption="RAW",
            insertDataOption="INSERT_ROWS", body={"values": [fila]}).execute()
        return datos.get("ID", "")

    def actualizar(self, sheet_id: str, tabla: str, fila: int, cambios: dict) -> None:
        enc = self.valores(sheet_id, f"'{tabla}'!1:1")[0]
        datos = []
        for col, val in cambios.items():
            j = enc.index(col)
            letra = _letra(j)
            datos.append({"range": f"'{tabla}'!{letra}{fila}", "values": [[val]]})
        self.sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id, body={"valueInputOption": "RAW", "data": datos}).execute()

    def escribir_pestana(self, sheet_id: str, tabla: str, filas: list[list]) -> None:
        """Reemplaza todo el contenido de una pestaña (para el reporte)."""
        if tabla not in self.pestanas(sheet_id):
            self.sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
                {"addSheet": {"properties": {"title": tabla}}}]}).execute()
            self._pestanas[sheet_id].add(tabla)
        self.sheets.spreadsheets().values().clear(spreadsheetId=sheet_id, range=f"'{tabla}'").execute()
        self.sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=f"'{tabla}'!A1", valueInputOption="USER_ENTERED",
            body={"values": filas}).execute()

    # -------------------------------------------------------------- pagos (atajos)

    def leer_pagos(self, sheet_id: str) -> list[dict]:
        pagos = self.leer_tabla(sheet_id, HOJA_PAGOS)
        for p in pagos:
            p["Monto"] = _num(p.get("Monto"))
        return pagos

    def agregar_pago(self, sheet_id: str, *, fecha_servicio: str, event_id: str, cliente: str,
                     monto: float, metodo: str, referencia: str, registro: str, estado: str,
                     notas: str = "") -> str:
        from calculos import abono, iva_a_declarar
        confirmado = estado == CONFIRMADO
        return self.agregar(sheet_id, HOJA_PAGOS, {
            "Fecha servicio": fecha_servicio, "Event ID": event_id, "Cliente": cliente,
            "Monto": round(float(monto), 2), "Método": metodo, "Referencia": referencia,
            "Registró": registro, "Estado": estado,
            "Confirmó": registro if confirmado else "",
            "Fecha confirmación": ahora().strftime("%Y-%m-%d %H:%M") if confirmado else "",
            "Notas": notas, "Abono sin IVA": abono(monto, metodo), "IVA a declarar": iva_a_declarar(monto),
        })

    def cambiar_estado(self, sheet_id: str, fila: int, estado: str, quien: str) -> None:
        self.actualizar(sheet_id, HOJA_PAGOS, fila, {
            "Estado": estado, "Confirmó": quien, "Fecha confirmación": ahora().strftime("%Y-%m-%d %H:%M")})

    def crear_hoja_pagos(self, titulo: str = "PAGOS APP CleanTox") -> str:
        hoja = self.sheets.spreadsheets().create(body={
            "properties": {"title": titulo, "locale": "es_MX", "timeZone": "America/Mexico_City"},
            "sheets": [{"properties": {"title": HOJA_PAGOS, "gridProperties": {"frozenRowCount": 1}}}],
        }).execute()
        sid = hoja["spreadsheetId"]
        self.sheets.spreadsheets().values().update(
            spreadsheetId=sid, range=f"{HOJA_PAGOS}!A1", valueInputOption="RAW",
            body={"values": [TABLAS["Pagos"]]}).execute()
        return sid

    # ============================================================== Drive

    def carpeta(self, nombre: str, padre: str | None = None) -> str:
        """Busca (o crea) una carpeta de la app."""
        q = f"name = '{nombre.replace(chr(39), ' ')}' and mimeType = '{CARPETA}' and trashed = false"
        if padre:
            q += f" and '{padre}' in parents"
        r = self.drive.files().list(q=q, fields="files(id)", spaces="drive").execute().get("files", [])
        if r:
            return r[0]["id"]
        cuerpo = {"name": nombre, "mimeType": CARPETA}
        if padre:
            cuerpo["parents"] = [padre]
        return self.drive.files().create(body=cuerpo, fields="id").execute()["id"]

    def contar_archivos(self, carpeta_id: str) -> int:
        r = self.drive.files().list(q=f"'{carpeta_id}' in parents and trashed = false",
                                    fields="files(id)", pageSize=1000).execute()
        return len(r.get("files", []))

    def subir(self, carpeta_id: str, nombre: str, contenido: bytes, mime: str) -> str:
        media = MediaIoBaseUpload(io.BytesIO(contenido), mimetype=mime, resumable=False)
        f = self.drive.files().create(body={"name": nombre, "parents": [carpeta_id]},
                                      media_body=media, fields="id").execute()
        return f["id"]

    def descargar(self, file_id: str) -> bytes:
        return self.drive.files().get_media(fileId=file_id).execute()

    # ============================================================== correo

    def enviar_correo(self, para, asunto: str, cuerpo: str) -> None:
        if isinstance(para, (list, tuple)):
            para = ", ".join(para)
        msg = MIMEText(cuerpo, "plain", "utf-8")
        msg["to"] = para
        msg["subject"] = asunto
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


def _letra(j: int) -> str:
    s = ""
    j += 1
    while j:
        j, r = divmod(j - 1, 26)
        s = chr(65 + r) + s
    return s


# ------------------------------------------------------------------ utilidades de datos

def pagos_de_evento(pagos: list[dict], event_id: str) -> list[dict]:
    return [p for p in pagos if p.get("Event ID") == event_id and p.get("Estado") != RECHAZADO]


def sumar(pagos: list[dict], estado: str | None = None) -> float:
    return round(sum(p["Monto"] for p in pagos if estado is None or p.get("Estado") == estado), 2)


def de_evento(filas: list[dict], event_id: str) -> list[dict]:
    return [f for f in filas if f.get("Event ID") == event_id]


def numero(v) -> float:
    return _num(v)
