"""
Cuentas de cada servicio con la regla de CleanTox:

- Todo se lleva en PRECIO SIN IVA (base).
- Pago por transferencia / terminal: abona monto ÷ 1.16 (el cliente pagó IVA encima).
- Pago en efectivo: abona el monto completo (precio especial en efectivo).
- En TODOS los casos, para el contador: IVA a declarar = monto − monto ÷ 1.16.
"""

from __future__ import annotations

TASA = 0.16
SIN_IVA = {"Efectivo"}


def abono(monto: float, metodo: str) -> float:
    """Cuánto del precio sin IVA cubre un pago."""
    return round(monto if metodo in SIN_IVA else monto / (1 + TASA), 2)


def iva_a_declarar(monto: float) -> float:
    return round(monto - monto / (1 + TASA), 2)


def cobrar(base_pendiente: float, metodo: str) -> float:
    """Cuánto cobrar para liquidar, según cómo pague."""
    return round(base_pendiente if metodo in SIN_IVA else base_pendiente * (1 + TASA), 2)


def estado_cuenta(s, pagos: list[dict], extras: list[dict]) -> dict:
    """
    s: Servicio (parser). pagos: filas de la hoja Pagos de ese evento (sin rechazados).
    extras: filas de 'Extras servicio' de ese evento.
    """
    extras_ok = sum(float(e.get("Importe sin IVA") or 0) for e in extras if e.get("Estado") == "Aprobado")
    extras_pend = sum(float(e.get("Importe sin IVA") or 0) for e in extras if e.get("Estado") == "Por aprobar")
    if s.base is None and not extras_ok:
        return {"base": None, "extras": extras_ok, "extras_por_aprobar": extras_pend, "abonado": 0.0,
                "pendiente": None, "efectivo": None, "transferencia": None, "liquidado": False, "nada_hoy": False}
    base = round((s.base or 0) + extras_ok, 2)
    abonado = 0.0
    if s.anticipo:
        abonado += abono(s.anticipo, s.anticipo_metodo or "Transferencia")
    for p in pagos:
        abonado += abono(float(p["Monto"]), p.get("Método", ""))
    abonado = round(abonado, 2)
    pendiente = round(max(base - abonado, 0.0), 2)
    if s.liquidado_en_calendario:
        pendiente = 0.0
    nada_hoy = base == 0 and getattr(s, "cobro_en_entrega", 0) > 0
    return {
        "base": base, "extras": round(extras_ok, 2), "extras_por_aprobar": round(extras_pend, 2),
        "abonado": abonado, "pendiente": pendiente,
        "efectivo": cobrar(pendiente, "Efectivo"), "transferencia": cobrar(pendiente, "Transferencia"),
        "liquidado": pendiente <= 0.5 and not nada_hoy,
        "nada_hoy": nada_hoy,
    }
