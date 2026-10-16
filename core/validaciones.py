"""Validaciones de comprobantes paraguayos (sin IA)."""
import re
from datetime import date, datetime

TIPOS_SOPORTADOS = {109, 112}  # 109 factura, 112 ticket de máquina registradora
TOLERANCIA_GS = 2  # diferencia de redondeo aceptada, en guaraníes


def calcular_dv(numero: str, base_max: int = 11) -> int:
    """Dígito verificador del RUC (algoritmo módulo 11 de la SET)."""
    digitos = "".join(c if c.isdigit() else str(ord(c.upper())) for c in numero)
    total, k = 0, 2
    for c in reversed(digitos):
        if k > base_max:
            k = 2
        total += int(c) * k
        k += 1
    resto = total % 11
    return 11 - resto if resto > 1 else 0


def solo_digitos(texto) -> str:
    return re.sub(r"\D", "", str(texto or ""))


def normalizar_ruc(ruc, dv="") -> tuple[str, str]:
    """Acepta '80012345-6', '80.012.345', etc. Devuelve (ruc_sin_dv, dv)."""
    texto = str(ruc or "").strip()
    if "-" in texto and not str(dv or "").strip():
        texto, dv = texto.rsplit("-", 1)
    return solo_digitos(texto).lstrip("0"), solo_digitos(dv)


def normalizar_numero(numero) -> str:
    """Lleva el número de comprobante al formato 001-001-0000001."""
    grupos = re.findall(r"\d+", str(numero or ""))
    if len(grupos) == 3:
        a, b, c = grupos
        if len(a) <= 3 and len(b) <= 3 and len(c) <= 7:
            return f"{int(a):03d}-{int(b):03d}-{int(c):07d}"
    if len(grupos) == 1 and len(grupos[0]) == 13:
        g = grupos[0]
        return f"{g[:3]}-{g[3:6]}-{g[6:]}"
    return str(numero or "").strip()


def parsear_fecha(texto) -> date | None:
    texto = str(texto or "").strip()
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def formatear_fecha(texto) -> str:
    f = parsear_fecha(texto)
    return f.strftime("%d/%m/%Y") if f else str(texto or "").strip()


def validar_comprobante(c: dict, cliente_ruc: str, periodo: str, es_duplicado: bool = False) -> list[str]:
    """Devuelve la lista de problemas encontrados. Lista vacía = OK.

    `c` usa las mismas claves que la tabla `comprobantes`.
    `periodo` tiene el formato MM/AAAA.
    """
    errores: list[str] = []

    if c.get("tipo_comprobante") not in TIPOS_SOPORTADOS:
        errores.append("Tipo de comprobante no soportado en esta fase (solo factura o ticket)")

    ruc, dv = str(c.get("ruc") or ""), str(c.get("dv") or "")
    if not re.fullmatch(r"\d{1,8}", ruc):
        errores.append("RUC del proveedor ilegible o con formato inválido")
    elif not dv:
        errores.append(f"Falta el DV del RUC (el DV calculado sería {calcular_dv(ruc)})")
    elif calcular_dv(ruc) != int(dv):
        errores.append(f"DV del RUC incorrecto: dice {dv}, corresponde {calcular_dv(ruc)}")

    if not re.fullmatch(r"\d{8}", str(c.get("timbrado") or "")):
        errores.append("El timbrado debe tener 8 dígitos")

    if not re.fullmatch(r"\d{3}-\d{3}-\d{7}", str(c.get("numero") or "")):
        errores.append("Número de comprobante con formato inválido (001-001-0000000)")

    fecha = parsear_fecha(c.get("fecha"))
    if not fecha:
        errores.append("Fecha de emisión ilegible")
    else:
        mes, anio = periodo.split("/")
        if (fecha.month, fecha.year) != (int(mes), int(anio)):
            errores.append(f"La fecha {fecha:%d/%m/%Y} no corresponde al período {periodo}")
        inicio = parsear_fecha(c.get("vigencia_inicio"))
        fin = parsear_fecha(c.get("vigencia_fin"))
        if inicio and fecha < inicio:
            errores.append("La fecha es anterior al inicio de vigencia del timbrado")
        if fin and fecha > fin:
            errores.append("La fecha es posterior al fin de vigencia del timbrado")

    g10, g5, ex, total = (int(c.get(k) or 0) for k in ("gravada_10", "gravada_5", "exenta", "total"))
    if min(g10, g5, ex, total) < 0:
        errores.append("Hay montos negativos")
    if total <= 0:
        errores.append("El total es cero o no se pudo leer")
    if c.get("tipo_comprobante") == 109 and abs(g10 + g5 + ex - total) > TOLERANCIA_GS:
        errores.append(f"Gravada 10% + gravada 5% + exenta ({g10 + g5 + ex:,}) no coincide con el total ({total:,})")

    iva10, iva5 = int(c.get("iva_10") or 0), int(c.get("iva_5") or 0)
    if iva10 and abs(round(g10 / 11) - iva10) > TOLERANCIA_GS:
        errores.append(f"IVA 10% impreso ({iva10:,}) no coincide con el calculado ({round(g10 / 11):,})")
    if iva5 and abs(round(g5 / 21) - iva5) > TOLERANCIA_GS:
        errores.append(f"IVA 5% impreso ({iva5:,}) no coincide con el calculado ({round(g5 / 21):,})")

    if c.get("condicion") not in (1, 2):
        errores.append("No se pudo determinar si es contado o crédito")

    if (c.get("moneda") or "PYG").upper() not in ("PYG", "GS", "GUARANIES", ""):
        errores.append("Operación en moneda extranjera: no soportada en esta fase")

    receptor = str(c.get("ruc_receptor") or "")
    if c.get("tipo_comprobante") == 109:
        if not receptor:
            errores.append("La factura no muestra el RUC del cliente")
        elif receptor != cliente_ruc:
            errores.append(f"La factura está a nombre del RUC {receptor}, no del cliente {cliente_ruc}")

    if es_duplicado:
        errores.append("Comprobante duplicado (mismo RUC, timbrado y número ya cargado)")

    dudosos = c.get("campos_dudosos") or []
    if dudosos:
        errores.append("La IA marcó como dudosos: " + ", ".join(dudosos))

    return errores
