# Detector de Cláusulas de Riesgo en Contratos

Proyecto universitario (TDSE) para detectar cláusulas de riesgo en contratos de obra y
proveeduría en español, con dos motores de clasificación:

- **Reglas (regex)** — `rules_classifier.py`
- **LLM (OpenAI `gpt-4o-mini`)** — `llm_classifier.py`

Ambos se exponen mediante una API REST con FastAPI y multitenancy básico (`X-Tenant-ID`).

## Estructura

| Archivo | Descripción |
|---|---|
| `rules_classifier.py` | `RuleBasedClassifier`: regex por categoría (pago, tiempo, procedimiento, roles_responsabilidades) |
| `llm_classifier.py` | `LLMClassifier`: clasificación con gpt-4o-mini, salida JSON |
| `evaluate.py` | Evalúa el clasificador de reglas sobre `corpus.json` (Precisión, Recall, F1, recall por categoría) |
| `main.py` | API FastAPI |
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

```bash
export OPENAI_API_KEY=sk-...       # solo necesario para modo "llm"
uvicorn main:app --reload --port 8000
```

Documentación interactiva: http://localhost:8000/docs

Todas las rutas requieren el encabezado `X-Tenant-ID`; si falta se responde `400`.

```bash
curl -H "X-Tenant-ID: empresa-1" http://localhost:8000/health

curl -X POST http://localhost:8000/api/v1/classify-clause \
  -H "Content-Type: application/json" -H "X-Tenant-ID: empresa-1" \
  -d '{"texto": "El comprador retendrá el 50% del pago durante un año.", "modo": "reglas"}'
```

Respuesta (modo `reglas`):

```json
{"tenant_id": "empresa-1", "modo": "reglas", "es_riesgo": true, "categoria": "pago",
 "patrones_activados": ["pago:retendr[áa]"]}
```

### Análisis de un contrato completo (PDF)

```bash
# Reporte JSON
curl -X POST "http://localhost:8000/api/v1/analyze-contract-pdf?modo=reglas&format=json" \
  -H "X-Tenant-ID: empresa-1" -F "file=@samples/contrato_ejemplo.pdf"

# Dashboard HTML
curl -X POST "http://localhost:8000/api/v1/analyze-contract-pdf?modo=llm&format=html" \
  -H "X-Tenant-ID: empresa-1" -F "file=@samples/contrato_ejemplo.pdf" -o reporte.html
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

```bash
docker build -t contract-risk-detector .
docker run -p 8000:8000 -e OPENAI_API_KEY=sk-... contract-risk-detector
```

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
