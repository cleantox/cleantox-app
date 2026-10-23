"""
Extrae los datos de un servicio a partir de un evento de Google Calendar.

Está hecho para el formato que CleanTox ya usa en sus eventos:

  Título:       "*LAVADO REC Fernanda Pérez"   ->  marca "*", códigos [LAVADO, REC], cliente
                "/LIMPINT Juan López"          ->  marca "/", códigos [LIMPINT], cliente
  Ubicación:    dirección del cliente
  Descripción:  una línea por concepto con su precio ($), y opcionalmente
                Subtotal / IVA / Total / "Des 10% $X" / teléfono / Anticipo.

Para registrar el anticipo en el evento basta con agregar una línea como:
    Anticipo: $2,000
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Edita aquí los códigos que usan en los títulos. Los que no estén se muestran tal cual.
CODIGOS_SERVICIO = {
    # Códigos de dos palabras van primero (se buscan antes que los sueltos)
    "PRE MUDANZA": "Pre mudanza",
    "POST OBRA": "Post obra",
    "ENT TAP": "Entrega de tapete",
    "REC TAP": "Recoger tapete",
    "PREMUD": "Pre mudanza",
    "PREMUDANZA": "Pre mudanza",
    "PRE": "Pre mudanza",
    "POST": "Post obra",
    "LIMPINT": "Limpieza integral",
    "LIM": "Limpieza integral",
    "ASP": "Aspirado profundo",
    "ASPIRADO": "Aspirado profundo",
    "TEFLON": "Teflón",
    "TEFLÓN": "Teflón",
    "TEF": "Teflón",
    "SAN": "Sanitización",
    "SANI": "Sanitización",
    "SANIT": "Sanitización",
    "SANITIZACION": "Sanitización",
    "SANITIZACIÓN": "Sanitización",
    "LIMPIN": "Limpieza integral",
    "LIMP": "Limpieza integral",
    "LIMPIEZA": "Limpieza integral",
    "LAVADO": "Lavado de tapicería",
    "LAV": "Lavado de tapicería",
    "VAP": "Desinfección y limpieza a vapor",
    "VAPOR": "Desinfección y limpieza a vapor",
    "RECTAP": "Recoger tapete",
    "RECOLECCION": "Recoger tapete",
    "RECOLECCIÓN": "Recoger tapete",
    "REC": "Recoger tapete",
    "TAP": "Entrega de tapete",      # TAP solo = entrega (la venta se registra al entregar)
}

MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")
PHONE_RE = re.compile(r"(?<![\d$,.])(?:\+?52[\s-]?)?(\d{2,3}[\s.-]?\d{3,4}[\s.-]?\d{4})(?![\d,])")
ANTICIPO_RE = re.compile(r"\b(anticipo|adelanto|ant\.?)\b", re.I)
LIQUIDADO_RE = re.compile(r"\b(liquidado|pagado completo|pagado total)\b", re.I)
TOTAL_RE = re.compile(r"^\s*total\b", re.I)
SUBTOTAL_RE = re.compile(r"^\s*sub\s?total\b", re.I)
IVA_LINEA_RE = re.compile(r"^\s*iva\b", re.I)
DESCUENTO_RE = re.compile(r"\b(?:des|desc|descuento)\.?\s*(\d+(?:\.\d+)?)\s*%", re.I)
POR_COTIZAR_RE = re.compile(r"a cotizar|por cotizar", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
ORIGEN_RE = re.compile(r"^(instagram|ig|facebook|fb|tiktok|google|whatsapp|recomendaci[oó]n|referido|p[aá]gina web|web)$", re.I)
MAS_IVA_RE = re.compile(r"(m[aá]s|\+)\s*iva", re.I)
RESTAN_RE = re.compile(r"^\s*(restan|resta|saldo|pendiente|por pagar)\b", re.I)
TASA_IVA = 0.16


@dataclass
class Concepto:
    texto: str
    monto_unitario: Optional[float] = None
    cantidad: int = 1

    @property
    def importe(self) -> Optional[float]:
        if self.monto_unitario is None:
            return None
        return self.monto_unitario * self.cantidad


@dataclass
class Servicio:
    event_id: str
    calendario: str
    inicio: Optional[datetime]
    fin: Optional[datetime]
    titulo_original: str
    marca: str = ""
    codigos: list[str] = field(default_factory=list)
    cliente: str = ""
    direccion: str = ""
    telefono: str = ""
    email: str = ""
    origen: str = ""                 # Instagram, Facebook, recomendación…
    conceptos: list[Concepto] = field(default_factory=list)
    subtotal: Optional[float] = None
    iva: Optional[float] = None
    descuento_pct: Optional[float] = None
    total: Optional[float] = None
    total_fuente: str = ""          # de dónde salió el total
    restan_en_evento: Optional[float] = None
    anticipo: float = 0.0
    anticipo_marcado: bool = False   # ✅ en el título
    anticipo_metodo: str = ""        # Efectivo / Transferencia / Terminal / "" (no dice)
    base: Optional[float] = None     # precio del servicio SIN IVA (lo que se usa para cobrar)
    base_fuente: str = ""
    paga_efectivo: bool = False      # la descripción dice "paga en efectivo"
    no_fotos: bool = False           # la descripción menciona FOTOS -> no autoriza
    metros: str = ""
    banos: str = ""
    descripcion: str = ""
    cobro_en_entrega: float = 0.0    # tapete recogido hoy: se vende y se cobra el día de la entrega
    liquidado_en_calendario: bool = False
    alertas: list[str] = field(default_factory=list)
    link_calendar: str = ""

    @property
    def tipo_servicio(self) -> str:
        if not self.codigos:
            return "Sin código"
        nombres, i, cs = [], 0, self.codigos
        while i < len(cs):
            par = " ".join(cs[i:i + 2])
            if i + 1 < len(cs) and par in CODIGOS_SERVICIO:
                nombres.append(CODIGOS_SERVICIO[par]); i += 2
            else:
                nombres.append(CODIGOS_SERVICIO.get(cs[i], cs[i])); i += 1
        sin_repetir = list(dict.fromkeys(nombres))
        return " · ".join(sin_repetir)

    def saldo_pendiente(self, pagado_en_app: float = 0.0) -> Optional[float]:
        if self.total is None:
            return None
        if self.liquidado_en_calendario:
            return 0.0
        return round(max(self.total - self.anticipo - pagado_en_app, 0.0), 2)


# ---------------------------------------------------------------- utilidades

def metodo_de(texto: str) -> str:
    t = texto.lower()
    if re.search(r"\befe(ctivo)?\b|cash", t):
        return "Efectivo"
    if re.search(r"terminal|clip|tarjeta", t):
        return "Terminal"
    if re.search(r"transf|spei|dep[oó]sito|banbaj|bankaol|bancaria", t):
        return "Transferencia"
    return ""


def _a_numero(texto: str) -> float:
    return float(texto.replace(",", ""))


def _montos(linea: str) -> list[float]:
    return [_a_numero(m) for m in MONEY_RE.findall(linea)]


def _limpiar(linea: str) -> str:
    # Quita el formato tipo WhatsApp (*negritas*, _cursivas_) y etiquetas HTML simples
    linea = re.sub(r"<[^>]+>", " ", linea)
    return re.sub(r"[*_]", "", linea).strip()


def _parse_fecha(valor: dict) -> Optional[datetime]:
    if not valor:
        return None
    texto = valor.get("dateTime") or valor.get("date")
    if not texto:
        return None
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


# ---------------------------------------------------------------- título

def parsear_titulo(titulo: str) -> tuple[str, list[str], str]:
    titulo = (titulo or "").strip()
    m = re.match(r"^([^\wÁÉÍÓÚÑáéíóúñ]*)(.*)$", titulo)
    marca, resto = (m.group(1).strip(), m.group(2).strip()) if m else ("", titulo)

    tokens = [re.sub(r"^[^\wÁÉÍÓÚÑ]+|[^\wÁÉÍÓÚÑ]+$", "", t) or t for t in resto.split()]
    codigos: list[str] = []
    i = 0
    while i < len(tokens) and tokens[i].isupper() and len(tokens[i]) > 1:
        codigos.append(tokens[i])
        i += 1
    nombre = " ".join(tokens[i:])
    nombre = re.sub(r"\s*\d{1,2}(:\d{2})?\s*(a\.?\s?m\.?|p\.?\s?m\.?|hrs?)\.?", " ", nombre, flags=re.I)
    nombre = re.sub(r"[^\w\s.'\-áéíóúñÁÉÍÓÚÑüÜ]", "", nombre)
    nombre = " ".join(nombre.split()).title() if nombre.islower() else " ".join(nombre.split())
    # Palabras en MAYÚSCULAS al final del nombre son notas (p.ej. "PUNTUAL")
    partes = nombre.split()
    while len(partes) > 1 and partes[-1].isupper() and len(partes[-1]) >= 3:
        partes.pop()
    nombre = " ".join(partes)

    # Si el nombre también venía en mayúsculas, separa por códigos conocidos
    if not nombre and codigos:
        conocidos = [c for c in codigos if c in CODIGOS_SERVICIO]
        desconocidos = [c for c in codigos if c not in CODIGOS_SERVICIO]
        codigos, nombre = conocidos, " ".join(desconocidos).title()
    return marca, codigos, nombre


# ---------------------------------------------------------------- descripción

def parsear_evento(evento: dict, calendario: str = "") -> Servicio:
    marca, codigos, cliente = parsear_titulo(evento.get("summary", ""))
    s = Servicio(
        event_id=evento.get("id", ""),
        calendario=calendario,
        inicio=_parse_fecha(evento.get("start")),
        fin=_parse_fecha(evento.get("end")),
        titulo_original=evento.get("summary", ""),
        marca=marca,
        codigos=codigos,
        cliente=cliente or "(sin nombre en el título)",
        direccion=(evento.get("location") or "").strip(),
        link_calendar=evento.get("htmlLink", ""),
        anticipo_marcado="✅" in (evento.get("summary") or ""),
    )

    descripcion = (evento.get("description") or "").replace("<br>", "\n")
    s.descripcion = descripcion
    if re.search(r"fotos?", descripcion, re.I):
        s.no_fotos = True
    if re.search(r"paga(r[aá])?\s+(en\s+)?efectivo", descripcion, re.I):
        s.paga_efectivo = True
    m2 = re.search(r"(\d[\d.,\-– ]*)\s*(m2|m²|mts?|metros)\b", descripcion, re.I)
    if m2:
        s.metros = m2.group(1).strip(" -–")
    bn = re.search(r"(\d+(?:[.,]5)?)\s*baños", descripcion, re.I)
    if bn:
        s.banos = bn.group(1)
    total_descuento = total_linea = None
    hay_por_cotizar = menciona_iva = False
    total_mas_iva = conceptos_mas_iva = False

    for bruta in descripcion.splitlines():
        linea = _limpiar(bruta)
        if not linea:
            continue
        montos = _montos(linea)

        if re.search(r"\biva\b", linea, re.I):
            menciona_iva = True
        if POR_COTIZAR_RE.search(linea):
            hay_por_cotizar = True
        if LIQUIDADO_RE.search(linea):
            s.liquidado_en_calendario = True
            continue

        # Datos de contacto: nunca se muestran como tareas
        correo = EMAIL_RE.search(linea)
        if correo:
            s.email = s.email or correo.group(0)
            continue
        if ORIGEN_RE.match(linea):
            s.origen = linea
            continue

        # Dirección escrita en la descripción (si no hay ubicación)
        if re.match(r"^\s*(direcci[oó]n|domicilio)\s*:", linea, re.I):
            if not s.direccion:
                s.direccion = linea.split(":", 1)[1].strip()
            continue

        if RESTAN_RE.search(linea) and montos:
            s.restan_en_evento = montos[0]
            continue

        if ANTICIPO_RE.search(linea) and montos:
            s.anticipo += montos[0]
            s.anticipo_metodo = metodo_de(linea) or s.anticipo_metodo
            continue

        desc = DESCUENTO_RE.search(linea)
        if desc:
            s.descuento_pct = float(desc.group(1))
            if montos:
                total_descuento = montos[-1]
            if TOTAL_RE.search(linea) and len(montos) >= 2:
                total_linea = montos[0]
            continue
        if SUBTOTAL_RE.search(linea):
            s.subtotal = montos[0] if montos else None
            continue
        if IVA_LINEA_RE.search(linea) and montos:
            s.iva = montos[0]
            continue
        if TOTAL_RE.search(linea):
            total_linea = montos[0] if montos else None
            total_mas_iva = bool(MAS_IVA_RE.search(linea))
            continue

        # Teléfono: línea que es básicamente un número
        tel = PHONE_RE.search(linea)
        if tel and not montos:
            digitos = re.sub(r"\D", "", tel.group(1))
            if len(digitos) == 10:
                s.telefono = digitos
                resto = linea.replace(tel.group(0), "").strip(" :-+\u202a\u202c")
                if len(resto) <= 12:  # p.ej. "B", "Tel", "Cel"
                    continue
                linea = resto

        # Concepto del servicio
        cantidad = 1
        if re.search(r"c/u", linea, re.I):
            paren = re.search(r"\((\d+)\)", linea)
            inicial = re.match(r"^(\d+)\s+\D", linea)
            if paren:
                cantidad = int(paren.group(1))
            elif inicial:
                cantidad = int(inicial.group(1))
        if montos and MAS_IVA_RE.search(linea):
            conceptos_mas_iva = True
        texto = MONEY_RE.split(linea)[0].strip(" :-") if montos else linea
        s.conceptos.append(Concepto(texto=texto or linea,
                                    monto_unitario=montos[0] if montos else None,
                                    cantidad=cantidad))

    suma = sum(c.importe for c in s.conceptos if c.importe is not None)
    es_recoleccion = any(c in ("REC", "RECTAP", "RECOLECCION", "RECOLECCIÓN") for c in codigos)
    tapete_rec = 0.0
    if es_recoleccion:
        tapete_rec = sum(c.importe for c in s.conceptos
                         if c.importe is not None and re.search(r"tapete|alfombra", c.texto, re.I))

    # Prioridad del total: descuento > Total > Subtotal+IVA > Subtotal > suma
    if total_descuento is not None:
        s.total, s.total_fuente = total_descuento, f"Total con descuento {s.descuento_pct:g}%"
    elif total_linea is not None and total_mas_iva:
        s.subtotal, s.iva = total_linea, round(total_linea * TASA_IVA, 2)
        s.total, s.total_fuente = round(total_linea * (1 + TASA_IVA), 2), "Total 'más IVA' + 16%"
    elif total_linea is not None:
        s.total, s.total_fuente = total_linea, "Línea 'Total'"
    elif s.subtotal is not None and s.iva is not None:
        s.total, s.total_fuente = s.subtotal + s.iva, "Subtotal + IVA"
    elif s.subtotal is not None:
        s.total, s.total_fuente = s.subtotal, "Subtotal (sin IVA)"
    elif suma > 0 and conceptos_mas_iva:
        s.subtotal, s.iva = suma, round(suma * TASA_IVA, 2)
        s.total, s.total_fuente = round(suma * (1 + TASA_IVA), 2), "Precio 'más IVA' + 16%"
    elif suma > 0:
        s.total, s.total_fuente = suma, "Suma de conceptos (estimado)"

    # ------------------------------------------------ precio SIN IVA (base para cobrar)
    if total_descuento is not None:
        if s.iva is not None or total_mas_iva or menciona_iva:
            s.base, s.base_fuente = round(total_descuento / (1 + TASA_IVA), 2), "Total con descuento ÷ 1.16"
        else:
            s.base, s.base_fuente = total_descuento, "Total con descuento"
    elif total_linea is not None and total_mas_iva:
        s.base, s.base_fuente = total_linea, "Total 'más IVA'"
    elif s.subtotal is not None:
        s.base, s.base_fuente = s.subtotal, "Subtotal"
    elif total_linea is not None:
        s.base, s.base_fuente = total_linea, "Total (sin aclarar IVA)"
        s.alertas.append("El Total no dice si incluye IVA: se tomó como precio SIN IVA. Confírmalo.")
    elif suma > 0:
        s.base, s.base_fuente = round(suma, 2), "Suma de conceptos"

    # Tapete recogido: no se vende hoy, se vende y cobra en la entrega
    if tapete_rec and s.base is not None:
        s.cobro_en_entrega = round(tapete_rec, 2)
        s.base = round(max(s.base - tapete_rec, 0.0), 2)
        s.base_fuente += " − tapete (se cobra en la entrega)"

    # ------------------------------------------------ alertas de control
    if s.total is None:
        s.alertas.append("No encontré montos: no se puede calcular el saldo.")
    if s.restan_en_evento is not None and s.total is not None:
        calc = round(s.total - s.anticipo, 2)
        if abs(calc - s.restan_en_evento) > 1:
            s.alertas.append(f"El evento dice 'Restan ${s.restan_en_evento:,.2f}' pero la app calcula ${calc:,.2f}: revisa.")
    if s.total_fuente == "Suma de conceptos (estimado)":
        s.alertas.append("No hay línea de Total: el monto es una suma automática, confírmalo.")
    if menciona_iva and s.iva is None and total_linea is None and total_descuento is None:
        s.alertas.append("Se menciona IVA pero no está desglosado ni hay Total: el cobro podría quedar sin IVA.")
    if s.subtotal is not None and suma and abs(suma - s.subtotal) > 1:
        s.alertas.append(f"La suma de conceptos (${suma:,.2f}) no cuadra con el Subtotal (${s.subtotal:,.2f}).")
    if s.subtotal is not None and s.iva is not None and abs(s.subtotal * 0.16 - s.iva) > 1:
        s.alertas.append("El IVA no es 16% del subtotal: revisa.")
    if hay_por_cotizar:
        s.alertas.append("Hay conceptos 'a cotizar': el total puede subir el día del servicio.")
    if s.anticipo > 0 and not s.anticipo_metodo:
        s.alertas.append("El anticipo no dice si fue efectivo o transferencia: se calculó como transferencia. Revísalo.")
    if s.anticipo == 0 and not s.liquidado_en_calendario:
        if s.anticipo_marcado:
            s.alertas.append("Tiene ✅ (anticipo dado) pero no dice el monto: agrega 'Anticipo $X' en la descripción.")
        else:
            s.alertas.append("Sin anticipo registrado en el evento (agrega una línea 'Anticipo: $X').")
    if not s.direccion:
        s.alertas.append("Sin dirección (campo Ubicación vacío).")
    if not s.telefono:
        s.alertas.append("Sin teléfono en la descripción.")
    return s


def es_servicio(evento: dict) -> bool:
    """Descarta eventos de todo el día (zonas NORTE/SUR/CENTRO) y juntas internas."""
    inicio = evento.get("start", {})
    if "dateTime" not in inicio:
        return False
    tiene_precio = bool(MONEY_RE.search(evento.get("description") or ""))
    return bool(evento.get("location")) or tiene_precio
