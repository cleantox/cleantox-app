"""Links firmados para coordinadoras: un link por servicio, vence al terminar el día del servicio."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Mexico_City")


def _firma(secreto: str, event_id: str, fecha: str) -> str:
    mac = hmac.new(secreto.encode(), f"{event_id}|{fecha}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode()[:24]


def crear_link(app_url: str, secreto: str, event_id: str, fecha: date) -> str:
    f = fecha.isoformat()
    q = urlencode({"s": event_id, "d": f, "k": _firma(secreto, event_id, f)})
    return f"{app_url.rstrip('/')}/?{q}"


def verificar(secreto: str, event_id: str, fecha: str, firma: str) -> tuple[bool, str]:
    if not (event_id and fecha and firma):
        return False, "Link incompleto."
    if not hmac.compare_digest(_firma(secreto, event_id, fecha), firma):
        return False, "Este link no es válido."
    try:
        dia = date.fromisoformat(fecha)
    except ValueError:
        return False, "Este link no es válido."
    if datetime.now(TZ).date() > dia:
        return False, "Este link ya venció. Pide uno nuevo a la oficina."
    return True, ""
