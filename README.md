# Detector de Cláusulas de Riesgo en Contratos

Proyecto universitario (TDSE) para detectar cláusulas de riesgo en contratos de obra y
proveeduría en español, con dos motores de clasificación:

- **Reglas (regex)** — `rules_classifier.py`
- **LLM (OpenAI `gpt-4o-mini`)** — `llm_classifier.py`

Ambos se exponen mediante una API REST con FastAPI, multi-tenant: cada organización se autentica con
una API key y sus datos quedan aislados en PostgreSQL con Row Level Security.

## Estructura

| Archivo | Descripción |
|---|---|
| `rules_classifier.py` | `RuleBasedClassifier`: regex por categoría (pago, tiempo, procedimiento, roles_responsabilidades) |
| `llm_classifier.py` | `LLMClassifier`: clasificación con gpt-4o-mini, salida JSON |
| `evaluate.py` | Evalúa el clasificador de reglas sobre `corpus.json` (Precisión, Recall, F1, recall por categoría) |
| `main.py` | API FastAPI |
| `db.py` | Acceso a PostgreSQL con aislamiento por tenant (RLS) y auditoría |
| `db/schema.sql` | Tablas, políticas RLS, función de autenticación y permisos |
| `manage.py` | Administración: esquema, organizaciones y API keys |
| `docker-compose.yml` | Entorno local: PostgreSQL + migración + API |
| `tests/` | Pruebas de integración (autenticación, aislamiento, auditoría, tiempos) |
| `pdf_processor.py` | Extracción de texto de PDF (`pypdf`) y segmentación en cláusulas |
| `report_generator.py` | Reporte semáforo (rojo / amarillo / verde) en JSON y HTML |
| `experimentation_and_stats.py` | Comparación reglas vs. LLM, McNemar y análisis de errores |
| `expand_corpus.py` | Genera `corpus_expanded_80.json` (40 originales + 40 nuevas) |
| `corpus.json` | 40 cláusulas anotadas (`texto`, `es_riesgo`, `categoria`) |
| `samples/contrato_ejemplo.pdf` | Contrato de ejemplo para probar el análisis de PDF |

## Instalación local

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Evaluación

```bash
python evaluate.py
```

> Nota: los patrones regex se diseñaron a partir del mismo corpus con el que se evalúan,
> por lo que las métricas sobre `corpus.json` son optimistas. Para una estimación real se
> recomienda evaluar con cláusulas nuevas no vistas.

## API

### Levantar el entorno

```bash
docker compose up --build -d                      # PostgreSQL + esquema + API en :8000
docker compose exec api python manage.py seed-demo  # crea 2 organizaciones y muestra sus API keys
```

`seed-demo` crea `empresa-demo-1` (con LLM habilitado) y `empresa-demo-2` (sin LLM). Las claves solo
se muestran una vez. Si el puerto 8000 está ocupado: `API_PORT=8010 docker compose up -d`.

Documentación interactiva: http://localhost:8000/docs

### Autenticación y aislamiento

| Encabezado | Uso | Error |
|---|---|---|
| `X-Tenant-ID` | Organización (obligatorio en todas las rutas) | `400` si falta |
| `X-API-Key` | Clave de la organización (todas las rutas salvo `/health`) | `401` si falta o es inválida, `403` si es de otra organización |

- Las API keys se guardan como hash SHA-256; la clave en claro solo se muestra al crearla.
- La API se conecta con el rol `app_user` (sin superusuario ni `BYPASSRLS`). Cada transacción fija
  `app.tenant_id` y las políticas RLS filtran todas las tablas por ese valor: aunque una consulta
  olvide filtrar por organización, la base de datos no devuelve datos de otra.
- **Política de datos:** el modo `llm` envía el texto a un tercero, por eso solo funciona para
  organizaciones con opt-in (`403` si no lo tienen). Se activa con `manage.py set-llm <tenant> on`.
- Cada análisis queda en el registro de auditoría (sin el texto de las cláusulas).

