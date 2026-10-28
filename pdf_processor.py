"""
pdf_processor.py
----------------
Extracción de texto de contratos en PDF y segmentación en cláusulas.

- extract_text_from_pdf(pdf_bytes) -> str
- segment_into_clauses(text) -> list[dict]

Estrategia de segmentación (en orden de prioridad):
1. Encabezados contractuales al inicio de línea:
     "CLÁUSULA PRIMERA", "Cláusula 3", "DÉCIMA SEGUNDA.", "1.", "2)", "1.1", "3.2.1"
2. Si hay menos de 2 encabezados: párrafos separados por líneas en blanco.
3. Si tampoco hay párrafos: líneas que terminan en punto (texto sin formato).

Nota: solo funciona con PDF que tienen texto. Los PDF escaneados (imágenes)
requieren OCR, que está fuera del alcance de este módulo.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class PDFProcessingError(ValueError):
    """El PDF no se pudo leer o no contiene texto extraíble."""


# Segmentos más cortos que esto se consideran títulos sueltos, no cláusulas.
MIN_CLAUSE_CHARS = 25

# ---------------------------------------------------------------------------
# Patrones de encabezado
# ---------------------------------------------------------------------------
_ORDINALES = (
    r"PRIMER[AO]|SEGUND[AO]|TERCER[AO]|CUART[AO]|QUINT[AO]|SEXT[AO]|S[ÉE]PTIM[AO]|"
    r"OCTAV[AO]|NOVEN[AO]|D[ÉE]CIM[AO]|UND[ÉE]CIM[AO]|DUOD[ÉE]CIM[AO]|"
    r"VIG[ÉE]SIM[AO]|TRIG[ÉE]SIM[AO]"
)
# Ordinal compuesto opcional: "DÉCIMA PRIMERA", "VIGÉSIMA TERCERA".
_ORDINAL = rf"(?:{_ORDINALES})(?:[\s\-]+(?:{_ORDINALES}))?"

_HEADER_RE = re.compile(
    rf"""^[ \t]*(?P<id>
          CL[ÁA]USULA[ \t]+(?:{_ORDINAL}|\d{{1,3}})   # CLÁUSULA PRIMERA / CLÁUSULA 3
        | (?:{_ORDINAL})(?=[ \t]*[.:\-–—])            # PRIMERA.  /  DÉCIMA SEGUNDA:
        | \d{{1,2}}(?:\.\d{{1,2}})+\.?(?=[ \t])       # 1.1  /  3.2.1.
        | \d{{1,2}}[.)](?=[ \t])                      # 1.  /  2)
    )[ \t]*[.:\-–—]?[ \t]*""",
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

# Título en mayúsculas justo después del encabezado: "CLÁUSULA SEGUNDA. PRECIO. El valor..."
_TITLE_RE = re.compile(r"^(?P<titulo>[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ ,/\-]{2,60}?)[ \t]*[.:\-–—][ \t]*")

# Líneas típicas de pie/encabezado de página.
_PAGE_NOISE_RE = re.compile(r"^\s*(p[áa]gina\s+\d+(\s+de\s+\d+)?|\d+\s*/\s*\d+|-\s*\d+\s*-)\s*$",
                            re.IGNORECASE | re.MULTILINE)


# ===========================================================================
# Extracción
# ===========================================================================
def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extrae el texto de todas las páginas del PDF, separadas por saltos de línea."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            # Muchos PDF vienen "cifrados" con contraseña vacía (solo restricciones).
            if not reader.decrypt(""):
                raise PDFProcessingError("El PDF está protegido con contraseña.")
        paginas = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError as exc:
        raise PDFProcessingError(f"Archivo PDF inválido o dañado: {exc}") from exc

    texto = "\n".join(paginas)
    if not texto.strip():
        raise PDFProcessingError(
            "El PDF no contiene texto extraíble (posiblemente es un documento escaneado)."
        )
    return texto


# ===========================================================================
# Segmentación
# ===========================================================================
def _limpiar(texto: str) -> str:
    """Normaliza saltos de línea y elimina ruido de paginación."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    texto = _PAGE_NOISE_RE.sub("", texto)
    texto = re.sub(r"(\w)-\n(\w)", r"\1\2", texto)   # une palabras cortadas con guion
    return texto


