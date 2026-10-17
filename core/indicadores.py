"""Indicadores del mes para el panel de inicio (sin dependencias de la interfaz)."""
from .contadis import ESTADOS_INCLUIDOS, separar_iva


def resumen_registro(comprobantes: list[dict]) -> dict:
    """Cantidades por estado e importes (base e IVA) de un registro (compras o ventas) del mes."""
    r = {"total": 0, "revisar": 0, "listos": 0, "electronicas": 0, "exportados": 0, "sin_cuenta": 0,
         "base": 0, "iva": 0, "importe": 0}
    for c in comprobantes:
        if c["estado"] == "DESCARTADA":
            continue
        r["total"] += 1
        r["revisar"] += c["estado"] == "REVISAR"
        r["listos"] += c["estado"] in ("OK", "APROBADA")
        r["electronicas"] += c.get("clase") == "ELECTRONICA"
        r["exportados"] += bool(c.get("exportado"))
        if c["estado"] in ESTADOS_INCLUIDOS:
            r["sin_cuenta"] += not c.get("cuenta_id")
            base5, iva5 = separar_iva(c.get("gravada_5"), c.get("iva_5"), 21)
            base10, iva10 = separar_iva(c.get("gravada_10"), c.get("iva_10"), 11)
            r["base"] += base5 + base10
            r["iva"] += iva5 + iva10
            r["importe"] += int(c.get("total") or 0)
    return r


def resumen_mes(comprobantes: list[dict]) -> dict:
    compras = resumen_registro([c for c in comprobantes if c["tipo_lote"] == "COMPRAS"])
    ventas = resumen_registro([c for c in comprobantes if c["tipo_lote"] == "VENTAS"])
    return {
        "COMPRAS": compras,
        "VENTAS": ventas,
        # Estimación con lo cargado en la app: IVA débito (ventas) menos IVA crédito (compras).
        "saldo_iva": ventas["iva"] - compras["iva"],
    }
