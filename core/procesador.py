"""Orquesta el proceso: páginas -> clasificación -> extracción -> validación -> base de datos."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from . import cdc as cdc_mod
from . import archivos, config, db
from .extractor import extraer_pagina
from .ingesta import paginas_desde_archivo
from .qr import leer_qr
from .validaciones import (NUMERO_SIN_NOMBRE, formatear_fecha, normalizar_numero, normalizar_ruc, parsear_fecha, solo_digitos,
                           validar_comprobante)

TIPO_DOCUMENTO = {"FACTURA": 109, "TICKET": 112}
TIPO_IDENTIFICACION = {"RUC": "11", "CEDULA": "12", "PASAPORTE": "13", "CEDULA_EXTRANJERA": "14",
                       "SIN_NOMBRE": "15", "DIPLOMATICO": "16", "ID_TRIBUTARIA": "17"}
CONDICION = {"CONTADO": 1, "CREDITO": 2}


def analizar_pagina(pagina: dict, cliente: dict, tipo_lote: str, extractor: Callable = extraer_pagina) -> dict:
    """Devuelve {"comprobantes": [...], "contenido_pagina": ..., "cdcs": [...], "fuente": ...}."""
    # 1) PDF digital (KuDE descargado): el CDC está en el texto, no hace falta IA.
    cdcs_texto = cdc_mod.buscar_en_texto(pagina.get("texto", ""))
    if cdcs_texto:
        return {"contenido_pagina": "COMPROBANTES", "comprobantes": [], "cdcs": cdcs_texto, "fuente": "texto"}

    # 2) Imagen o escaneo: intentar leer el QR.
    cdcs_qr = [c for c in (cdc_mod.desde_qr(t) for t in leer_qr(pagina["jpg"])) if c]

    # 3) Claude transcribe la página (también si hay QR, por si la hoja tiene otras facturas).
    try:
        resultado = extractor(pagina["jpg"], cliente["ruc"], cliente["nombre"], tipo_lote, cdcs_qr)
    except Exception as e:  # noqa: BLE001 - se registra el error y la página queda para revisión
        if cdcs_qr:
            return {"contenido_pagina": "COMPROBANTES", "comprobantes": [], "cdcs": cdcs_qr, "fuente": "qr"}
        return {"error": str(e), "comprobantes": [], "cdcs": []}
    resultado["cdcs"] = cdcs_qr
    resultado["fuente"] = "claude"
    return resultado


def periodo_de_fecha(fecha) -> str | None:
    """MM/AAAA de una fecha dd/mm/aaaa, o None si no se puede leer."""
    f = parsear_fecha(fecha)
    return f"{f.month:02d}/{f.year}" if f else None


def asignar_periodo(c: dict) -> dict:
    """Cada comprobante va al mes de su fecha de emisión, aunque haya venido en el lote de otro mes."""
    periodo = periodo_de_fecha(c.get("fecha"))
    if periodo and periodo != c["periodo"]:
        nota = f"Asignado al período {periodo} según su fecha (vino en el lote de {c['periodo']})."
        c["observaciones"] = " ".join(x for x in (c.get("observaciones"), nota) if x)
        c["periodo"] = periodo
    return c


def _registro_electronico(cdc: str, base: dict) -> dict:
    datos = cdc_mod.parsear(cdc)
    registro = {
        **base, "clase": "ELECTRONICA", "cdc": cdc, "ruc": datos["ruc"], "dv": datos["dv"],
        "numero": datos["numero"], "fecha": datos["fecha"], "estado": "EXCLUIDA",
        "observaciones": f"{datos['tipo_de']}: ya figura en Marangatu, no se importa.",
    }
    return asignar_periodo(registro)


def comprador_de_venta(tipo_doc: str, numero: str, dv: str) -> tuple[str, str, str]:
    """Ventas: (tipo_identificacion, numero, dv) del comprador, con las reglas de la oficina."""
    tipo = TIPO_IDENTIFICACION.get(tipo_doc, "15")
    if tipo == "11":
        numero, dv = normalizar_ruc(numero, dv)  # si falta el DV, la validación lo marca para revisar
    if tipo == "12":
        numero, dv = solo_digitos(numero).lstrip("0"), ""
    if tipo == "15" or not str(numero).strip():
        return "15", NUMERO_SIN_NOMBRE, ""
    if tipo not in ("11", "12"):
        numero, dv = str(numero).strip(), ""
    return tipo, numero, dv


def _registro_desde_ia(item: dict, base: dict, cliente: dict) -> dict:
    tipo_identificacion = "11"
    razon_social = item["razon_social_emisor"]
    ruc, dv = normalizar_ruc(item["ruc_emisor"], item["dv_emisor"])
    ruc_receptor, _ = normalizar_ruc(item["ruc_receptor"], item["dv_receptor"])
    if base["tipo_lote"] == "VENTAS":
        # En ventas la contraparte es el comprador (receptor) y el emisor es el cliente del estudio.
        tipo_identificacion, ruc, dv = comprador_de_venta(item["tipo_doc_receptor"], item["ruc_receptor"],
                                                          item["dv_receptor"])
        razon_social = item["nombre_receptor"]
        ruc_receptor, _ = normalizar_ruc(item["ruc_emisor"], item["dv_emisor"])
    registro = {
        **base,
        "clase": item["clase"],
        "tipo_comprobante": TIPO_DOCUMENTO.get(item["tipo_documento"]),
        "cdc": solo_digitos(item["cdc"]) or None,
        "tipo_identificacion": tipo_identificacion,
        "ruc": ruc, "dv": dv,
        "razon_social": razon_social,
        "ruc_receptor": ruc_receptor,
        "timbrado": solo_digitos(item["timbrado"]),
        "vigencia_inicio": formatear_fecha(item["vigencia_inicio"]),
        "vigencia_fin": formatear_fecha(item["vigencia_fin"]),
        "numero": normalizar_numero(item["numero"]),
        "fecha": formatear_fecha(item["fecha_emision"]),
        "gravada_10": item["gravada_10"], "gravada_5": item["gravada_5"], "exenta": item["exenta"],
        "total": item["total"], "iva_10": item["iva_10"], "iva_5": item["iva_5"],
        "condicion": CONDICION.get(item["condicion"]),
        "moneda": (item["moneda"] or "PYG").upper(),
        "campos_dudosos": item["campos_dudosos"],
        "observaciones": item["observaciones"],
    }
    return asignar_periodo(registro)


def evaluar(c: dict, cliente: dict) -> dict:
    """Corre las validaciones y define el estado (OK o REVISAR) de un comprobante no electrónico."""
    duplicado = db.es_duplicado(c["cliente_id"], c["tipo_lote"], c.get("ruc"), c.get("timbrado"),
                                c.get("numero"), c.get("id") or 0)
    c["errores"] = validar_comprobante(c, cliente["ruc"], c["periodo"], duplicado, c["tipo_lote"])
    c["estado"] = "REVISAR" if c["errores"] else "OK"
    return c


def procesar_lote(lote_id: int, archivos_subidos: list[tuple[str, bytes]], extractor: Callable = extraer_pagina,
                  progreso: Callable[[int, int, str], None] | None = None) -> dict:
    lote = db.fila("SELECT * FROM lotes WHERE id=%s", (lote_id,))
    cliente = db.fila("SELECT * FROM clientes WHERE id=%s", (lote["cliente_id"],))

    # 1) Páginas -> imagen JPG, que se guarda (temporalmente) en el almacenamiento.
    paginas = []
    for nombre, datos in archivos_subidos:
        for p in paginas_desde_archivo(nombre, datos, inicio=len(paginas) + 1):
            p["ruta"] = f"lote_{lote_id:06d}/pag_{p['nro']:04d}.jpg"
            archivos.almacen().subir(p["ruta"], p["jpg"])
            p["id"] = db.ejecutar(
                "INSERT INTO paginas (lote_id, nro, archivo, ruta) VALUES (%s,%s,%s,%s) RETURNING id",
                (lote_id, p["nro"], p["archivo"], p["ruta"]))
            paginas.append(p)
            if progreso:
                progreso(0, 1, f"Preparando página {p['nro']}…")

    # 2) Clasificación y lectura, varias páginas a la vez.
    total = len(paginas)
    resultados: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, config.HILOS)) as pool:
        futuros = {pool.submit(analizar_pagina, p, cliente, lote["tipo"], extractor): p for p in paginas}
        for i, futuro in enumerate(as_completed(futuros), start=1):
            p = futuros[futuro]
            resultados[p["nro"]] = futuro.result()
            if progreso:
                progreso(i, total, f"Leídas {i} de {total} páginas")

    # 3) Validación y guardado, en orden de página (para duplicados y continuaciones).
    resumen = {"paginas": total, "OK": 0, "REVISAR": 0, "EXCLUIDA": 0, "errores_pagina": 0, "periodos": {}}
    ultimo_id = None
    for p in paginas:
        p.pop("jpg", None)
        r = resultados[p["nro"]]
        db.ejecutar("UPDATE paginas SET resultado=%s, error=%s WHERE id=%s", (r, r.get("error"), p["id"]))
        base = {
            "lote_id": lote_id, "cliente_id": cliente["id"], "pagina_id": p["id"],
            "tipo_lote": lote["tipo"], "periodo": lote["periodo"],
            "imputa_iva": cliente["imputa_iva"], "imputa_ire": cliente["imputa_ire"],
            "imputa_irp": cliente["imputa_irp"],
        }
        if r.get("error"):
            resumen["errores_pagina"] += 1
            ultimo_id = db.insertar_comprobante(
                {**base, "estado": "REVISAR", "errores": [f"No se pudo procesar la página: {r['error']}"]})
            resumen["REVISAR"] += 1
            continue
        if r.get("contenido_pagina") == "CONTINUACION" and ultimo_id:
            # La imagen de esta página se conserva mientras el comprobante anterior siga abierto.
            db.ejecutar("UPDATE paginas SET comprobante_ref=%s WHERE id=%s", (ultimo_id, p["id"]))
            anterior = db.leer_comprobante(ultimo_id)
            db.actualizar_comprobante(ultimo_id, {
                "estado": "REVISAR",
                "errores": anterior["errores"] + [f"Continúa en la página {p['nro']}: verificar totales"],
            }, "sistema", "continuacion")
            continue

        cdcs_pendientes = list(r.get("cdcs", []))
        registros = []
        for item in r.get("comprobantes", []):
            if item["clase"] == "ELECTRONICA":
                cdc = solo_digitos(item["cdc"])
                if not cdc_mod.dv_valido(cdc) and cdcs_pendientes:
                    cdc = cdcs_pendientes[0]
                if cdc in cdcs_pendientes:
                    cdcs_pendientes.remove(cdc)
                if cdc_mod.dv_valido(cdc):
                    registros.append(_registro_electronico(cdc, base))
                else:
                    reg = _registro_desde_ia(item, base, cliente)
                    reg.update(estado="REVISAR", errores=["Parece electrónica pero el CDC no es válido: verificar"])
                    registros.append(reg)
            else:
                registros.append(evaluar(_registro_desde_ia(item, base, cliente), cliente))
        # CDC leídos por QR o texto que la IA no listó.
        registros += [_registro_electronico(c, base) for c in cdcs_pendientes]

        for reg in registros:
            ultimo_id = db.insertar_comprobante(reg)
            resumen[reg["estado"]] += 1
            resumen["periodos"][reg["periodo"]] = resumen["periodos"].get(reg["periodo"], 0) + 1

    db.ejecutar("UPDATE lotes SET estado='PROCESADO' WHERE id=%s", (lote_id,))
    # Hojas en blanco u otros documentos: su imagen ya no hace falta.
    archivos.liberar([p["id"] for p in paginas])
    return resumen
