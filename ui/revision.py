"""Revisión: imagen de la factura al lado de los datos, para corregir, aprobar o descartar."""
import re

import pandas as pd
import streamlit as st

from core import archivos, cuentas, db, procesador
from core.validaciones import TIPOS_IDENTIFICACION, normalizar_numero, normalizar_ruc, solo_digitos
from ui.comun import ESTADOS, encabezado, gs, nombre_periodo, requiere_cliente, seccion

CLASES = ["PREIMPRESA", "AUTOIMPRESOR", "MANUAL", "TICKET", "ELECTRONICA", "OTRO"]
TIPOS = {109: "Factura", 112: "Ticket"}
CONDICIONES = {1: "Contado", 2: "Crédito"}
ORIGEN_CUENTA = {"IA": "sugerida por la IA", "PROVEEDOR": "recordada del proveedor",
                 "CLIENTE": "recordada del comprador", "MANUAL": "elegida en la revisión"}


def _imagenes(c: dict, paginas: list[dict]) -> None:
    if not paginas:
        st.warning("Imagen no disponible.", icon=":material/hide_image:")
    for pagina in paginas:
        imagen = archivos.bajar(pagina["ruta"])
        if imagen:
            st.image(imagen, caption=f"{pagina['archivo']} · página {pagina['nro']}", width="stretch")
        else:
            st.info(f"{pagina['archivo']} · página {pagina['nro']}: la imagen ya se borró (comprobante cerrado).",
                    icon=":material/image_not_supported:")


