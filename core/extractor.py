"""Extracción de datos de facturas con IA (Claude o Gemini): visión + salida JSON con esquema fijo.

El proveedor se elige con PROVEEDOR_IA en el archivo .env. Los dos reciben las mismas
instrucciones y devuelven el mismo JSON, así el resto de la app no cambia.
"""
import base64
import copy
import json
import random
import time

from . import config

_CAMPO_TEXTO = {"type": "string"}
_CAMPO_ENTERO = {"type": "integer"}

ESQUEMA = {
    "type": "object",
    "properties": {
        "contenido_pagina": {
            "type": "string",
            "enum": ["COMPROBANTES", "CONTINUACION", "NO_ES_COMPROBANTE"],
        },
        "comprobantes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "clase": {
                        "type": "string",
                        "enum": ["ELECTRONICA", "PREIMPRESA", "AUTOIMPRESOR", "MANUAL", "TICKET", "OTRO"],
                    },
                    "tipo_documento": {
                        "type": "string",
                        "enum": ["FACTURA", "TICKET", "NOTA_CREDITO", "NOTA_DEBITO", "AUTOFACTURA", "RECIBO", "OTRO"],
                    },
                    "cdc": _CAMPO_TEXTO,
                    "ruc_emisor": _CAMPO_TEXTO,
                    "dv_emisor": _CAMPO_TEXTO,
                    "razon_social_emisor": _CAMPO_TEXTO,
                    "ruc_receptor": _CAMPO_TEXTO,
                    "dv_receptor": _CAMPO_TEXTO,
                    "tipo_doc_receptor": {
                        "type": "string",
                        "enum": ["RUC", "CEDULA", "PASAPORTE", "CEDULA_EXTRANJERA", "SIN_NOMBRE",
                                 "DIPLOMATICO", "ID_TRIBUTARIA"],
                    },
                    "nombre_receptor": _CAMPO_TEXTO,
                    "timbrado": _CAMPO_TEXTO,
                    "vigencia_inicio": _CAMPO_TEXTO,
                    "vigencia_fin": _CAMPO_TEXTO,
                    "numero": _CAMPO_TEXTO,
                    "fecha_emision": _CAMPO_TEXTO,
                    "condicion": {"type": "string", "enum": ["CONTADO", "CREDITO", "NO_INDICA"]},
                    "moneda": _CAMPO_TEXTO,
                    "gravada_10": _CAMPO_ENTERO,
                    "gravada_5": _CAMPO_ENTERO,
                    "exenta": _CAMPO_ENTERO,
                    "total": _CAMPO_ENTERO,
                    "iva_10": _CAMPO_ENTERO,
                    "iva_5": _CAMPO_ENTERO,
                    "campos_dudosos": {"type": "array", "items": {"type": "string"}},
                    "observaciones": _CAMPO_TEXTO,
                },
                "required": [
                    "clase", "tipo_documento", "cdc", "ruc_emisor", "dv_emisor", "razon_social_emisor",
                    "ruc_receptor", "dv_receptor", "tipo_doc_receptor", "nombre_receptor", "timbrado", "vigencia_inicio", "vigencia_fin", "numero",
                    "fecha_emision", "condicion", "moneda", "gravada_10", "gravada_5", "exenta", "total",
                    "iva_10", "iva_5", "campos_dudosos", "observaciones",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["contenido_pagina", "comprobantes"],
    "additionalProperties": False,
}

