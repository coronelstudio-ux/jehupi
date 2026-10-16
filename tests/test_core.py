import io
import json
import zipfile

import cv2
import numpy as np
import pymupdf
import pytest

from core import cdc as cdc_mod
from core import archivos, config, db, exportar, procesador
from core.validaciones import calcular_dv, normalizar_numero, normalizar_ruc, validar_comprobante


def _cdc_valido(base43: str) -> str:
    return base43 + str(calcular_dv(base43))


CDC = _cdc_valido("01" + "80012345".zfill(8) + str(calcular_dv("80012345")) + "001" + "002" + "0000123"
                  + "2" + "20260915" + "1" + "123456789")


# ---------- validaciones ----------

@pytest.mark.parametrize("ruc,dv", [("4327185", 5), ("1895550", 9), ("5850580", 6)])
def test_dv_ruc(ruc, dv):
    assert calcular_dv(ruc) == dv


def test_normalizaciones():
    assert normalizar_ruc("1.895.550-9") == ("1895550", "9")
    assert normalizar_numero("1-1-45645") == "001-001-0045645"
    assert normalizar_numero("0010010045645") == "001-001-0045645"


def _comprobante(**cambios):
    c = {"tipo_comprobante": 109, "ruc": "1895550", "dv": "9", "timbrado": "18586180", "numero": "001-001-0045645",
         "fecha": "09/04/2026", "vigencia_inicio": "19/01/2026", "vigencia_fin": "31/01/2027",
         "gravada_10": 51000, "gravada_5": 0, "exenta": 0, "total": 51000, "iva_10": 4636, "iva_5": 0,
         "condicion": 1, "moneda": "PYG", "ruc_receptor": "5850580", "campos_dudosos": []}
    c.update(cambios)
    return c


def test_comprobante_correcto():
    assert validar_comprobante(_comprobante(), "5850580", "04/2026") == []


@pytest.mark.parametrize("cambio,texto", [
    ({"dv": "3"}, "DV del RUC incorrecto"),
    ({"timbrado": "1858618"}, "timbrado"),
    ({"total": 60000}, "no coincide con el total"),
    ({"iva_10": 5000}, "IVA 10%"),
    ({"fecha": "09/05/2026"}, "período"),
    ({"fecha": "09/02/2027"}, "fin de vigencia"),
    ({"ruc_receptor": "123"}, "a nombre del RUC"),
    ({"condicion": None}, "contado o crédito"),
    ({"campos_dudosos": ["total"]}, "dudosos"),
])
def test_comprobante_con_errores(cambio, texto):
    errores = validar_comprobante(_comprobante(**cambio), "5850580", "04/2026")
    assert any(texto in e for e in errores), errores


# ---------- CDC ----------

def test_cdc():
    assert cdc_mod.dv_valido(CDC)
    assert not cdc_mod.dv_valido(CDC[:-1] + str((int(CDC[-1]) + 1) % 10))
    datos = cdc_mod.parsear(CDC)
    assert datos["ruc"] == "80012345" and datos["numero"] == "001-002-0000123" and datos["fecha"] == "15/09/2026"
    agrupado = " ".join(CDC[i:i + 4] for i in range(0, 44, 4))
    assert cdc_mod.buscar_en_texto(f"CDC: {agrupado}\nTotal 100") == [CDC]
    assert cdc_mod.desde_qr(f"https://ekuatia.set.gov.py/consultas/qr?nVersion=150&Id={CDC}&dFeEmiDE=x") == CDC


# ---------- proceso completo con un extractor simulado ----------

def _pdf(paginas_texto: list[str]) -> bytes:
    doc = pymupdf.open()
    for t in paginas_texto:
        doc.new_page().insert_text((72, 72), t)
    return doc.tobytes()


def _png_con_qr(texto: str) -> bytes:
    qr = cv2.QRCodeEncoder.create().encode(texto)
    qr = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    lienzo = np.full((qr.shape[0] + 200, qr.shape[1] + 200), 255, np.uint8)
    lienzo[100:-100, 100:-100] = qr
    return cv2.imencode(".png", lienzo)[1].tobytes()


def _item(**cambios):
    item = {"clase": "PREIMPRESA", "tipo_documento": "FACTURA", "cdc": "", "ruc_emisor": "1895550", "dv_emisor": "9",
            "razon_social_emisor": "Librería", "ruc_receptor": "5850580", "dv_receptor": "6", "timbrado": "18586180",
            "vigencia_inicio": "19/01/2026", "vigencia_fin": "31/01/2027", "numero": "001-001-45645",
            "fecha_emision": "09/04/2026", "condicion": "CONTADO", "moneda": "PYG", "gravada_10": 51000,
            "gravada_5": 0, "exenta": 0, "total": 51000, "iva_10": 4636, "iva_5": 0, "campos_dudosos": [],
            "observaciones": ""}
    item.update(cambios)
    return item