def formulario(c: dict, cliente: dict) -> None:
    paginas = db.filas("SELECT * FROM paginas WHERE id=%s OR comprobante_ref=%s ORDER BY nro",
                       (c["pagina_id"] or 0, c["id"]))
    ventas = c["tipo_lote"] == "VENTAS"
    disponibles = cuentas.listar(c["tipo_lote"], cliente["id"])
    ids_cuentas = [None] + [x["id"] for x in disponibles]
    por_id = {x["id"]: x for x in disponibles}

    col_img, col_form = st.columns([1, 1.05], gap="large")
    with col_img:
        _imagenes(c, paginas)
    with col_form:
        st.markdown(f"**{ESTADOS.get(c['estado'], c['estado'])}** · {TIPOS.get(c['tipo_comprobante'], 'Comprobante')} "
                    f"{c['numero'] or ''}")
        for e in c["errores"]:
            st.error(e, icon=":material/error:")
        if c["observaciones"]:
            st.info(c["observaciones"], icon=":material/info:")

        with st.form(f"comp_{c['id']}", border=True):
            seccion("Comprobante")
            f1, f2, f3 = st.columns(3)
            tipo = f1.selectbox("Tipo", list(TIPOS), format_func=TIPOS.get,
                                index=list(TIPOS).index(c["tipo_comprobante"]) if c["tipo_comprobante"] in TIPOS else 0)
            clase = f2.selectbox("Clase", CLASES, CLASES.index(c["clase"]) if c["clase"] in CLASES else 0)
            condicion = f3.selectbox("Condición", list(CONDICIONES), format_func=CONDICIONES.get,
                                     index=0 if c["condicion"] != 2 else 1)
            f1, f2, f3 = st.columns(3)
            numero = f1.text_input("Número", c["numero"] or "", placeholder="001-001-0000001")
            fecha = f2.text_input("Fecha", c["fecha"] or "", placeholder="dd/mm/aaaa")
            periodo = f3.text_input("Período a declarar", c["periodo"], placeholder="MM/AAAA")
            f1, f2, f3 = st.columns(3)
            timbrado = f1.text_input("Timbrado", c["timbrado"] or "")
            vig_ini = f2.text_input("Vigencia desde", c["vigencia_inicio"] or "")
            vig_fin = f3.text_input("Vigencia hasta", c["vigencia_fin"] or "")

            seccion("Comprador" if ventas else "Proveedor")
            tipo_ident = c.get("tipo_identificacion") or "11"
            if ventas:
                tipos_id = list(TIPOS_IDENTIFICACION)
                tipo_ident = st.selectbox("Documento", tipos_id,
                                          index=tipos_id.index(tipo_ident) if tipo_ident in tipos_id else 0,
                                          format_func=lambda t: f"{t} · {TIPOS_IDENTIFICACION[t]}")
            f1, f2, f3 = st.columns([3, 1, 3])
            ruc = f1.text_input("RUC / CI / Nº" if ventas else "RUC", c["ruc"] or "",
                                help="Cédula sin puntos; sin nombre: 44444401" if ventas else None)
            dv = f2.text_input("DV", c["dv"] or "")
            razon_social = f3.text_input("Nombre o razón social", c.get("razon_social") or "")
            ruc_receptor = st.text_input("RUC del cliente en la factura (" + ("emisor" if ventas else "receptor") + ")",
                                         c["ruc_receptor"] or "")

            seccion("Montos (IVA incluido, como en la factura)")
            f1, f2, f3, f4 = st.columns(4)
            g10 = f1.number_input("Gravada 10%", 0, value=int(c["gravada_10"] or 0), step=1)
            g5 = f2.number_input("Gravada 5%", 0, value=int(c["gravada_5"] or 0), step=1)
            ex = f3.number_input("Exenta", 0, value=int(c["exenta"] or 0), step=1)
            total = f4.number_input("Total", 0, value=int(c["total"] or 0), step=1)
            f1, f2, _, _ = st.columns(4)
            iva10 = f1.number_input("IVA 10%", 0, value=int(c["iva_10"] or 0), step=1)
            iva5 = f2.number_input("IVA 5%", 0, value=int(c["iva_5"] or 0), step=1)

            seccion("Contabilidad · Contadis")
            actual = c.get("cuenta_id") if c.get("cuenta_id") in por_id else None
            cuenta_id = st.selectbox(
                "Cuenta", ids_cuentas, index=ids_cuentas.index(actual),
                format_func=lambda i: "— Sin cuenta —" if i is None else cuentas.etiqueta(por_id[i]),
                help=("Origen: " + ORIGEN_CUENTA[c["cuenta_origen"]]) if c.get("cuenta_origen") in ORIGEN_CUENTA
                else "Al aprobar, la cuenta queda recordada para este " + ("comprador" if ventas else "proveedor"))
            concepto = st.text_input("Concepto", c.get("concepto") or "")
            detalle = st.text_area("Detalle de ítems", c.get("detalle") or "", height=68)

            seccion("Imputación")
            f1, f2, f3 = st.columns(3)
            imp_iva = f1.checkbox("IVA", bool(c["imputa_iva"]))
            imp_ire = f2.checkbox("IRE", bool(c["imputa_ire"]))
            imp_irp = f3.checkbox("IRP-RSP", bool(c["imputa_irp"]))

            b1, b2, b3 = st.columns(3)
            guardar = b1.form_submit_button("Guardar", icon=":material/save:", width="stretch", help="Guarda y vuelve a validar")
            aprobar = b2.form_submit_button("Aprobar", type="primary", icon=":material/check:", width="stretch")
            descartar = b3.form_submit_button("Descartar", icon=":material/delete:", width="stretch")

    if not (guardar or aprobar or descartar):
        return
    usuario = st.session_state.usuario["usuario"]
    if descartar:
        db.actualizar_comprobante(c["id"], {"estado": "DESCARTADA"}, usuario, "descartar")
        archivos.liberar([p["id"] for p in paginas])
        st.rerun()

    if ventas and tipo_ident != "11":
        ruc_n, dv_n = (solo_digitos(ruc) if tipo_ident in ("12", "15") else ruc.strip()), ""
    else:
        ruc_n, dv_n = normalizar_ruc(ruc, dv)
    periodo = periodo.strip()
    if periodo == c["periodo"] and fecha.strip() != (c["fecha"] or ""):
        periodo = procesador.periodo_de_fecha(fecha) or periodo  # el período sigue a la fecha corregida
    if not re.fullmatch(r"(0[1-9]|1[0-2])/\d{4}", periodo):
        st.error("El período debe tener el formato MM/AAAA, por ejemplo 04/2026.")
        return
    nuevo = {
        **c, "periodo": periodo, "clase": clase, "tipo_comprobante": tipo, "ruc": ruc_n, "dv": dv_n,
        "tipo_identificacion": tipo_ident, "razon_social": razon_social.strip(),
        "ruc_receptor": normalizar_ruc(ruc_receptor)[0], "timbrado": solo_digitos(timbrado),
        "numero": normalizar_numero(numero), "fecha": fecha.strip(), "vigencia_inicio": vig_ini.strip(),
        "vigencia_fin": vig_fin.strip(), "gravada_10": g10, "gravada_5": g5, "exenta": ex, "total": total,
        "iva_10": iva10, "iva_5": iva5, "condicion": condicion, "imputa_iva": int(imp_iva),
        "imputa_ire": int(imp_ire), "imputa_irp": int(imp_irp), "campos_dudosos": [],
        "concepto": concepto.strip(), "detalle": detalle.strip(), "cuenta_id": cuenta_id,
        "cuenta_origen": c.get("cuenta_origen") if cuenta_id == actual else ("MANUAL" if cuenta_id else ""),
    }
    procesador.evaluar(nuevo, cliente)
    if aprobar:
        accion = "aprobar" if not nuevo["errores"] else "aprobar_con_observaciones"
        nuevo["estado"] = "APROBADA"
    else:
        accion = "editar"
    db.actualizar_comprobante(c["id"], nuevo, usuario, accion)
    if aprobar:
        cuentas.recordar(cliente["id"], c["tipo_lote"], ruc_n, cuenta_id)
        archivos.liberar([p["id"] for p in paginas])
    st.rerun()


