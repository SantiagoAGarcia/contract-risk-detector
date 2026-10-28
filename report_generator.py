"""
report_generator.py
-------------------
Reporte tipo semáforo para contratos completos.

Niveles por cláusula:
- ROJO     (alto riesgo):    es_riesgo=True en categoría crítica
                             (pago, roles_responsabilidades, procedimiento).
- AMARILLO (riesgo moderado): es_riesgo=True en categoría menor (tiempo, seguridad,
                             definicion_referencia), o confianza limítrofe del modelo.
- VERDE    (normal):         es_riesgo=False.

Confianza limítrofe: si la cláusula analizada trae un campo opcional
"confianza" (0–1) dentro de [CONFIANZA_LIMITE_MIN, CONFIANZA_LIMITE_MAX], la
decisión se considera dudosa y la cláusula se marca AMARILLO. Los
clasificadores actuales no devuelven este campo; queda listo para cuando lo hagan.

Formatos:
- generate_json_report(analyzed_clauses) -> dict
- generate_html_report(analyzed_clauses, contract_name) -> str  (HTML autocontenido)

Cada elemento de analyzed_clauses es un dict con al menos:
    texto, es_riesgo, categoria
y opcionalmente: numero, identificador, titulo, razon, patrones_activados, confianza.
"""

from __future__ import annotations

from datetime import datetime

from jinja2 import Environment

ROJO, AMARILLO, VERDE = "rojo", "amarillo", "verde"

CATEGORIAS_CRITICAS = {"pago", "roles_responsabilidades", "procedimiento"}
CATEGORIAS_MENORES = {"tiempo", "seguridad", "definicion_referencia"}

CONFIANZA_LIMITE_MIN = 0.4
CONFIANZA_LIMITE_MAX = 0.6

# Peso de cada color en el score ponderado (0 = sin riesgo, 100 = todo rojo).
PESOS = {ROJO: 1.0, AMARILLO: 0.5, VERDE: 0.0}

ETIQUETAS = {
    ROJO: "Alto riesgo",
    AMARILLO: "Riesgo moderado",
    VERDE: "Normal",
}

NOMBRES_CATEGORIA = {
    "pago": "Pago",
    "tiempo": "Tiempo",
    "procedimiento": "Procedimiento",
    "roles_responsabilidades": "Roles y responsabilidades",
    "seguridad": "Seguridad",
    "definicion_referencia": "Definición y referencia",
}


# ===========================================================================
# Lógica del semáforo
# ===========================================================================
def nivel_semaforo(clausula: dict) -> tuple[str, str]:
    """Retorna (color, motivo) para una cláusula analizada."""
    confianza = clausula.get("confianza")
    limitrofe = (
        isinstance(confianza, (int, float))
        and CONFIANZA_LIMITE_MIN <= confianza <= CONFIANZA_LIMITE_MAX
    )

    if not clausula.get("es_riesgo"):
        if limitrofe:
            return AMARILLO, f"Clasificada como normal, pero con confianza limítrofe ({confianza:.2f})."
        return VERDE, "No se identificaron riesgos."

    categoria = clausula.get("categoria", "")
    nombre = NOMBRES_CATEGORIA.get(categoria, categoria)
    if limitrofe:
        return AMARILLO, f"Posible riesgo de {nombre.lower()} con confianza limítrofe ({confianza:.2f})."
    if categoria in CATEGORIAS_CRITICAS:
        return ROJO, f"Riesgo en categoría crítica: {nombre}."
    return AMARILLO, f"Riesgo en categoría menor: {nombre}."


def _explicacion(clausula: dict) -> str:
    """Texto explicativo según el clasificador que se usó."""
    if clausula.get("razon"):
        return clausula["razon"]
    patrones = clausula.get("patrones_activados") or []
    if patrones:
        # "categoria:patron" -> solo el patrón, más legible.
        return "Patrones detectados: " + "; ".join(p.split(":", 1)[-1] for p in patrones)
    return "Sin indicadores de riesgo."


def _clausulas_con_nivel(analyzed_clauses: list[dict]) -> list[dict]:
    resultado = []
    for i, c in enumerate(analyzed_clauses, start=1):
        color, motivo = nivel_semaforo(c)
        resultado.append({
            "numero": c.get("numero", i),
            "identificador": c.get("identificador") or str(i),
            "titulo": c.get("titulo"),
            "texto": c.get("texto", ""),
            "es_riesgo": bool(c.get("es_riesgo")),
            "categoria": c.get("categoria"),
            "categoria_nombre": NOMBRES_CATEGORIA.get(c.get("categoria"), c.get("categoria")),
            "nivel": color,
            "nivel_etiqueta": ETIQUETAS[color],
            "motivo": motivo,
            "explicacion": _explicacion(c),
            **({"confianza": c["confianza"]} if "confianza" in c else {}),
        })
    return resultado