```bash
H='-H X-Tenant-ID:empresa-demo-1 -H X-API-Key:crd_...'

curl -X POST http://localhost:8000/api/v1/classify-clause $H -H "Content-Type: application/json" \
  -d '{"texto": "El comprador retendrá el 50% del pago durante un año.", "modo": "reglas"}'
```

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/health` | Estado del servicio |
| POST | `/api/v1/classify-clause` | Clasifica una cláusula |
| POST | `/api/v1/analyze-contract-pdf` | Analiza un PDF, lo guarda y devuelve el semáforo |
| GET | `/api/v1/contracts` | Contratos de la organización |
| GET | `/api/v1/contracts/{id}` | Reporte de un contrato (`?format=html` para el dashboard) |
| DELETE | `/api/v1/contracts/{id}` | Borra un contrato y sus resultados |
| GET | `/api/v1/audit-log` | Registro de auditoría de la organización |

Todas las respuestas incluyen `X-Response-Time-ms`.

### Administración

```bash
docker compose exec api python manage.py create-tenant empresa-3 "Empresa Tres" [--llm]
docker compose exec api python manage.py create-key empresa-3
docker compose exec api python manage.py revoke-key crd_XXXXXXXX
docker compose exec api python manage.py list-tenants
```

### Pruebas

```bash
pip install -r requirements-dev.txt
docker compose up -d db
TEST_ADMIN_DATABASE_URL=postgresql://admin:admin_dev@localhost:5432/postgres pytest
```

Usan una base de datos aparte (`contracts_test`). Cubren autenticación, aislamiento entre
organizaciones (vía API y directamente contra RLS), opt-in del LLM, auditoría y la meta de
tiempo de respuesta (< 3 s para un contrato de 20 cláusulas).

Respuesta (modo `reglas`):

```json
{"tenant_id": "empresa-1", "modo": "reglas", "es_riesgo": true, "categoria": "pago",
 "patrones_activados": ["pago:retendr[áa]"]}
```

### Análisis de un contrato completo (PDF)

```bash
# Reporte JSON
curl -X POST "http://localhost:8000/api/v1/analyze-contract-pdf?modo=reglas&format=json" \
  $H -F "file=@samples/contrato_ejemplo.pdf"

# Dashboard HTML
curl -X POST "http://localhost:8000/api/v1/analyze-contract-pdf?modo=llm&format=html" \
  $H -F "file=@samples/contrato_ejemplo.pdf" -o reporte.html
```

El PDF se segmenta por encabezados ("CLÁUSULA PRIMERA", "DÉCIMA SEGUNDA", "1.", "1.1"...) o,
si no los hay, por párrafos. Cada cláusula recibe un nivel de semáforo:

| Nivel | Criterio |
|---|---|
| 🔴 Rojo | Riesgo en pago, roles_responsabilidades o procedimiento |
| 🟡 Amarillo | Riesgo en tiempo, seguridad o definicion_referencia, o confianza limítrofe (0.4–0.6) si el clasificador la reporta |
| 🟢 Verde | Sin riesgo |

Límites: 10 MB por PDF, 300 cláusulas. Los PDF escaneados (sin texto) no se soportan porque requieren OCR.

En modo `llm` la respuesta incluye `razon` en lugar de `patrones_activados`. Si no hay
`OPENAI_API_KEY` configurada, el modo `llm` responde `503`.

### Usar un proveedor LLM gratuito

El modo `llm` funciona con cualquier API compatible con OpenAI, configurando:

| Variable | Descripción |
|---|---|
| `LLM_API_KEY` | Clave del proveedor (si no se define, se usa `OPENAI_API_KEY`) |
| `LLM_BASE_URL` | URL base del proveedor (vacío = OpenAI) |
| `LLM_MODEL` | Modelo (vacío = `gpt-4o-mini`) |

Ejemplos:

```bash
# Groq (capa gratuita): https://console.groq.com/keys
export LLM_BASE_URL=https://api.groq.com/openai/v1
export LLM_MODEL=openai/gpt-oss-120b
export LLM_API_KEY=gsk_...

# Google Gemini (capa gratuita): https://aistudio.google.com/apikey
export LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
export LLM_MODEL=gemini-2.5-flash
export LLM_API_KEY=...

# Ollama local (100% gratis, sin internet): https://ollama.com
export LLM_BASE_URL=http://localhost:11434/v1
export LLM_MODEL=qwen2.5:7b
export LLM_API_KEY=ollama   # cualquier valor no vacío
```

Los nombres de modelos cambian con el tiempo; revisar la lista vigente en cada proveedor.

## Docker

Usar `docker compose` (sección API): la imagen sola necesita una base de datos PostgreSQL
configurada en `DATABASE_URL`.

## Experimentación (reglas vs. LLM)

```bash
python experimentation_and_stats.py            # usa .env para el LLM
python experimentation_and_stats.py --sin-llm  # solo reglas
```

Calcula las métricas de ambos métodos, la prueba de McNemar y la clasificación de errores. Exporta
`resultados_evaluacion.json` y completa la plantilla `ANALISIS_RESULTADOS.md` en
`ANALISIS_RESULTADOS_completado.md`. Las respuestas del LLM se guardan en `llm_cache.json` para que
los resultados sean reproducibles (`--refrescar-llm` las vuelve a pedir).

Para el corpus ampliado:

```bash
python expand_corpus.py   # genera corpus_expanded_80.json
python experimentation_and_stats.py --corpus corpus_expanded_80.json \
    --salida resultados_evaluacion_80.json --informe ANALISIS_RESULTADOS_80.md
```

El experimento usa los patrones de **línea base** (`RISK_PATTERNS_BASE`). Los patrones afinados
(`RISK_PATTERNS`, usados por la API) se ajustaron mirando el corpus y solo se reportan como referencia.
