"""
main.py
-------
API REST (FastAPI) para clasificar cláusulas contractuales.

Multitenancy básico: todas las rutas de negocio exigen el encabezado HTTP
`X-Tenant-ID`. Si falta, se responde 400 Bad Request.

Ejecutar en local:
    uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from llm_classifier import LLMClassifier, LLMClassifierError
from pdf_processor import PDFProcessingError, extract_text_from_pdf, segment_into_clauses
from report_generator import generate_html_report, generate_json_report
from rules_classifier import RuleBasedClassifier

# Límites para el análisis de contratos en PDF.
MAX_PDF_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_CLAUSULAS = 300
# Los códigos 413/422 se usan como números: sus nombres en starlette.status cambiaron entre versiones.

app = FastAPI(
    title="Detector de Cláusulas de Riesgo",
    description="Clasificación de cláusulas de riesgo en contratos de obra y proveeduría (español).",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Dependencia de multitenancy
# ---------------------------------------------------------------------------
def get_tenant_id(
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-ID"),
) -> str:
    """Valida la presencia del encabezado X-Tenant-ID y lo retorna."""
    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Encabezado X-Tenant-ID es obligatorio",
        )
    return x_tenant_id.strip()


# ---------------------------------------------------------------------------
# Clasificadores (instancias únicas, creadas bajo demanda)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_rules_classifier() -> RuleBasedClassifier:
    return RuleBasedClassifier()


@lru_cache(maxsize=1)
def get_llm_classifier() -> LLMClassifier:
    # Se crea perezosamente para que el modo "reglas" funcione sin OPENAI_API_KEY.
    return LLMClassifier()


# ---------------------------------------------------------------------------
# Esquemas
# ---------------------------------------------------------------------------
class ClauseRequest(BaseModel):
    texto: str = Field(..., min_length=1, description="Cláusula a analizar")
    modo: Literal["reglas", "llm"] = Field("reglas", description="Motor de clasificación")


class ClauseResponse(BaseModel):
    tenant_id: str
    modo: str
    es_riesgo: bool
    categoria: str
    patrones_activados: Optional[list[str]] = None  # solo modo "reglas"
    razon: Optional[str] = None                     # solo modo "llm"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health(tenant_id: str = Depends(get_tenant_id)) -> dict:
    return {"status": "ok", "tenant_id": tenant_id}


@app.post("/api/v1/classify-clause", response_model=ClauseResponse, response_model_exclude_none=True)
async def classify_clause(
    payload: ClauseRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> ClauseResponse:
    if payload.modo == "reglas":
        resultado = get_rules_classifier().classify(payload.texto)
    else:
        try:
            clf = get_llm_classifier()
            # La llamada a OpenAI es bloqueante: se ejecuta en un hilo aparte.
            resultado = await run_in_threadpool(clf.classify, payload.texto)
        except LLMClassifierError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    return ClauseResponse(tenant_id=tenant_id, modo=payload.modo, **resultado)


@app.post("/api/v1/analyze-contract-pdf")
async def analyze_contract_pdf(
    file: UploadFile = File(..., description="Contrato en PDF"),
    modo: Literal["reglas", "llm"] = Query("reglas", description="Motor de clasificación"),
    formato: Literal["json", "html"] = Query("json", alias="format", description="Formato del reporte"),
    tenant_id: str = Depends(get_tenant_id),
):
    """
    Analiza un contrato completo en PDF:
    extrae el texto -> segmenta en cláusulas -> clasifica cada una -> reporte semáforo.
    """
    # --- Validación del archivo ---------------------------------------------
    pdf_bytes = await file.read(MAX_PDF_BYTES + 1)
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(413,
                            f"El PDF supera el máximo de {MAX_PDF_BYTES // (1024 * 1024)} MB")
    # Se valida por la firma del archivo, no solo por la extensión o el content-type.
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "El archivo no es un PDF válido")

    # --- Extracción y segmentación (CPU, en un hilo aparte) ------------------
    try:
        texto = await run_in_threadpool(extract_text_from_pdf, pdf_bytes)
    except PDFProcessingError as exc:
        raise HTTPException(422, str(exc))

    clausulas = segment_into_clauses(texto)
    if not clausulas:
        raise HTTPException(422, "No se encontraron cláusulas en el PDF")
    if len(clausulas) > MAX_CLAUSULAS:
        raise HTTPException(422,
                            f"El contrato tiene {len(clausulas)} cláusulas; el máximo es {MAX_CLAUSULAS}")

    # --- Clasificación -------------------------------------------------------
    if modo == "reglas":
        clf = get_rules_classifier()
        resultados = [clf.classify(c["texto"]) for c in clausulas]
    else:
        try:
            llm = get_llm_classifier()
            resultados = await run_in_threadpool(lambda: [llm.classify(c["texto"]) for c in clausulas])
        except LLMClassifierError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    analizadas = [{**c, **r} for c, r in zip(clausulas, resultados)]
    nombre = file.filename or "contrato.pdf"

    # --- Reporte -------------------------------------------------------------
    if formato == "html":
        return HTMLResponse(generate_html_report(analizadas, nombre))

    return {
        "tenant_id": tenant_id,
        "contrato": nombre,
        "modo": modo,
        **generate_json_report(analizadas),
    }