def _cliente_y_lote(periodo="04/2026"):
    cliente_id = db.ejecutar("INSERT INTO clientes (ruc, dv, nombre) VALUES ('5850580','6','Cliente Prueba') RETURNING id")
    lote_id = db.ejecutar("INSERT INTO lotes (cliente_id, tipo, periodo, creado_por) "
                          "VALUES (%s, 'COMPRAS', %s, 'test') RETURNING id", (cliente_id, periodo))
    return cliente_id, lote_id


def test_proceso_y_exportacion(base):
    cliente_id, lote_id = _cliente_y_lote()
    llamadas = []

    def extractor_falso(imagen, ruc, nombre, tipo, cdcs):
        assert imagen[:2] == b"\xff\xd8"  # la IA recibe la página como JPG
        llamadas.append(cdcs)
        n = len(llamadas)
        if cdcs:  # página con QR: KuDE impreso y escaneado
            return {"contenido_pagina": "COMPROBANTES", "comprobantes": [_item(clase="ELECTRONICA", cdc="")]}
        if n == 1:  # dos facturas en la misma hoja, la segunda duplicada
            return {"contenido_pagina": "COMPROBANTES", "comprobantes": [_item(), _item(numero="001-001-0045646", total=60000)]}
        return {"contenido_pagina": "COMPROBANTES", "comprobantes": [_item()]}

    archivos = [
        ("lote.pdf", _pdf(["factura escaneada"])),
        ("kude.pdf", _pdf(["KuDE de factura electronica CDC " + " ".join(CDC[i:i + 4] for i in range(0, 44, 4))])),
        ("foto_qr.png", _png_con_qr(f"https://ekuatia.set.gov.py/consultas/qr?Id={CDC}")),
        ("repetida.jpg", _png_con_qr("no es un cdc")),
    ]
    resumen = procesador.procesar_lote(lote_id, archivos, extractor=extractor_falso)

    assert resumen["paginas"] == 4
    assert len(llamadas) == 3  # el KuDE digital no pasa por la IA
    comps = db.filas("SELECT * FROM comprobantes ORDER BY id")
    estados = [(c["clase"], c["estado"]) for c in comps]
    assert estados[0] == ("PREIMPRESA", "OK")
    assert estados[1] == ("PREIMPRESA", "REVISAR")  # total no cuadra
    assert ("ELECTRONICA", "EXCLUIDA") in estados
    assert comps[-1]["estado"] == "REVISAR" and any("duplicado" in e for e in comps[-1]["errores"])

    van, no_van = exportar.exportables(comps)
    assert len(van) == 1
    (nombre, contenido, cantidad), = exportar.generar_zips(van, "5850580", "04/2026", 1)
    assert nombre == "5850580_REG_042026_V0001.zip" and cantidad == 1
    with zipfile.ZipFile(io.BytesIO(contenido)) as z:
        assert z.namelist() == ["5850580_REG_042026_V0001.txt"]
        linea = z.read(z.namelist()[0]).decode("utf-8").rstrip("\r\n")
    assert linea.split("\t") == ["2", "11", "1895550", "", "109", "09/04/2026", "18586180", "001-001-0045645",
                                 "51000", "0", "0", "51000", "1", "N", "S", "N", "N", "N", "", ""]
    assert exportar.generar_excel_control(van, no_van, {})[:2] == b"PK"


def test_division_en_varios_archivos(monkeypatch):
    monkeypatch.setattr(config, "MAX_FILAS_ARCHIVO", 2)
    c = {"tipo_comprobante": 109, "ruc": "1", "fecha": "01/04/2026", "timbrado": "12345678", "numero": "001-001-0000001",
         "gravada_10": 11, "gravada_5": 0, "exenta": 0, "total": 11, "condicion": 1, "imputa_iva": 1, "imputa_ire": 0,
         "imputa_irp": 0}
    zips = exportar.generar_zips([c] * 5, "80012345", "04/2026", 3)
    assert [z[0] for z in zips] == [f"80012345_REG_042026_V000{i}.zip" for i in (3, 4, 5)]
    assert [z[2] for z in zips] == [2, 2, 1]


# ---------- período por fecha de emisión ----------