INSTRUCCIONES = """Sos un asistente contable que transcribe comprobantes paraguayos para el registro de comprobantes de Marangatu (RG 90/2021). Tu trabajo es transcribir lo que está impreso o escrito, no completar ni adivinar.

Cómo clasificar cada comprobante (campo "clase"):
- ELECTRONICA: KuDE de factura electrónica SIFEN/e-Kuatia. Tiene código QR y un CDC de 44 dígitos.
- PREIMPRESA: talonario de imprenta con timbrado impreso, completado a mano o a máquina.
- AUTOIMPRESOR: impresa por sistema del emisor, con timbrado, sin CDC.
- MANUAL: escrita a mano en un formulario sin datos de imprenta claros.
- TICKET: ticket de máquina registradora.

Cómo completar los campos:
- Una página puede tener más de un comprobante: devolvé uno por cada comprobante que veas.
- Si la página es la continuación de un comprobante de la página anterior (sin encabezado propio), usá contenido_pagina = CONTINUACION y comprobantes vacío. Si no es un comprobante (hoja en blanco, otro documento), usá NO_ES_COMPROBANTE.
- RUC: solo los dígitos, sin puntos; el dígito verificador va aparte en dv_emisor / dv_receptor. El emisor es quien emite la factura (arriba, junto al timbrado). El receptor es el cliente ("Señor(es)", "RUC/CI").
- tipo_doc_receptor: RUC si el documento del receptor tiene dígito verificador (ej. 80012345-6) o dice RUC; CEDULA si es un número de cédula sin DV; PASAPORTE, CEDULA_EXTRANJERA, DIPLOMATICO o ID_TRIBUTARIA si así lo indica; SIN_NOMBRE si no figura comprador, dice "sin nombre", "consumidor final" o "innominado", o es un ticket sin datos del comprador. Para CEDULA, ruc_receptor lleva solo los números (sin puntos) y dv_receptor queda vacío.
- nombre_receptor: nombre o razón social del receptor tal como figura.
- timbrado: los 8 dígitos. Vigencia y fecha de emisión en formato dd/mm/aaaa.
- numero: en formato 001-001-0000000 (establecimiento-punto de expedición-número).
- Montos en guaraníes, enteros, sin puntos ni decimales. gravada_10, gravada_5 y exenta son los subtotales de las columnas de venta, CON IVA incluido. total es el total a pagar. iva_10 e iva_5 son los de la liquidación del IVA. Si un monto no figura, poné 0.
- moneda: "PYG" salvo que el comprobante indique otra moneda (por ejemplo "USD").
- cdc: solo para electrónicas, los 44 dígitos sin espacios; si no hay, cadena vacía.
- Si un dato no está o no se lee, dejá la cadena vacía (o 0 en montos) y agregá el nombre del campo a campos_dudosos. Agregá también a campos_dudosos cualquier campo que hayas leído con duda (tachaduras, letra poco clara, papel borroso).
- No inventes datos ni los deduzcas de otros campos: es preferible marcarlos como dudosos."""


VALORES_POR_DEFECTO = {
    "clase": "OTRO", "tipo_documento": "OTRO", "condicion": "NO_INDICA", "moneda": "PYG",
    "tipo_doc_receptor": "SIN_NOMBRE",
    "gravada_10": 0, "gravada_5": 0, "exenta": 0, "total": 0, "iva_10": 0, "iva_5": 0,
    "campos_dudosos": [],
}


def _a_entero(valor) -> int:
    """Convierte '51.000', '51000.0' o 51000 en 51000. Si no se puede, 0."""
    if isinstance(valor, (int, float)):
        return int(round(valor))
    digitos = "".join(c for c in str(valor or "") if c.isdigit())
    return int(digitos) if digitos else 0


def normalizar_resultado(datos: dict) -> dict:
    """Completa campos faltantes y corrige tipos, para que cualquier proveedor entregue lo mismo."""
    items_esquema = ESQUEMA["properties"]["comprobantes"]["items"]
    contenido = datos.get("contenido_pagina")
    if contenido not in ESQUEMA["properties"]["contenido_pagina"]["enum"]:
        contenido = "COMPROBANTES" if datos.get("comprobantes") else "NO_ES_COMPROBANTE"
    comprobantes = []
    for item in datos.get("comprobantes") or []:
        limpio = {}
        for campo, regla in items_esquema["properties"].items():
            valor = item.get(campo, VALORES_POR_DEFECTO.get(campo, ""))
            if regla.get("type") == "integer":
                valor = _a_entero(valor)
            elif regla.get("type") == "array":
                valor = [str(v) for v in valor] if isinstance(valor, list) else []
            else:
                valor = "" if valor is None else str(valor).strip()
                if "enum" in regla and valor not in regla["enum"]:
                    valor = VALORES_POR_DEFECTO.get(campo, regla["enum"][-1])
            limpio[campo] = valor
        faltantes = [c for c in items_esquema["required"] if c not in item]
        if faltantes:
            limpio["campos_dudosos"] = limpio["campos_dudosos"] + faltantes
        comprobantes.append(limpio)
    return {"contenido_pagina": contenido, "comprobantes": comprobantes}


def _pedido(cliente_ruc: str, cliente_nombre: str, tipo_lote: str, cdcs_detectados: list[str] | None) -> str:
    rol = "comprador (receptor)" if tipo_lote == "COMPRAS" else "vendedor (emisor)"
    pedido = (
        f"Lote de {tipo_lote}. Nuestro cliente es {cliente_nombre}, RUC {cliente_ruc}, "
        f"y en estos comprobantes figura como {rol}. Transcribí los comprobantes de esta página."
    )
    if cdcs_detectados:
        pedido += f" En la página se leyó por QR el CDC: {', '.join(cdcs_detectados)}."
    return pedido