# ===========================================================================
# Reporte JSON
# ===========================================================================
def generate_json_report(analyzed_clauses: list[dict]) -> dict:
    """
    Resumen estructurado del contrato:
      resumen.conteo           -> cláusulas por color
      resumen.porcentaje       -> % por color
      resumen.score_riesgo     -> % de cláusulas rojas + amarillas
      resumen.score_ponderado  -> 0–100 (rojo=1, amarillo=0.5, verde=0)
      resumen.nivel_global     -> rojo si hay alguna roja, amarillo si hay alguna amarilla, si no verde
      riesgos_por_categoria    -> conteo de cláusulas de riesgo por categoría
      clausulas                -> detalle por cláusula
    """
    clausulas = _clausulas_con_nivel(analyzed_clauses)
    total = len(clausulas)
    conteo = {color: sum(c["nivel"] == color for c in clausulas) for color in (ROJO, AMARILLO, VERDE)}

    def pct(n: float) -> float:
        return round(100 * n / total, 1) if total else 0.0

    if conteo[ROJO]:
        nivel_global = ROJO
    elif conteo[AMARILLO]:
        nivel_global = AMARILLO
    else:
        nivel_global = VERDE

    riesgos_por_categoria: dict[str, int] = {}
    for c in clausulas:
        if c["es_riesgo"]:
            riesgos_por_categoria[c["categoria"]] = riesgos_por_categoria.get(c["categoria"], 0) + 1

    return {
        "resumen": {
            "total_clausulas": total,
            "conteo": conteo,
            "porcentaje": {color: pct(n) for color, n in conteo.items()},
            "score_riesgo": pct(conteo[ROJO] + conteo[AMARILLO]),
            "score_ponderado": pct(sum(PESOS[c["nivel"]] for c in clausulas)),
            "nivel_global": nivel_global,
            "nivel_global_etiqueta": ETIQUETAS[nivel_global],
        },
        "riesgos_por_categoria": riesgos_por_categoria,
        "clausulas": clausulas,
    }


