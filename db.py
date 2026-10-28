"""
db.py
-----
Acceso a PostgreSQL con aislamiento por tenant (Row Level Security).

- La API se conecta con DATABASE_URL usando el rol app_user (sin BYPASSRLS).
- Toda operación sobre datos se hace dentro de tenant_transaction(tenant_id),
  que fija app.tenant_id en la transacción. Las políticas RLS de schema.sql
  filtran por ese valor, así que aunque una consulta olvide el WHERE
  tenant_id = ..., la base de datos no devuelve datos de otro tenant.
- Las API keys se guardan como hash SHA-256 y se validan con la función
  SECURITY DEFINER auth_api_key().
"""

from __future__ import annotations

import hashlib
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

API_KEY_PREFIX = "crd_"


class DatabaseUnavailable(RuntimeError):
    """No hay DATABASE_URL configurada o la base de datos no responde."""


@dataclass(frozen=True)
class TenantContext:
    """Identidad autenticada de una petición."""

    tenant_id: str
    api_key_id: UUID
    llm_habilitado: bool


# ===========================================================================
# Conexiones
# ===========================================================================
@lru_cache(maxsize=1)
def get_pool() -> ConnectionPool:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise DatabaseUnavailable("La variable de entorno DATABASE_URL no está definida.")
    # open=True abre las conexiones mínimas al crear el pool.
    return ConnectionPool(
        url,
        min_size=1,
        max_size=int(os.getenv("DB_POOL_MAX", "10")),
        kwargs={"row_factory": dict_row},
        open=True,
    )


@contextmanager
def tenant_transaction(tenant_id: str) -> Iterator[Connection]:
    """Transacción con app.tenant_id fijado: todas las consultas quedan aisladas por RLS."""
    with get_pool().connection() as conn:
        with conn.transaction():
            # is_local=true: el valor solo vive durante esta transacción, así una
            # conexión reutilizada del pool nunca hereda el tenant anterior.
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            yield conn


# ===========================================================================
# API keys
# ===========================================================================
def hash_api_key(api_key: str) -> str:
    # Las claves son aleatorias de 256 bits, así que SHA-256 basta (no hace
    # falta un hash lento como bcrypt, que es para contraseñas elegidas por personas).
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def authenticate(api_key: str) -> TenantContext | None:
    """Valida una API key. Retorna el contexto del tenant o None si no es válida."""
    with get_pool().connection() as conn:
        row = conn.execute(
            "SELECT tenant_id, api_key_id, llm_habilitado FROM auth_api_key(%s)",
            (hash_api_key(api_key),),
        ).fetchone()
    if not row:
        return None
    return TenantContext(row["tenant_id"], row["api_key_id"], row["llm_habilitado"])


# ===========================================================================
# Auditoría
# ===========================================================================
def audit(conn: Connection, ctx: TenantContext, accion: str,
          recurso: str | None = None, detalle: dict | None = None) -> None:
    """Registra una acción. Nunca se guarda el texto de los contratos en la auditoría."""
    conn.execute(
        "INSERT INTO audit_log (tenant_id, api_key_id, accion, recurso, detalle) "
        "VALUES (%s, %s, %s, %s, %s)",
        (ctx.tenant_id, ctx.api_key_id, accion, recurso, Jsonb(detalle or {})),
    )


def log_action(ctx: TenantContext, accion: str, recurso: str | None = None,
               detalle: dict | None = None) -> None:
    """Auditoría en su propia transacción (para acciones que no guardan otros datos)."""
    with tenant_transaction(ctx.tenant_id) as conn:
        audit(conn, ctx, accion, recurso, detalle)


# ===========================================================================
# Contratos
# ===========================================================================
def save_contract(ctx: TenantContext, nombre_archivo: str, modo: str,
                  reporte: dict, clausulas: list[dict]) -> UUID:
    """
    Guarda un contrato analizado y sus cláusulas en una sola transacción.
    `reporte` es la salida de generate_json_report; `clausulas` son las cláusulas
    analizadas (con razon / patrones_activados del clasificador).
    """
    resumen = reporte["resumen"]
    niveles = {c["numero"]: c["nivel"] for c in reporte["clausulas"]}
    with tenant_transaction(ctx.tenant_id) as conn:
        contract_id = conn.execute(
            "INSERT INTO contracts (tenant_id, nombre_archivo, modo, total_clausulas, "
            "nivel_global, score_riesgo) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (ctx.tenant_id, nombre_archivo, modo, resumen["total_clausulas"],
             resumen["nivel_global"], resumen["score_riesgo"]),
        ).fetchone()["id"]

        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO clause_results (contract_id, tenant_id, numero, identificador, "
                "titulo, texto, es_riesgo, categoria, nivel, razon, patrones_activados) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (contract_id, ctx.tenant_id, c["numero"], c.get("identificador"),
                     c.get("titulo"), c["texto"], bool(c["es_riesgo"]), c.get("categoria"),
                     niveles[c["numero"]], c.get("razon"),
                     Jsonb(c["patrones_activados"]) if c.get("patrones_activados") is not None else None)
                    for c in clausulas
                ],
            )

        audit(conn, ctx, "analyze_contract", f"contract:{contract_id}",
              {"archivo": nombre_archivo, "modo": modo, "clausulas": len(clausulas),
               "nivel_global": resumen["nivel_global"]})
    return contract_id


def list_contracts(ctx: TenantContext, limit: int = 50, offset: int = 0) -> list[dict]:
    with tenant_transaction(ctx.tenant_id) as conn:
        # Sin WHERE tenant_id: el filtrado lo hace RLS (y así se prueba en los tests).
        return conn.execute(
            "SELECT id, nombre_archivo, modo, total_clausulas, nivel_global, score_riesgo, created_at "
            "FROM contracts ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()


def get_contract(ctx: TenantContext, contract_id: UUID) -> tuple[dict, list[dict]] | None:
    """Retorna (contrato, cláusulas) o None si no existe para este tenant."""
    with tenant_transaction(ctx.tenant_id) as conn:
        contrato = conn.execute(
            "SELECT id, nombre_archivo, modo, total_clausulas, nivel_global, score_riesgo, created_at "
            "FROM contracts WHERE id = %s",
            (contract_id,),
        ).fetchone()
        if not contrato:
            return None
        clausulas = conn.execute(
            "SELECT numero, identificador, titulo, texto, es_riesgo, categoria, razon, patrones_activados "
            "FROM clause_results WHERE contract_id = %s ORDER BY numero",
            (contract_id,),
        ).fetchall()
        audit(conn, ctx, "read_contract", f"contract:{contract_id}")
    return contrato, clausulas


def delete_contract(ctx: TenantContext, contract_id: UUID) -> bool:
    """Borra un contrato y sus cláusulas (procedimiento de borrado de datos)."""
    with tenant_transaction(ctx.tenant_id) as conn:
        borrado = conn.execute("DELETE FROM contracts WHERE id = %s", (contract_id,)).rowcount > 0
        if borrado:
            audit(conn, ctx, "delete_contract", f"contract:{contract_id}")
    return borrado


def list_audit(ctx: TenantContext, limit: int = 100) -> list[dict]:
    with tenant_transaction(ctx.tenant_id) as conn:
        return conn.execute(
            "SELECT id, accion, recurso, detalle, created_at FROM audit_log "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (limit,),
        ).fetchall()