# ---------------------------------------------------------------- Claude

def _cliente_claude():
    import anthropic
    # Toma la clave de la variable de entorno ANTHROPIC_API_KEY (archivo .env).
    return anthropic.Anthropic()


def _llamar_claude(imagen: bytes, pedido: str) -> dict:
    parametros = dict(
        model=config.MODELO,
        max_tokens=16000,
        system=INSTRUCCIONES,
        output_config={
            "effort": config.ESFUERZO,
            "format": {"type": "json_schema", "schema": ESQUEMA},
        },
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(imagen).decode("utf-8")}},
                {"type": "text", "text": pedido},
            ],
        }],
    )
    cliente = _cliente_claude()
    if "haiku" in config.MODELO:
        respuesta = cliente.messages.create(**parametros)
    else:
        # Si el filtro de seguridad rechaza la página, la API la reintenta con otro modelo.
        respuesta = cliente.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **parametros
        )

    if respuesta.stop_reason == "refusal":
        raise RuntimeError("Claude rechazó procesar esta página")
    if respuesta.stop_reason == "max_tokens":
        raise RuntimeError("La respuesta de Claude quedó incompleta (demasiados comprobantes en la página)")

    texto = next(b.text for b in respuesta.content if b.type == "text")
    return json.loads(texto)


# ---------------------------------------------------------------- Gemini

def _esquema_gemini(esquema: dict) -> dict:
    """Gemini acepta un subconjunto de JSON Schema: se quita 'additionalProperties'."""
    limpio = copy.deepcopy(esquema)

    def quitar(nodo):
        if isinstance(nodo, dict):
            nodo.pop("additionalProperties", None)
            for v in nodo.values():
                quitar(v)
        elif isinstance(nodo, list):
            for v in nodo:
                quitar(v)

    quitar(limpio)
    return limpio


def _cliente_gemini():
    from google import genai
    import os
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _llamar_gemini(imagen: bytes, pedido: str) -> dict:
    from google.genai import types

    cliente = _cliente_gemini()  # se guarda en una variable: si no, el cliente se cierra antes de enviar
    respuesta = cliente.models.generate_content(
        model=config.GEMINI_MODELO,
        contents=[types.Part.from_bytes(data=imagen, mime_type="image/jpeg"), pedido],
        config=types.GenerateContentConfig(
            system_instruction=INSTRUCCIONES,
            response_mime_type="application/json",
            response_json_schema=_esquema_gemini(ESQUEMA),
            max_output_tokens=16000,
        ),
    )
    if not respuesta.text:
        motivo = ""
        if respuesta.candidates:
            motivo = str(respuesta.candidates[0].finish_reason or "")
        elif respuesta.prompt_feedback:
            motivo = str(respuesta.prompt_feedback.block_reason or "")
        raise RuntimeError(f"Gemini no devolvió datos para esta página {motivo}".strip())
    return json.loads(respuesta.text)


# ---------------------------------------------------------------- entrada única

def _es_limite_temporal(error: Exception) -> bool:
    """True si la IA pidió esperar: demasiadas solicitudes por minuto (429) o servicio saturado (503)."""
    codigo = getattr(error, "code", None) or getattr(error, "status_code", None)
    return codigo in (429, 503, 529)


def _con_reintentos(funcion, *args):
    for intento in range(config.REINTENTOS_IA + 1):
        try:
            return funcion(*args)
        except Exception as e:  # noqa: BLE001
            if not _es_limite_temporal(e) or intento == config.REINTENTOS_IA:
                raise
            # Espera creciente (5, 10, 20, 40 s…) con algo de azar para no reintentar todos juntos.
            time.sleep(min(60, 5 * 2 ** intento) + random.uniform(0, 2))


def extraer_pagina(imagen: bytes, cliente_ruc: str, cliente_nombre: str, tipo_lote: str,
                   cdcs_detectados: list[str] | None = None) -> dict:
    """Envía una página (JPG) al proveedor configurado y devuelve el JSON normalizado según ESQUEMA."""
    pedido = _pedido(cliente_ruc, cliente_nombre, tipo_lote, cdcs_detectados)
    llamar = _llamar_gemini if config.PROVEEDOR_IA == "gemini" else _llamar_claude
    return normalizar_resultado(_con_reintentos(llamar, imagen, pedido))
