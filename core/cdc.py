"""Código de Control (CDC) de 44 dígitos de los documentos electrónicos SIFEN."""
import re
from datetime import datetime

from .validaciones import calcular_dv

TIPOS_DE = {
    "01": "Factura electrónica",
    "04": "Autofactura electrónica",
    "05": "Nota de crédito electrónica",
    "06": "Nota de débito electrónica",
    "07": "Nota de remisión electrónica",
}


def dv_valido(cdc: str) -> bool:
    return len(cdc) == 44 and cdc.isdigit() and calcular_dv(cdc[:43]) == int(cdc[43])


def desde_qr(texto: str) -> str | None:
    """El QR del KuDE es una URL de e-Kuatia con el parámetro Id=<CDC>."""
    m = re.search(r"[?&]Id=(\d{44})", texto or "")
    if m and dv_valido(m.group(1)):
        return m.group(1)
    digitos = re.sub(r"\D", "", texto or "")
    return digitos if dv_valido(digitos) else None


def buscar_en_texto(texto: str) -> list[str]:
    """Busca CDC en el texto de un PDF digital. En el KuDE suele ir en grupos de 4 dígitos."""
    encontrados = []
    for m in re.finditer(r"\d[\d  ]{42,70}\d", texto or ""):
        digitos = re.sub(r"\D", "", m.group(0))
        for i in range(0, len(digitos) - 43):
            candidato = digitos[i:i + 44]
            if dv_valido(candidato) and candidato not in encontrados:
                encontrados.append(candidato)
                break
    return encontrados


def parsear(cdc: str) -> dict:
    """Separa los datos que trae el CDC dentro de sus 44 dígitos."""
    try:
        fecha = datetime.strptime(cdc[25:33], "%Y%m%d").strftime("%d/%m/%Y")
    except ValueError:
        fecha = ""
    return {
        "tipo_de": TIPOS_DE.get(cdc[0:2], f"Tipo {cdc[0:2]}"),
        "ruc": cdc[2:10].lstrip("0"),
        "dv": cdc[10],
        "numero": f"{cdc[11:14]}-{cdc[14:17]}-{cdc[17:24]}",
        "fecha": fecha,
    }
