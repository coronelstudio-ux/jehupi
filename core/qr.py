"""Lectura de códigos QR (facturas electrónicas) sin IA."""
import cv2
import numpy as np


def _leer(img) -> list[str]:
    detector = cv2.QRCodeDetector()
    try:
        ok, textos, _, _ = detector.detectAndDecodeMulti(img)
        if ok:
            encontrados = [t for t in textos if t]
            if encontrados:
                return encontrados
    except cv2.error:
        pass
    texto, _, _ = detector.detectAndDecode(img)
    return [texto] if texto else []


def leer_qr(imagen: bytes) -> list[str]:
    img = cv2.imdecode(np.frombuffer(imagen, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return []
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for variante in (gris, cv2.resize(gris, None, fx=1.5, fy=1.5)):
        textos = _leer(variante)
        if textos:
            return textos
    return []