def pagina() -> None:
    encabezado("Revisión", "Corregí y aprobá los comprobantes con la imagen a la vista")
    ctx = requiere_cliente()
    if not ctx:
        return
    cliente, periodo = ctx
    f1, f2 = st.columns([1, 2])
    with f1:
        tipo_lote = st.segmented_control("Registro", ["COMPRAS", "VENTAS"], default="COMPRAS",
                                         format_func=str.capitalize, key="rev_tipo") or "COMPRAS"
    with f2:
        estados = st.pills("Estados", list(ESTADOS), default=["REVISAR"], selection_mode="multi",
                           format_func=ESTADOS.get, key="rev_estados")
    if not estados:
        st.caption("Elegí al menos un estado.")
        return
    marcas = ",".join("%s" for _ in estados)
    lista = db.filas(
        f"SELECT c.*, p.archivo, p.nro AS pagina FROM comprobantes c LEFT JOIN paginas p ON p.id=c.pagina_id "
        f"WHERE c.cliente_id=%s AND c.tipo_lote=%s AND c.periodo=%s AND c.estado IN ({marcas}) ORDER BY c.id",
        (cliente["id"], tipo_lote, periodo, *estados))
    if not lista:
        st.success(f"No hay comprobantes con esos estados en {nombre_periodo(periodo)}.", icon=":material/task_alt:")
        return
    tabla = pd.DataFrame(lista)
    tabla["_orden"] = pd.to_datetime(tabla["fecha"], format="%d/%m/%Y", errors="coerce")
    tabla = tabla.sort_values(["_orden", "id"], na_position="last").reset_index(drop=True)
    tabla["estado"] = tabla["estado"].map(lambda e: ESTADOS.get(e, e))
    tabla["avisos"] = tabla["errores"].map(lambda e: len(e or []))
    tabla["total"] = tabla["total"].map(gs)
    tabla["cuenta"] = tabla["cuenta_id"].map(lambda i: "✓" if pd.notna(i) else "—")
    seleccion = st.dataframe(
        tabla[["estado", "fecha", "numero", "ruc", "razon_social", "total", "cuenta", "avisos", "archivo", "pagina"]],
        hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={
            "estado": "Estado", "fecha": "Fecha", "numero": "Número", "ruc": "RUC / CI",
            "razon_social": "Razón social", "cuenta": st.column_config.TextColumn("Cuenta", width="small"),
            "total": st.column_config.TextColumn("Total"),
            "avisos": st.column_config.NumberColumn("Avisos", width="small"),
            "archivo": "Archivo", "pagina": st.column_config.NumberColumn("Pág.", width="small"),
        })
    filas_sel = seleccion.selection.rows
    if filas_sel:
        st.divider()
        formulario(db.leer_comprobante(int(tabla.iloc[filas_sel[0]]["id"])), cliente)
    else:
        st.caption("Seleccioná una fila para ver la imagen y corregir.")
