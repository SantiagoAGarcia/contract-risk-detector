"""
main.py
-------
API REST (FastAPI) para clasificar cláusulas y contratos.

Multitenancy:
- Todas las rutas exigen el encabezado `X-Tenant-ID` (400 si falta).
- Las rutas de negocio exigen además `X-API-Key`. La clave identifica a la
  organización: si falta o no es válida -> 401; si no pertenece al
  X-Tenant-ID enviado -> 403.
- Los datos se guardan en PostgreSQL con Row Level Security (ver db/schema.sql),
  de modo que cada organización solo puede leer sus propios contratos.
- El modo "llm" envía texto a un tercero, así que solo está disponible para
  organizaciones con opt-in (tenants.llm_habilitado) -> 403 si no lo tienen.

Ejecutar en local (requiere PostgreSQL, ver docker-compose.yml):
    uvicorn main:app --reload --port 8000 --env-file .env
"""

from __future__ import annotations

import time
from functools import lru_cache
from typing import Literal, Optional
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field

import db
from db import DatabaseUnavailable, TenantContext
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
    version="0.2.0",
)


# ---------------------------------------------------------------------------
# Middleware y manejo de errores
# ---------------------------------------------------------------------------
@app.middleware("http")
async def response_time_header(request: Request, call_next):
    """Agrega X-Response-Time-ms para medir tiempos de respuesta."""
    inicio = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - inicio) * 1000:.1f}"
    return response


@app.exception_handler(DatabaseUnavailable)
@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(PoolTimeout)
async def database_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"Base de datos no disponible: {exc}"})


# ---------------------------------------------------------------------------
# Dependencias de multitenancy y autenticación
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


def get_tenant_context(
    tenant_id: str = Depends(get_tenant_id),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> TenantContext:
    """Autentica la API key y verifica que pertenezca al tenant del encabezado."""
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Encabezado X-API-Key es obligatorio")
    ctx = db.authenticate(x_api_key)
    if ctx is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o revocada")
    if ctx.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La API key no pertenece a la organización indicada")
    return ctx


def check_modo_permitido(ctx: TenantContext, modo: str) -> None:
    """Política de datos: el modo LLM requiere opt-in explícito de la organización."""
    if modo == "llm" and not ctx.llm_habilitado:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "El modo LLM no está habilitado para esta organización (requiere aceptación explícita)",
        )


# ---------------------------------------------------------------------------
# Clasificadores (instancias únicas, creadas bajo demanda)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_rules_classifier() -> RuleBasedClassifier:
    return RuleBasedClassifier()


@lru_cache(maxsize=1)
def get_llm_classifier() -> LLMClassifier:
    # Se crea perezosamente para que el modo "reglas" funcione sin clave de LLM.
    return LLMClassifier()


def clasificar(textos: list[str], modo: str) -> list[dict]:
    """Clasifica una lista de textos con el motor indicado (bloqueante)."""
    if modo == "reglas":
        clf = get_rules_classifier()
        return [clf.classify(t) for t in textos]
    try:
        llm = get_llm_classifier()
        return [llm.classify(t) for t in textos]
    except LLMClassifierError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


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


# Los endpoints que usan la base de datos o el LLM son funciones normales (def):
# FastAPI las ejecuta en un hilo aparte, así no bloquean el event loop.
@app.post("/api/v1/classify-clause", response_model=ClauseResponse, response_model_exclude_none=True)
def classify_clause(
    payload: ClauseRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> ClauseResponse:
    check_modo_permitido(ctx, payload.modo)
    resultado = clasificar([payload.texto], payload.modo)[0]
    # En la auditoría no se guarda el texto, solo metadatos.
    db.log_action(ctx, "classify_clause", detalle={
        "modo": payload.modo, "es_riesgo": resultado["es_riesgo"],
        "categoria": resultado["categoria"], "longitud": len(payload.texto),
    })
    return ClauseResponse(tenant_id=ctx.tenant_id, modo=payload.modo, **resultado)


@app.post("/api/v1/analyze-contract-pdf")
def analyze_contract_pdf(
    file: UploadFile = File(..., description="Contrato en PDF"),
    modo: Literal["reglas", "llm"] = Query("reglas", description="Motor de clasificación"),
    formato: Literal["json", "html"] = Query("json", alias="format", description="Formato del reporte"),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """
    Analiza un contrato completo en PDF:
    extrae el texto -> segmenta en cláusulas -> clasifica cada una -> guarda -> reporte semáforo.
    """
    check_modo_permitido(ctx, modo)

    # --- Validación del archivo ---------------------------------------------
    pdf_bytes = file.file.read(MAX_PDF_BYTES + 1)
    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise HTTPException(413, f"El PDF supera el máximo de {MAX_PDF_BYTES // (1024 * 1024)} MB")
    # Se valida por la firma del archivo, no solo por la extensión o el content-type.
    if not pdf_bytes.startswith(b"%PDF"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "El archivo no es un PDF válido")

    # --- Extracción y segmentación -------------------------------------------
    try:
        texto = extract_text_from_pdf(pdf_bytes)
    except PDFProcessingError as exc:
        raise HTTPException(422, str(exc))

    clausulas = segment_into_clauses(texto)
    if not clausulas:
        raise HTTPException(422, "No se encontraron cláusulas en el PDF")
    if len(clausulas) > MAX_CLAUSULAS:
        raise HTTPException(422, f"El contrato tiene {len(clausulas)} cláusulas; el máximo es {MAX_CLAUSULAS}")

    # --- Clasificación y persistencia ----------------------------------------
    resultados = clasificar([c["texto"] for c in clausulas], modo)
    analizadas = [{**c, **r} for c, r in zip(clausulas, resultados)]
    nombre = file.filename or "contrato.pdf"
    reporte = generate_json_report(analizadas)
    contract_id = db.save_contract(ctx, nombre, modo, reporte, analizadas)

    # --- Reporte -------------------------------------------------------------
    if formato == "html":
        return HTMLResponse(generate_html_report(analizadas, nombre),
                            headers={"X-Contract-ID": str(contract_id)})
    return {"tenant_id": ctx.tenant_id, "contract_id": str(contract_id),
            "contrato": nombre, "modo": modo, **reporte}


@app.get("/api/v1/contracts")
def list_contracts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Contratos analizados por la organización (más recientes primero)."""
    return {"tenant_id": ctx.tenant_id, "contratos": db.list_contracts(ctx, limit, offset)}


@app.get("/api/v1/contracts/{contract_id}")
def get_contract(
    contract_id: UUID,
    formato: Literal["json", "html"] = Query("json", alias="format"),
    ctx: TenantContext = Depends(get_tenant_context),
):
    """Reporte de un contrato guardado. 404 si no existe o es de otra organización."""
    encontrado = db.get_contract(ctx, contract_id)
    if encontrado is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contrato no encontrado")
    contrato, clausulas = encontrado

    if formato == "html":
        return HTMLResponse(generate_html_report(clausulas, contrato["nombre_archivo"]))
    return {"tenant_id": ctx.tenant_id, "contrato": contrato, **generate_json_report(clausulas)}


@app.delete("/api/v1/contracts/{contract_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(
    contract_id: UUID,
    ctx: TenantContext = Depends(get_tenant_context),
) -> Response:
    """Borra un contrato y sus resultados (procedimiento de borrado de datos)."""
    if not db.delete_contract(ctx, contract_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Contrato no encontrado")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/v1/audit-log")
def audit_log(
    limit: int = Query(100, ge=1, le=500),
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Registro de auditoría de la organización."""
    return {"tenant_id": ctx.tenant_id, "eventos": db.list_audit(ctx, limit)}