def _unir_lineas(texto: str) -> str:
    """Convierte el texto de una cláusula (con saltos de línea del PDF) en un solo párrafo."""
    return re.sub(r"\s+", " ", texto).strip()


def _por_encabezados(texto: str) -> list[dict]:
    matches = list(_HEADER_RE.finditer(texto))
    if len(matches) < 2:
        return []

    segmentos = []
    # Texto previo al primer encabezado (partes, considerandos...) se conserva como preámbulo.
    preambulo = _unir_lineas(texto[: matches[0].start()])
    if len(preambulo) >= MIN_CLAUSE_CHARS:
        segmentos.append({"identificador": "PREÁMBULO", "titulo": None, "texto": preambulo})

    for i, m in enumerate(matches):
        fin = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        bruto = texto[m.end():fin]
        titulo = None
        # Título en su propia línea y en mayúsculas: "1. OBJETO\nEl proveedor..."
        primera, _, resto = bruto.partition("\n")
        primera = primera.strip()
        if resto.strip() and primera and len(primera) <= 60 and primera == primera.upper() \
                and any(ch.isalpha() for ch in primera):
            titulo, bruto = primera.rstrip(".:-–— "), resto
        cuerpo = _unir_lineas(bruto)
        t = None if titulo else _TITLE_RE.match(cuerpo)
        if t:
            titulo, cuerpo = t.group("titulo").strip(), cuerpo[t.end():]
        segmentos.append({
            "identificador": _unir_lineas(m.group("id")).upper(),
            "titulo": titulo,
            "texto": cuerpo,
        })
    return segmentos


def _por_parrafos(texto: str) -> list[dict]:
    bloques = [b for b in re.split(r"\n[ \t]*\n", texto) if b.strip()]
    if len(bloques) < 2:
        # Sin líneas en blanco: cortar donde una línea termina en punto.
        bloques = [b for b in re.split(r"(?<=[.;:])[ \t]*\n", texto) if b.strip()]
    return [
        {"identificador": f"P{i}", "titulo": None, "texto": _unir_lineas(b)}
        for i, b in enumerate(bloques, start=1)
    ]


def _fusionar_titulos(segmentos: list[dict]) -> list[dict]:
    """Un segmento demasiado corto (p. ej. "CLÁUSULA PRIMERA - OBJETO") pasa a ser título del siguiente."""
    resultado: list[dict] = []
    pendiente: dict | None = None
    for seg in segmentos:
        if len(seg["texto"]) < MIN_CLAUSE_CHARS:
            # Guardamos su identificador/título para el siguiente segmento.
            etiqueta = " ".join(x for x in (seg["titulo"], seg["texto"]) if x)
            pendiente = {"identificador": seg["identificador"], "titulo": etiqueta or None}
            continue
        if pendiente:
            seg = {**seg, "titulo": seg["titulo"] or pendiente["titulo"],
                   "seccion": pendiente["identificador"]}
            pendiente = None
        resultado.append(seg)
    return resultado


def segment_into_clauses(text: str) -> list[dict]:
    """
    Divide el texto de un contrato en cláusulas.

    Retorna una lista de dicts:
        {"numero": int, "identificador": str, "titulo": str | None, "texto": str}
    - numero: posición secuencial (1, 2, 3...).
    - identificador: encabezado original ("CLÁUSULA PRIMERA", "1.1") o "P<n>" si se
      segmentó por párrafos.
    - seccion (opcional): encabezado padre cuando un título suelto se fusionó.
    """
    texto = _limpiar(text or "")
    if not texto.strip():
        return []

    segmentos = _por_encabezados(texto) or _por_parrafos(texto)
    segmentos = _fusionar_titulos(segmentos)
    return [{"numero": i, **seg} for i, seg in enumerate(segmentos, start=1)]


if __name__ == "__main__":
    # Uso: python pdf_processor.py contrato.pdf
    import sys

    with open(sys.argv[1], "rb") as f:
        for c in segment_into_clauses(extract_text_from_pdf(f.read())):
            print(f"[{c['numero']}] {c['identificador']} | {c['titulo']}\n    {c['texto'][:110]}")