# ===========================================================================
# Reporte HTML
# ===========================================================================
_HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Semáforo de riesgo · {{ contract_name }}</title>
<style>
  :root {
    --bg: #f5f6f8; --surface: #ffffff; --text: #1d2330; --muted: #5d6676; --border: #e2e5ea;
    --rojo: #c62828; --rojo-bg: #fdecec; --amarillo: #a66a00; --amarillo-bg: #fff6dc;
    --verde: #2e7d32; --verde-bg: #e9f5ea; --accent: #2f5bd3;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #12151b; --surface: #1b2029; --text: #e6e9ef; --muted: #9aa3b2; --border: #2c3340;
      --rojo: #ff6b6b; --rojo-bg: #3a1f22; --amarillo: #f2c14e; --amarillo-bg: #3a3120;
      --verde: #6fcf74; --verde-bg: #1f3323; --accent: #7aa2ff;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: var(--bg); color: var(--text); line-height: 1.5; }
  .wrap { max-width: 1040px; margin: 0 auto; padding: 24px 16px 48px; }
  header h1 { margin: 0 0 4px; font-size: 1.5rem; }
  header p { margin: 0; color: var(--muted); font-size: .9rem; }
  .global { display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 6px 12px;
            border-radius: 999px; font-weight: 600; font-size: .9rem; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 24px 0; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px; }
  .card .label { color: var(--muted); font-size: .8rem; text-transform: uppercase; letter-spacing: .04em; }
  .card .value { font-size: 2rem; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .card .sub { color: var(--muted); font-size: .85rem; }
  .card.rojo { border-top: 4px solid var(--rojo); }
  .card.amarillo { border-top: 4px solid var(--amarillo); }
  .card.verde { border-top: 4px solid var(--verde); }
  .card.score { border-top: 4px solid var(--accent); }
  .bar { display: flex; height: 10px; border-radius: 999px; overflow: hidden; background: var(--border); margin-top: 10px; }
  .bar span { display: block; height: 100%; }
  .filters { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; }
  .filters button { border: 1px solid var(--border); background: var(--surface); color: var(--text);
                    padding: 6px 14px; border-radius: 999px; cursor: pointer; font: inherit; font-size: .85rem; }
  .filters button[aria-pressed="true"] { background: var(--text); color: var(--surface); border-color: var(--text); }
  details { background: var(--surface); border: 1px solid var(--border); border-left: 6px solid; border-radius: 10px;
            margin-bottom: 10px; }
  details.rojo { border-left-color: var(--rojo); }
  details.amarillo { border-left-color: var(--amarillo); }
  details.verde { border-left-color: var(--verde); }
  summary { cursor: pointer; list-style: none; padding: 12px 16px; display: flex; align-items: center; gap: 12px; }
  summary::-webkit-details-marker { display: none; }
  summary .id { font-weight: 600; white-space: nowrap; }
  summary .title { flex: 1; min-width: 0; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pill { font-size: .75rem; font-weight: 600; padding: 2px 10px; border-radius: 999px; white-space: nowrap; }
  .pill.rojo { color: var(--rojo); background: var(--rojo-bg); }
  .pill.amarillo { color: var(--amarillo); background: var(--amarillo-bg); }
  .pill.verde { color: var(--verde); background: var(--verde-bg); }
  .body { padding: 0 16px 16px; }
  .body blockquote { margin: 0 0 12px; padding: 10px 14px; background: var(--bg); border-radius: 8px; }
  .body dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; margin: 0; font-size: .9rem; }
  .body dt { color: var(--muted); }
  .body dd { margin: 0; }
  footer { margin-top: 32px; color: var(--muted); font-size: .8rem; }
  @media (max-width: 560px) { summary { flex-wrap: wrap; } summary .title { flex-basis: 100%; order: 3; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{{ contract_name }}</h1>
    <p>Reporte de riesgo contractual · {{ generado }} · {{ r.resumen.total_clausulas }} cláusulas analizadas</p>
    <div class="global pill {{ r.resumen.nivel_global }}">Nivel global: {{ r.resumen.nivel_global_etiqueta }}</div>
  </header>

  <section class="cards" aria-label="Resumen de alertas">
    <div class="card score">
      <div class="label">Score de riesgo</div>
      <div class="value">{{ r.resumen.score_riesgo }}%</div>
      <div class="sub">Cláusulas en rojo o amarillo · ponderado {{ r.resumen.score_ponderado }}</div>
      <div class="bar" role="img" aria-label="Distribución por color">
        <span style="width: {{ r.resumen.porcentaje.rojo }}%; background: var(--rojo)"></span>
        <span style="width: {{ r.resumen.porcentaje.amarillo }}%; background: var(--amarillo)"></span>
        <span style="width: {{ r.resumen.porcentaje.verde }}%; background: var(--verde)"></span>
      </div>
    </div>
    {% for color in ["rojo", "amarillo", "verde"] %}
    <div class="card {{ color }}">
      <div class="label">{{ etiquetas[color] }}</div>
      <div class="value">{{ r.resumen.conteo[color] }}</div>
      <div class="sub">{{ r.resumen.porcentaje[color] }}% del contrato</div>
    </div>
    {% endfor %}
  </section>

  <div class="filters" role="group" aria-label="Filtrar cláusulas">
    <button type="button" data-filter="todos" aria-pressed="true">Todas</button>
    {% for color in ["rojo", "amarillo", "verde"] %}
    <button type="button" data-filter="{{ color }}" aria-pressed="false">{{ etiquetas[color] }} ({{ r.resumen.conteo[color] }})</button>
    {% endfor %}
  </div>

  <section id="clausulas">
    {% for c in r.clausulas %}
    <details class="{{ c.nivel }}" data-nivel="{{ c.nivel }}" {% if c.nivel == "rojo" %}open{% endif %}>
      <summary>
        <span class="id">{{ c.identificador }}</span>
        <span class="title">{{ c.titulo or c.texto }}</span>
        <span class="pill {{ c.nivel }}">{{ c.nivel_etiqueta }}</span>
      </summary>
      <div class="body">
        <blockquote>{{ c.texto }}</blockquote>
        <dl>
          <dt>Evaluación</dt><dd>{{ c.motivo }}</dd>
          {% if c.es_riesgo %}<dt>Categoría</dt><dd>{{ c.categoria_nombre }}</dd>{% endif %}
          <dt>Explicación</dt><dd>{{ c.explicacion }}</dd>
          {% if c.confianza is defined %}<dt>Confianza</dt><dd>{{ "%.2f"|format(c.confianza) }}</dd>{% endif %}
        </dl>
      </div>
    </details>
    {% endfor %}
  </section>

  <footer>
    Este reporte es una primera capa de revisión automática y no reemplaza la revisión de un abogado.
  </footer>
</div>
<script>
  // Filtro por color (sin dependencias externas).
  document.querySelectorAll(".filters button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var filtro = btn.dataset.filter;
      document.querySelectorAll(".filters button").forEach(function (b) {
        b.setAttribute("aria-pressed", String(b === btn));
      });
      document.querySelectorAll("#clausulas details").forEach(function (d) {
        d.hidden = filtro !== "todos" && d.dataset.nivel !== filtro;
      });
    });
  });
</script>
</body>
</html>
"""

# autoescape=True: el texto de las cláusulas viene de un PDF subido por el usuario
# y no debe poder inyectar HTML/JS en el reporte.
_env = Environment(autoescape=True)
_template = _env.from_string(_HTML_TEMPLATE)


def generate_html_report(analyzed_clauses: list[dict], contract_name: str) -> str:
    """Genera un dashboard HTML autocontenido (sin recursos externos)."""
    return _template.render(
        contract_name=contract_name,
        r=generate_json_report(analyzed_clauses),
        etiquetas=ETIQUETAS,
        generado=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
