"""Convierte los archivos subidos (PDF, JPG, PNG) en una imagen JPG por página, en memoria."""
import io
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from . import config

EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def _a_jpg(img: Image.Image) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((config.MAX_LADO_PX, config.MAX_LADO_PX))
    salida = io.BytesIO()
    img.save(salida, "JPEG", quality=85)
    return salida.getvalue()


def paginas_desde_archivo(nombre: str, datos: bytes, inicio: int = 1):
    """Genera {nro, archivo, texto, jpg} página por página (no carga todo el PDF en memoria).

    `texto` solo viene lleno en PDF digitales (por ejemplo, un KuDE descargado).
    """
    extension = Path(nombre).suffix.lower()
    if extension == ".pdf":
        with pymupdf.open(stream=datos, filetype="pdf") as doc:
            for i, page in enumerate(doc):
                pix = page.get_pixmap(dpi=config.DPI_PDF)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                yield {"nro": inicio + i, "archivo": nombre, "texto": page.get_text(), "jpg": _a_jpg(img)}
    elif extension in EXTENSIONES_IMAGEN:
        img = Image.open(io.BytesIO(datos))
        for i in range(getattr(img, "n_frames", 1)):  # los TIFF pueden tener varias páginas
            img.seek(i)
            yield {"nro": inicio + i, "archivo": nombre, "texto": "",
                   "jpg": _a_jpg(ImageOps.exif_transpose(img.copy()))}
    else:
        raise ValueError(f"Formato no soportado: {nombre}. Use PDF, JPG o PNG.")


def contar_paginas(nombre: str, datos: bytes) -> int:
    if Path(nombre).suffix.lower() == ".pdf":
        with pymupdf.open(stream=datos, filetype="pdf") as doc:
            return len(doc)
    return getattr(Image.open(io.BytesIO(datos)), "n_frames", 1)