def test_factura_de_otro_mes_va_a_su_periodo(base):
    cliente_id, lote_id = _cliente_y_lote()
    items = [_item(), _item(numero="001-001-0046021", fecha_emision="07/05/2026", gravada_10=85000, total=85000, iva_10=7727)]
    resumen = procesador.procesar_lote(lote_id, [("lote.pdf", _pdf(["x"]))],
                                       extractor=lambda *a: {"contenido_pagina": "COMPROBANTES", "comprobantes": items})
    assert resumen["OK"] == 2 and resumen["periodos"] == {"04/2026": 1, "05/2026": 1}
    mayo = db.fila("SELECT * FROM comprobantes WHERE numero='001-001-0046021'")
    assert mayo["periodo"] == "05/2026" and mayo["estado"] == "OK"
    assert "vino en el lote de 04/2026" in mayo["observaciones"]


# ---------- proveedores de IA ----------

from core import extractor  # noqa: E402


def test_normalizar_resultado():
    crudo = {"contenido_pagina": "raro", "comprobantes": [
        {"clase": "PREIMPRESA", "tipo_documento": "FACTURA", "total": "51.000", "gravada_10": 51000.0,
         "condicion": "contado", "campos_dudosos": None, "timbrado": 18586180}]}
    r = extractor.normalizar_resultado(crudo)
    c = r["comprobantes"][0]
    assert r["contenido_pagina"] == "COMPROBANTES"
    assert c["total"] == 51000 and c["gravada_10"] == 51000 and c["timbrado"] == "18586180"
    assert c["condicion"] == "NO_INDICA"  # valor fuera de la lista: se marca como no indicado
    assert "numero" in c["campos_dudosos"] and c["numero"] == ""  # campo que faltó: queda dudoso


def _imagen(tmp_path):
    from PIL import Image
    salida = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(salida, "JPEG")
    return salida.getvalue()


