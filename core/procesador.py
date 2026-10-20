"""Orquesta el proceso: páginas -> clasificación -> extracción -> validación -> base de datos."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from . import cdc as cdc_mod
from . import archivos, config, cuentas, db
from .extractor import extraer_pagina
from .ingesta import paginas_desde_archivo
from .qr import leer_qr
from .validaciones import (NUMERO_SIN_NOMBRE, formatear_fecha, normalizar_numero, normalizar_ruc, parsear_fecha, solo_digitos,
                           validar_comprobante)

TIPO_DOCUMENTO = {"FACTURA": 109, "TICKET": 112}
TIPO_IDENTIFICACION = {"RUC": "11", "CEDULA": "12", "PASAPORTE": "13", "CEDULA_EXTRANJERA": "14",
                       "SIN_NOMBRE": "15", "DIPLOMATICO": "16", "ID_TRIBUTARIA": "17"}
CONDICION = {"CONTADO": 1, "CREDITO": 2}


def analizar_pagina(pagina: dict, cliente: dict, tipo_lote: str, extractor: Callable = extraer_pagina,
                    cuentas_disponibles: list[dict] | None = None) -> dict:
    """Devuelve {"comprobantes": [...], "contenido_pagina": ..., "cdcs": [...], "fuente": ...}."""
    # 1) El CDC de las electrónicas se toma sin IA: del texto (KuDE en PDF digital) o del QR.
    cdcs = cdc_mod.buscar_en_texto(pagina.get("texto", "")) or \
        [c for c in (cdc_mod.desde_qr(t) for t in leer_qr(pagina["jpg"])) if c]

    # 2) La IA lee la página siempre: también los montos de las electrónicas, que no se
    #    importan a Marangatu pero sí se cargan en el sistema contable (Contadis).
    try:
        resultado = extractor(pagina["jpg"], cliente["ruc"], cliente["nombre"], tipo_lote, cdcs,
                              cuentas=cuentas_disponibles)
    except Exception as e:  # noqa: BLE001 - se registra el error y la página queda para revisión
        if cdcs:  # al menos se registran las electrónicas por su CDC (sin montos)
            return {"contenido_pagina": "COMPROBANTES", "comprobantes": [], "cdcs": cdcs, "fuente": "cdc",
                    "aviso": str(e)}
        return {"error": str(e), "comprobantes": [], "cdcs": []}
    resultado["cdcs"] = cdcs
    resultado["fuente"] = "ia"
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


def _registro_electronico(cdc: str, base: dict, item: dict | None = None, cliente: dict | None = None) -> dict:
    """Factura electrónica: no va a Marangatu (ya figura), pero se guardan sus datos para el sistema contable.

    Número, fecha y RUC del emisor salen del CDC (son exactos); montos, timbrado y comprador, de la IA.
    """
    datos = cdc_mod.parsear(cdc)
    nota = f"{datos['tipo_de']}: ya figura en Marangatu, no se importa."
    if item is None:
        registro = {**base, "ruc": datos["ruc"], "dv": datos["dv"], "observaciones": nota + " Sin montos: completar."}
        if base["tipo_lote"] == "VENTAS":
            registro.update(ruc="", dv="", ruc_receptor=datos["ruc"])
    else:
        registro = _registro_desde_ia(item, base, cliente)
        # La nota de período se recalcula abajo con la fecha del CDC.
        registro["observaciones"] = " ".join(x for x in (nota, item.get("observaciones")) if x)
        if base["tipo_lote"] == "VENTAS":
            registro["ruc_receptor"] = datos["ruc"]
        else:
            registro.update(ruc=datos["ruc"], dv=datos["dv"])
    registro.update(clase="ELECTRONICA", cdc=cdc, numero=datos["numero"], fecha=datos["fecha"],
                    estado="EXCLUIDA", periodo=base["periodo"])
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
        "concepto": (item.get("concepto") or "")[:120],
        "detalle": (item.get("detalle") or "")[:500],
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
    disponibles = cuentas.listar(lote["tipo"], cliente["id"])

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
        futuros = {pool.submit(analizar_pagina, p, cliente, lote["tipo"], extractor, disponibles): p
                   for p in paginas}
        for i, futuro in enumerate(as_completed(futuros), start=1):
            p = futuros[futuro]
            resultados[p["nro"]] = futuro.result()
            if progreso:
                progreso(i, total, f"Leídas {i} de {total} páginas")

    # 3) Validación y guardado, en orden de página (para duplicados y continuaciones).
    resumen = {"paginas": total, "OK": 0, "REVISAR": 0, "EXCLUIDA": 0, "errores_pagina": 0, "periodos": {},
               "detalle": []}
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
            resumen["detalle"].append({"archivo": p["archivo"], "pagina": p["nro"],
                                       "resultado": "Error al leer: queda para revisar"})
            continue
        if r.get("contenido_pagina") == "CONTINUACION" and ultimo_id:
            # La imagen de esta página se conserva mientras el comprobante anterior siga abierto.
            db.ejecutar("UPDATE paginas SET comprobante_ref=%s WHERE id=%s", (ultimo_id, p["id"]))
            anterior = db.leer_comprobante(ultimo_id)
            db.actualizar_comprobante(ultimo_id, {
                "estado": "REVISAR",
                "errores": anterior["errores"] + [f"Continúa en la página {p['nro']}: verificar totales"],
            }, "sistema", "continuacion")
            resumen["detalle"].append({"archivo": p["archivo"], "pagina": p["nro"],
                                       "resultado": "Continúa la factura anterior"})
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
                    registros.append(cuentas.asignar(_registro_electronico(cdc, base, item, cliente),
                                                     item.get("cuenta_id"), disponibles))
                else:
                    reg = _registro_desde_ia(item, base, cliente)
                    reg.update(estado="REVISAR", errores=["Parece electrónica pero el CDC no es válido: verificar"])
                    registros.append(cuentas.asignar(reg, item.get("cuenta_id"), disponibles))
            else:
                reg = cuentas.asignar(_registro_desde_ia(item, base, cliente), item.get("cuenta_id"), disponibles)
                registros.append(evaluar(reg, cliente))
        # CDC leídos por QR o texto que la IA no listó.
        registros += [cuentas.asignar(_registro_electronico(c, base), None, disponibles) for c in cdcs_pendientes]

        for reg in registros:
            ultimo_id = db.insertar_comprobante(reg)
            resumen[reg["estado"]] += 1
            resumen["periodos"][reg["periodo"]] = resumen["periodos"].get(reg["periodo"], 0) + 1
        if registros:
            resumen["detalle"].append({"archivo": p["archivo"], "pagina": p["nro"],
                                       "resultado": f"{len(registros)} comprobante(s) cargado(s)"})
        else:
            # Antes esta página desaparecía sin aviso: ahora se informa para revisarla a mano.
            resumen["detalle"].append({"archivo": p["archivo"], "pagina": p["nro"],
                                       "resultado": "Sin comprobante: la IA no la reconoció como factura"})

    db.ejecutar("UPDATE lotes SET estado='PROCESADO' WHERE id=%s", (lote_id,))
    # Hojas en blanco u otros documentos: su imagen ya no hace falta.
    archivos.liberar([p["id"] for p in paginas])
    return resumen
