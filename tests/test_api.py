"""
Pruebas de integración de la API: autenticación, aislamiento entre
organizaciones (RLS), política de opt-in del LLM, auditoría y tiempos.
"""

from __future__ import annotations

import time

import psycopg
import pytest

URL_PDF = "/api/v1/analyze-contract-pdf"


def subir(client, headers, pdf, **params):
    return client.post(URL_PDF, headers=headers, params=params,
                       files={"file": ("contrato.pdf", pdf, "application/pdf")})


# ===========================================================================
# Autenticación
# ===========================================================================
def test_health_exige_tenant(client):
    assert client.get("/health").status_code == 400
    r = client.get("/health", headers={"X-Tenant-ID": "org-a"})
    assert r.json() == {"status": "ok", "tenant_id": "org-a"}


def test_sin_tenant_400(client, tenants):
    r = client.post("/api/v1/classify-clause", headers={"X-API-Key": tenants["org-a"]},
                    json={"texto": "x", "modo": "reglas"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Encabezado X-Tenant-ID es obligatorio"


def test_sin_api_key_401(client):
    r = client.get("/api/v1/contracts", headers={"X-Tenant-ID": "org-a"})
    assert r.status_code == 401


def test_api_key_invalida_401(client):
    r = client.get("/api/v1/contracts", headers={"X-Tenant-ID": "org-a", "X-API-Key": "crd_falsa"})
    assert r.status_code == 401


def test_api_key_de_otra_organizacion_403(client, tenants):
    # La clave de B no sirve para actuar como A, aunque se envíe X-Tenant-ID: org-a.
    r = client.get("/api/v1/contracts", headers={"X-Tenant-ID": "org-a", "X-API-Key": tenants["org-b"]})
    assert r.status_code == 403


def test_api_key_revocada_401(client, database_urls, headers):
    from db import generate_api_key, hash_api_key

    clave = generate_api_key()
    with psycopg.connect(database_urls["admin"]) as conn:
        conn.execute("INSERT INTO api_keys (tenant_id, key_hash, prefijo, revoked_at) "
                     "VALUES ('org-a', %s, %s, now())", (hash_api_key(clave), clave[:12]))
    r = client.get("/api/v1/contracts", headers={"X-Tenant-ID": "org-a", "X-API-Key": clave})
    assert r.status_code == 401


# ===========================================================================
# Clasificación y opt-in del LLM
# ===========================================================================
def test_clasificar_clausula_reglas(client, headers):
    r = client.post("/api/v1/classify-clause", headers=headers("org-a"),
                    json={"texto": "El contratista no podrá ceder el presente contrato", "modo": "reglas"})
    assert r.status_code == 200
    assert r.json()["es_riesgo"] is True and r.json()["tenant_id"] == "org-a"


def test_llm_sin_opt_in_403(client, headers, contrato_pdf):
    r = client.post("/api/v1/classify-clause", headers=headers("org-b"),
                    json={"texto": "cualquier cláusula", "modo": "llm"})
    assert r.status_code == 403
    assert subir(client, headers("org-b"), contrato_pdf, modo="llm").status_code == 403


def test_llm_con_opt_in(client, headers, monkeypatch):
    # Se reemplaza el LLM por uno falso para no depender de la red.
    import main

    class LLMFalso:
        def classify(self, texto):
            return {"es_riesgo": True, "categoria": "pago", "razon": "prueba"}

    monkeypatch.setattr(main, "get_llm_classifier", lambda: LLMFalso())
    r = client.post("/api/v1/classify-clause", headers=headers("org-a"),
                    json={"texto": "cualquier cláusula", "modo": "llm"})
    assert r.status_code == 200 and r.json()["razon"] == "prueba"


# ===========================================================================
# Contratos y aislamiento entre organizaciones
# ===========================================================================
def test_analizar_y_recuperar_contrato(client, headers, contrato_pdf):
    r = subir(client, headers("org-a"), contrato_pdf)
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["resumen"]["total_clausulas"] == 10
    cid = cuerpo["contract_id"]

    lista = client.get("/api/v1/contracts", headers=headers("org-a")).json()["contratos"]
    assert [c["id"] for c in lista] == [cid]

    detalle = client.get(f"/api/v1/contracts/{cid}", headers=headers("org-a")).json()
    assert detalle["resumen"] == cuerpo["resumen"]

    html = client.get(f"/api/v1/contracts/{cid}?format=html", headers=headers("org-a"))
    assert html.headers["content-type"].startswith("text/html")


def test_organizacion_b_no_ve_contratos_de_a(client, headers, contrato_pdf):
    cid = subir(client, headers("org-a"), contrato_pdf).json()["contract_id"]

    assert client.get("/api/v1/contracts", headers=headers("org-b")).json()["contratos"] == []
    assert client.get(f"/api/v1/contracts/{cid}", headers=headers("org-b")).status_code == 404
    assert client.delete(f"/api/v1/contracts/{cid}", headers=headers("org-b")).status_code == 404
    # El contrato de A sigue existiendo.
    assert client.get(f"/api/v1/contracts/{cid}", headers=headers("org-a")).status_code == 200


def test_borrar_contrato(client, headers, contrato_pdf):
    cid = subir(client, headers("org-a"), contrato_pdf).json()["contract_id"]
    assert client.delete(f"/api/v1/contracts/{cid}", headers=headers("org-a")).status_code == 204
    assert client.get(f"/api/v1/contracts/{cid}", headers=headers("org-a")).status_code == 404


def test_auditoria_por_organizacion(client, headers, contrato_pdf):
    subir(client, headers("org-a"), contrato_pdf)
    client.post("/api/v1/classify-clause", headers=headers("org-b"),
                json={"texto": "Cláusula de prueba sin riesgo alguno", "modo": "reglas"})

    eventos_a = client.get("/api/v1/audit-log", headers=headers("org-a")).json()["eventos"]
    eventos_b = client.get("/api/v1/audit-log", headers=headers("org-b")).json()["eventos"]
    assert [e["accion"] for e in eventos_a] == ["analyze_contract"]
    assert [e["accion"] for e in eventos_b] == ["classify_clause"]
    # La auditoría no guarda el texto de la cláusula.
    assert "texto" not in eventos_b[0]["detalle"]


# ===========================================================================
# RLS directamente en la base de datos (defensa en profundidad)
# ===========================================================================
def test_rls_sin_tenant_no_devuelve_filas(client, headers, contrato_pdf, database_urls):
    subir(client, headers("org-a"), contrato_pdf)
    with psycopg.connect(database_urls["app"]) as conn:
        # Sin app.tenant_id: ninguna fila, aunque la consulta no tenga WHERE.
        assert conn.execute("SELECT count(*) FROM contracts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM clause_results").fetchone()[0] == 0


def test_rls_tenant_b_no_ve_filas_de_a(client, headers, contrato_pdf, database_urls):
    subir(client, headers("org-a"), contrato_pdf)
    with psycopg.connect(database_urls["app"]) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'org-b', false)")
        assert conn.execute("SELECT count(*) FROM contracts").fetchone()[0] == 0
        conn.execute("SELECT set_config('app.tenant_id', 'org-a', false)")
        assert conn.execute("SELECT count(*) FROM contracts").fetchone()[0] == 1


def test_rls_impide_insertar_en_otro_tenant(database_urls, tenants):
    with psycopg.connect(database_urls["app"]) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'org-b', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO contracts (tenant_id, nombre_archivo, modo, total_clausulas, "
                         "nivel_global, score_riesgo) VALUES ('org-a', 'x.pdf', 'reglas', 1, 'verde', 0)")


def test_app_user_no_puede_leer_api_keys(database_urls, tenants):
    with psycopg.connect(database_urls["app"]) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'org-a', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM api_keys")


def test_app_user_no_puede_modificar_auditoria(client, headers, database_urls):
    client.post("/api/v1/classify-clause", headers=headers("org-a"),
                json={"texto": "Cláusula de prueba", "modo": "reglas"})
    with psycopg.connect(database_urls["app"]) as conn:
        conn.execute("SELECT set_config('app.tenant_id', 'org-a', false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM audit_log")


# ===========================================================================
# Tiempo de respuesta (meta del paper: < 3 s para un contrato de 20 cláusulas)
# ===========================================================================
def test_tiempo_respuesta_contrato_20_clausulas(client, headers):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    for i in range(1, 21):
        pdf.multi_cell(0, 5, f"CLÁUSULA {i}. El contratista acepta que el precio pactado es fijo e "
                             f"inamovible y asume la obligación número {i} del presente contrato.",
                       new_x="LMARGIN", new_y="NEXT")
    contenido = bytes(pdf.output())

    inicio = time.perf_counter()
    r = subir(client, headers("org-a"), contenido)
    duracion = time.perf_counter() - inicio

    assert r.status_code == 200
    assert r.json()["resumen"]["total_clausulas"] == 20
    assert duracion < 3.0, f"Tardó {duracion:.2f} s"
    assert "X-Response-Time-ms" in r.headers