def test_extractor_gemini(tmp_path, monkeypatch):
    from google.genai import types
    capturado = {}

    class Respuesta:
        text = '{"contenido_pagina": "COMPROBANTES", "comprobantes": [{"clase": "PREIMPRESA", "total": 100}]}'

    class Modelos:
        def generate_content(self, **kwargs):
            capturado.update(kwargs)
            return Respuesta()

    class Cliente:
        models = Modelos()

    monkeypatch.setattr(config, "PROVEEDOR_IA", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODELO", "gemini-prueba")
    monkeypatch.setattr(extractor, "_cliente_gemini", lambda: Cliente())
    r = extractor.extraer_pagina(_imagen(tmp_path), "5850580", "Cliente", "COMPRAS", [])
    assert r["comprobantes"][0]["total"] == 100
    assert capturado["model"] == "gemini-prueba"
    assert isinstance(capturado["contents"][0], types.Part)
    cfg = capturado["config"]
    assert cfg.response_mime_type == "application/json"
    assert "additionalProperties" not in json.dumps(cfg.response_json_schema)


def test_extractor_claude(tmp_path, monkeypatch):
    capturado = {}

    class Bloque:
        type = "text"
        text = '{"contenido_pagina": "NO_ES_COMPROBANTE", "comprobantes": []}'

    class Respuesta:
        stop_reason = "end_turn"
        content = [Bloque()]

    class Mensajes:
        def create(self, **kwargs):
            capturado.update(kwargs)
            return Respuesta()

    class Cliente:
        class beta:
            messages = Mensajes()

    monkeypatch.setattr(config, "PROVEEDOR_IA", "claude")
    monkeypatch.setattr(extractor, "_cliente_claude", lambda: Cliente())
    r = extractor.extraer_pagina(_imagen(tmp_path), "5850580", "Cliente", "COMPRAS", [])
    assert r == {"contenido_pagina": "NO_ES_COMPROBANTE", "comprobantes": []}
    assert capturado["model"] == config.MODELO and capturado["fallbacks"] == "default"


def test_problema_configuracion(monkeypatch):
    monkeypatch.setattr(config, "PROVEEDOR_IA", "gemini")
    monkeypatch.setattr(config, "GEMINI_MODELO", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert "GEMINI_API_KEY" in config.problema_configuracion()
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert "GEMINI_MODELO" in config.problema_configuracion()
    monkeypatch.setattr(config, "GEMINI_MODELO", "m")
    assert config.problema_configuracion() is None
    monkeypatch.setattr(config, "PROVEEDOR_IA", "otro")
    assert "claude" in config.problema_configuracion()


# ---------- reintentos ante el límite por minuto de la IA ----------

def test_reintenta_si_la_ia_pide_esperar(monkeypatch):
    from google.genai import errors
    monkeypatch.setattr(extractor.time, "sleep", lambda s: None)
    llamadas = []

    def llamar(imagen, pedido):
        llamadas.append(1)
        if len(llamadas) < 3:
            raise errors.ClientError(429, {"error": {"message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED"}})
        return {"contenido_pagina": "NO_ES_COMPROBANTE", "comprobantes": []}

    assert extractor._con_reintentos(llamar, b"", "")["contenido_pagina"] == "NO_ES_COMPROBANTE"
    assert len(llamadas) == 3

    def falla(imagen, pedido):
        raise errors.ClientError(400, {"error": {"message": "API key not valid", "status": "INVALID_ARGUMENT"}})
    with pytest.raises(errors.ClientError):  # un error que no es de límite no se reintenta
        extractor._con_reintentos(falla, b"", "")


# ---------- usuarios ----------

def test_usuarios(base):
    assert not db.hay_usuarios()
    db.crear_usuario("Admin", "Administrador", "clave-segura-1", es_admin=True)
    u = db.autenticar("admin", "clave-segura-1")
    assert u and u["es_admin"] is True
    assert db.autenticar("admin", "otra") is None
    db.cambiar_clave(u["id"], "clave-nueva-2")
    assert db.autenticar("admin", "clave-nueva-2")


# ---------- imágenes temporales ----------

def _existe(base, ruta):
    return (base / ruta).exists()


def test_imagenes_se_borran_al_cerrar_comprobantes(base):
    cliente_id, lote_id = _cliente_y_lote()
    respuestas = [
        {"contenido_pagina": "COMPROBANTES", "comprobantes": [_item()]},                                 # p1: OK
        {"contenido_pagina": "COMPROBANTES", "comprobantes": [_item(numero="001-001-0000777", total=1)]},  # p2: REVISAR
        {"contenido_pagina": "CONTINUACION", "comprobantes": []},                                        # p3: sigue a p2
        {"contenido_pagina": "NO_ES_COMPROBANTE", "comprobantes": []},                                   # p4: hoja en blanco
    ]
    procesador.procesar_lote(lote_id, [("lote.pdf", _pdf(["1", "2", "3", "4"]))],
                             extractor=lambda *a: respuestas.pop(0))
    pag = {p["nro"]: p for p in db.filas("SELECT * FROM paginas ORDER BY nro")}
    rutas = {n: f"lote_{lote_id:06d}/pag_{n:04d}.jpg" for n in pag}

    # La hoja en blanco se borra enseguida; las demás se conservan para revisar.
    assert pag[4]["ruta"] is None and not _existe(base, rutas[4])
    assert all(_existe(base, rutas[n]) for n in (1, 2, 3))

    # Aprobar el comprobante de la p2 libera la p2 y su continuación (p3).
    c2 = db.fila("SELECT * FROM comprobantes WHERE pagina_id=%s", (pag[2]["id"],))
    assert pag[3]["comprobante_ref"] == c2["id"]
    db.actualizar_comprobante(c2["id"], {"estado": "APROBADA"}, "test", "aprobar")
    assert archivos.liberar([pag[2]["id"], pag[3]["id"]]) == 2
    assert not _existe(base, rutas[2]) and not _existe(base, rutas[3])

    # La p1 (OK) se conserva hasta que se exporta.
    assert archivos.liberar([pag[1]["id"]]) == 0 and _existe(base, rutas[1])
    db.ejecutar("UPDATE comprobantes SET exportado = true WHERE pagina_id=%s", (pag[1]["id"],))
    assert archivos.liberar([pag[1]["id"]]) == 1 and not _existe(base, rutas[1])
    assert archivos.bajar(None) is None


def test_pagina_con_dos_facturas_espera_a_ambas(base):
    cliente_id, lote_id = _cliente_y_lote()
    procesador.procesar_lote(lote_id, [("hoja.pdf", _pdf(["1"]))], extractor=lambda *a: {
        "contenido_pagina": "COMPROBANTES",
        "comprobantes": [_item(numero="001-001-0000001", total=1), _item(numero="001-001-0000002", total=1)]})
    pagina = db.fila("SELECT * FROM paginas")
    a, b = db.filas("SELECT id FROM comprobantes ORDER BY id")
    db.actualizar_comprobante(a["id"], {"estado": "APROBADA"}, "test", "aprobar")
    assert archivos.liberar([pagina["id"]]) == 0      # falta la otra factura de la hoja
    db.actualizar_comprobante(b["id"], {"estado": "DESCARTADA"}, "test", "descartar")
    assert archivos.liberar([pagina["id"]]) == 1
