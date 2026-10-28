"""
experimentation_and_stats.py
----------------------------
Fase de experimentación: compara el clasificador de reglas (línea base) contra
el clasificador LLM sobre el mismo corpus anotado.

Pasos:
1. Carga corpus.json y ejecuta ambos clasificadores sobre todas las cláusulas.
2. Calcula Precisión, Recall, F1, matriz de confusión y recall por categoría.
3. Aplica la prueba de McNemar sobre los aciertos/fallos de ambos modelos.
4. Identifica los fallos (FP/FN) de cada modelo y clasifica los de las reglas en:
     a) "redaccion_distinta": la frase clave existe pero escrita de otra forma.
     b) "riesgo_semantico":   el riesgo está en el sentido, no en una palabra.
5. Exporta todo a resultados_evaluacion.json y completa ANALISIS_RESULTADOS.md
   en ANALISIS_RESULTADOS_completado.md.

Notas metodológicas:
- La tarea evaluada es binaria (¿es riesgo?). La clase positiva es "riesgo".
- Las reglas de la línea base son RISK_PATTERNS_BASE (sin afinar con el corpus).
  Las reglas afinadas se reportan solo como referencia, porque fueron ajustadas
  mirando este mismo corpus.
- Las respuestas del LLM se guardan en caché (llm_cache.json) para que los
  resultados sean reproducibles y no se pague/consuma cuota en cada ejecución.
- El modelo LLM se toma de LLM_MODEL (por defecto gpt-4o-mini). Las variables
  se pueden definir en un archivo .env.

Uso:
    python experimentation_and_stats.py                 # ejecución completa
    python experimentation_and_stats.py --sin-llm       # solo reglas (prueba rápida)
    python experimentation_and_stats.py --refrescar-llm # ignora la caché del LLM
    python experimentation_and_stats.py --corpus corpus_expanded_80.json \
        --salida resultados_evaluacion_80.json --informe ANALISIS_RESULTADOS_80.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score
from statsmodels.stats.contingency_tables import mcnemar

from llm_classifier import MODEL_NAME, SYSTEM_PROMPT, LLMClassifier, LLMClassifierError
from rules_classifier import RISK_PATTERNS, RISK_PATTERNS_BASE, RuleBasedClassifier

BASE_DIR = Path(__file__).parent
ALPHA = 0.05

CATEGORIAS = [
    "pago",
    "tiempo",
    "procedimiento",
    "roles_responsabilidades",
    "seguridad",
    "definicion_referencia",
]

# Etiquetas legibles para los tipos de fallo de las reglas.
TIPOS_FALLO = {
    "redaccion_distinta": "Redacción distinta a la esperada",
    "riesgo_semantico": "Riesgo en el sentido, no en una palabra",
}

# Clasificación manual de fallos de las reglas: {id_clausula: tipo}.
# Tiene prioridad sobre la heurística automática. Usarla cuando la revisión
# humana discrepe de la heurística (y reportarlo en el informe).
CLASIFICACION_MANUAL_FALLOS: dict[int, str] = {
    # 18: "riesgo_semantico",
}

# Palabras vacías que no se usan para buscar "casi coincidencias".
STOPWORDS = {"el", "la", "los", "las", "de", "del", "a", "al", "en", "por", "un", "una",
             "sin", "su", "con", "que", "cada", "presente"}


# ===========================================================================
# Utilidades
# ===========================================================================
def quitar_tildes(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar palabras de forma robusta."""
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def cargar_corpus(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        corpus = json.load(f)
    for i, item in enumerate(corpus):
        item.setdefault("id", i + 1)
        faltan = {"texto", "es_riesgo", "categoria"} - item.keys()
        if faltan:
            raise ValueError(f"Cláusula {item['id']} sin llaves requeridas: {faltan}")
    return corpus


def fmt(valor: float | None, decimales: int = 2) -> str:
    """Formatea métricas; None se muestra como N/A."""
    return "N/A" if valor is None else f"{valor:.{decimales}f}"


# ===========================================================================
# 1. Ejecución de clasificadores
# ===========================================================================
def ejecutar_reglas(corpus: list[dict], patrones: dict) -> list[dict]:
    clf = RuleBasedClassifier(patrones)
    return [clf.classify(item["texto"]) for item in corpus]


def _clave_cache(modelo: str, texto: str) -> str:
    """La clave depende del modelo, del prompt y del texto: si cambia algo, se recalcula."""
    h = hashlib.sha256(f"{modelo}\n{SYSTEM_PROMPT}\n{texto}".encode()).hexdigest()
    return h[:24]


def ejecutar_llm(corpus: list[dict], cache_path: Path, refrescar: bool) -> tuple[list[dict], str]:
    """Clasifica con el LLM usando caché en disco. Retorna (predicciones, modelo)."""
    clf = LLMClassifier(max_retries=6)
    cache: dict = {}
    if cache_path.exists() and not refrescar:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))

    predicciones = []
    for n, item in enumerate(corpus, start=1):
        clave = _clave_cache(clf.model, item["texto"])
        if clave in cache:
            predicciones.append(cache[clave])
            continue

        # Reintentos ante respuestas inválidas o errores transitorios.
        for intento in range(4):
            try:
                pred = clf.classify(item["texto"])
                break
            except LLMClassifierError as exc:
                if intento == 3:
                    raise SystemExit(
                        f"El LLM falló en la cláusula {item['id']}: {exc}\n"
                        "Las respuestas ya obtenidas quedaron en caché; vuelva a ejecutar."
                    )
                espera = 5 * 2**intento
                print(f"  [LLM] cláusula {item['id']}: {exc} — reintento en {espera}s")
                time.sleep(espera)

        cache[clave] = pred
        # Guardamos después de cada llamada para poder reanudar si algo falla.
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        predicciones.append(pred)
        print(f"  [LLM] {n}/{len(corpus)} clasificadas", end="\r")

    print(" " * 40, end="\r")
    return predicciones, clf.model


# ===========================================================================
# 2. Métricas
# ===========================================================================
def calcular_metricas(corpus: list[dict], preds: list[dict]) -> dict:
    y_true = [bool(c["es_riesgo"]) for c in corpus]
    y_pred = [bool(p["es_riesgo"]) for p in preds]
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[False, True]).ravel()

    # Recall por categoría: sobre las cláusulas de riesgo de cada categoría.
    # det = detectada como riesgo; cat = detectada y con la categoría correcta.
    por_categoria = {}
    for cat in CATEGORIAS:
        idx = [i for i, c in enumerate(corpus) if c["es_riesgo"] and c["categoria"] == cat]
        if not idx:
            por_categoria[cat] = {"n_riesgo": 0, "recall": None, "recall_con_categoria": None}
            continue
        det = sum(y_pred[i] for i in idx)
        det_cat = sum(y_pred[i] and preds[i]["categoria"] == cat for i in idx)
        por_categoria[cat] = {
            "n_riesgo": len(idx),
            "detectadas": int(det),
            "recall": det / len(idx),
            "recall_con_categoria": det_cat / len(idx),
        }

    return {
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "exactitud": float((tp + tn) / len(corpus)),
        "confusion": {"VP": int(tp), "FP": int(fp), "VN": int(tn), "FN": int(fn)},
        "recall_por_categoria": por_categoria,
    }


def tabla_metricas(resultados: dict[str, dict]) -> str:
    filas = [
        {
            "Método": nombre,
            "Precisión": fmt(m["precision"]),
            "Recall": fmt(m["recall"]),
            "F1-Score": fmt(m["f1"]),
            "Exactitud": fmt(m["exactitud"]),
        }
        for nombre, m in resultados.items()
    ]
    return pd.DataFrame(filas).to_markdown(index=False, disable_numparse=True)


def tabla_confusion(resultados: dict[str, dict]) -> str:
    filas = [{"Método": nombre, **m["confusion"]} for nombre, m in resultados.items()]
    return pd.DataFrame(filas).to_markdown(index=False, disable_numparse=True)


def tabla_recall_categoria(resultados: dict[str, dict]) -> str:
    filas = []
    for cat in CATEGORIAS:
        fila = {"Categoría": cat}
        for nombre, m in resultados.items():
            info = m["recall_por_categoria"][cat]
            fila["N riesgo"] = info["n_riesgo"]
            fila[f"Recall {nombre}"] = (
                "N/A (sin riesgo)" if info["recall"] is None else
                f"{fmt(info['recall'])} ({info['detectadas']}/{info['n_riesgo']})"
            )
        filas.append(fila)
    return pd.DataFrame(filas).to_markdown(index=False, disable_numparse=True)


# ===========================================================================
# 3. Prueba de McNemar
# ===========================================================================
def prueba_mcnemar(corpus: list[dict], preds_a: list[dict], preds_b: list[dict],
                   nombre_a: str, nombre_b: str) -> dict:
    """
    Compara aciertos/fallos de dos clasificadores sobre las mismas cláusulas.
      a: ambos aciertan      b: A acierta y B falla
      c: A falla y B acierta d: ambos fallan
    Solo b y c (los casos discordantes) aportan evidencia.
    """
    acierto_a = [bool(p["es_riesgo"]) == bool(c["es_riesgo"]) for p, c in zip(preds_a, corpus)]
    acierto_b = [bool(p["es_riesgo"]) == bool(c["es_riesgo"]) for p, c in zip(preds_b, corpus)]

    a = sum(x and y for x, y in zip(acierto_a, acierto_b))
    b = sum(x and not y for x, y in zip(acierto_a, acierto_b))
    c = sum(not x and y for x, y in zip(acierto_a, acierto_b))
    d = sum(not x and not y for x, y in zip(acierto_a, acierto_b))

    # Con pocos casos discordantes (b + c < 25) se usa la prueba binomial exacta;
    # si no, chi-cuadrado con corrección de continuidad de Edwards.
    exacta = (b + c) < 25
    res = mcnemar([[a, b], [c, d]], exact=exacta, correction=True)
    p = float(res.pvalue)
    estadistico = float(res.statistic)

    if b + c == 0:
        conclusion = (
            f"No hay casos discordantes (b = c = 0): {nombre_a} y {nombre_b} aciertan y fallan "
            "exactamente en las mismas cláusulas, por lo que no hay evidencia de diferencia."
        )
    elif p < ALPHA:
        mejor = nombre_b if c > b else nombre_a
        conclusion = (
            f"p = {p:.4f} < α = {ALPHA}: se rechaza H0. Existe una diferencia estadísticamente "
            f"significativa entre {nombre_a} y {nombre_b}; {mejor} comete menos errores."
        )
    else:
        conclusion = (
            f"p = {p:.4f} ≥ α = {ALPHA}: no se rechaza H0. No hay evidencia estadísticamente "
            f"significativa de que {nombre_a} y {nombre_b} difieran en su tasa de error "
            f"(con n = {len(corpus)} cláusulas)."
        )

    return {
        "modelo_a": nombre_a,
        "modelo_b": nombre_b,
        "tabla": {"a": a, "b": b, "c": c, "d": d},
        "metodo": "binomial exacta" if exacta else "chi-cuadrado con corrección de continuidad",
        "estadistico": estadistico,
        "p_valor": p,
        "alpha": ALPHA,
        "significativo": bool(b + c > 0 and p < ALPHA),
        "conclusion": conclusion,
    }


def imprimir_mcnemar(mc: dict) -> None:
    t = mc["tabla"]
    tabla = pd.DataFrame(
        [[t["a"], t["b"]], [t["c"], t["d"]]],
        index=[f"{mc['modelo_a']} acierta", f"{mc['modelo_a']} falla"],
        columns=[f"{mc['modelo_b']} acierta", f"{mc['modelo_b']} falla"],
    )
    print(tabla.to_markdown())
    print()
    print(f"Método: {mc['metodo']}  |  estadístico = {mc['estadistico']:.4f}  |  "
          f"p-valor = {mc['p_valor']:.4f}")
    print(f"Conclusión: {mc['conclusion']}")


# ===========================================================================
# 4. Identificación y clasificación de fallos
# ===========================================================================
def raices_de_patron(patron: str) -> list[str]:
    """
    Extrae las palabras significativas de un regex y devuelve su raíz
    (5 primeras letras, sin tildes). Ej.: "terminaci[óo]n unilateral" -> ["termi", "unila"].
    """
    texto = re.sub(r"\[([^\]]+)\]", lambda m: m.group(1)[0], patron)   # [óo] -> ó
    texto = re.sub(r"\\s[+*]?|\.\*|\.\{\d*,?\d*\}|[\\()?+*|^$.]", " ", texto)
    palabras = [quitar_tildes(p) for p in texto.split()]
    return [p[:5] for p in palabras if p not in STOPWORDS and len(p) >= 4]


def clasificar_fallo_reglas(texto: str, patrones: dict) -> tuple[str, list[str]]:
    """
    Heurística para un falso negativo de las reglas:
    - Si al menos la mitad de las palabras clave de algún patrón (de 2+ palabras)
      aparece en la cláusula, la frase existe pero con otra redacción
      -> "redaccion_distinta".
    - Si no aparece ninguna, el riesgo no está en las palabras clave
      -> "riesgo_semantico".
    Retorna (tipo, evidencias).
    """
    palabras = {quitar_tildes(p) for p in re.findall(r"\w+", texto)}
    evidencias = []
    for categoria, lista in patrones.items():
        for patron in lista:
            raices = raices_de_patron(patron)
            if len(raices) < 2:
                continue
            presentes = [r for r in raices if any(p.startswith(r) for p in palabras)]
            if len(presentes) >= math.ceil(len(raices) / 2):
                evidencias.append(f"{categoria}:{patron} (coinciden: {', '.join(presentes)})")
    return ("redaccion_distinta" if evidencias else "riesgo_semantico"), evidencias


def identificar_fallos(corpus: list[dict], preds: list[dict]) -> dict:
    """Separa falsos positivos, falsos negativos y errores de categoría."""
    fp, fn, cat = [], [], []
    for item, pred in zip(corpus, preds):
        registro = {
            "id": item["id"],
            "texto": item["texto"],
            "real": {"es_riesgo": item["es_riesgo"], "categoria": item["categoria"]},
            "prediccion": pred,
        }
        if pred["es_riesgo"] and not item["es_riesgo"]:
            fp.append(registro)
        elif not pred["es_riesgo"] and item["es_riesgo"]:
            fn.append(registro)
        elif item["es_riesgo"] and pred["categoria"] != item["categoria"]:
            # Detectó el riesgo pero lo puso en otra categoría (no cuenta como FP/FN).
            cat.append(registro)
    return {"falsos_positivos": fp, "falsos_negativos": fn, "categoria_incorrecta": cat}


def cualificar_fallos_reglas(fallos: dict, patrones: dict, preds_llm: list[dict] | None,
                             corpus: list[dict]) -> list[dict]:
    """Clasifica cada fallo binario de las reglas y anota si el LLM lo resolvió."""
    por_id = {c["id"]: i for i, c in enumerate(corpus)}
    cualificados = []
    for registro in fallos["falsos_negativos"] + fallos["falsos_positivos"]:
        if registro in fallos["falsos_negativos"]:
            tipo, evidencias = clasificar_fallo_reglas(registro["texto"], patrones)
            tipo_error = "FN"
        else:
            # Un FP de reglas es una palabra clave presente sin riesgo real: el
            # error está en no interpretar el sentido.
            tipo, evidencias = "riesgo_semantico", registro["prediccion"]["patrones_activados"]
            tipo_error = "FP"
        origen = "heuristica"
        if registro["id"] in CLASIFICACION_MANUAL_FALLOS:
            tipo, origen = CLASIFICACION_MANUAL_FALLOS[registro["id"]], "manual"

        resuelto = None
        if preds_llm is not None:
            pred_llm = preds_llm[por_id[registro["id"]]]
            resuelto = bool(pred_llm["es_riesgo"]) == bool(registro["real"]["es_riesgo"])

        cualificados.append({
            **registro,
            "tipo_error": tipo_error,
            "tipo_fallo": tipo,
            "tipo_fallo_desc": TIPOS_FALLO[tipo],
            "clasificacion_origen": origen,
            "evidencia": evidencias,
            "llm_resuelve": resuelto,
            "llm_razon": None if preds_llm is None else preds_llm[por_id[registro["id"]]].get("razon"),
        })
    return sorted(cualificados, key=lambda r: r["id"])


def tabla_fallos_reglas(cualificados: list[dict]) -> str:
    if not cualificados:
        return "_Las reglas no tuvieron fallos de detección._"
    filas = [{
        "ID": r["id"],
        "Error": r["tipo_error"],
        "Categoría real": r["real"]["categoria"],
        "Tipo de fallo": r["tipo_fallo_desc"],
        "¿LLM lo resuelve?": {True: "Sí", False: "No", None: "N/A"}[r["llm_resuelve"]],
        "Cláusula": r["texto"][:90] + ("…" if len(r["texto"]) > 90 else ""),
    } for r in cualificados]
    return pd.DataFrame(filas).to_markdown(index=False, disable_numparse=True)


def tabla_fallos_llm(fallos: dict) -> str:
    registros = [("FN", r) for r in fallos["falsos_negativos"]] + \
                [("FP", r) for r in fallos["falsos_positivos"]]
    if not registros:
        return "_El LLM no tuvo fallos de detección._"
    filas = [{
        "ID": r["id"],
        "Error": tipo,
        "Categoría real": r["real"]["categoria"],
        "Razón del LLM": (r["prediccion"].get("razon") or "")[:120],
    } for tipo, r in sorted(registros, key=lambda x: x[1]["id"])]
    return pd.DataFrame(filas).to_markdown(index=False, disable_numparse=True)


# ===========================================================================
# 5. Informe Markdown
# ===========================================================================
def completar_plantilla(plantilla: Path, destino: Path, valores: dict[str, str]) -> None:
    """Reemplaza los marcadores {{CLAVE}} de la plantilla por los valores calculados."""
    texto = plantilla.read_text(encoding="utf-8")
    for clave, valor in valores.items():
        texto = texto.replace("{{" + clave + "}}", str(valor))
    destino.write_text(texto, encoding="utf-8")


# ===========================================================================
# Programa principal
# ===========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", type=Path, default=BASE_DIR / "corpus.json")
    parser.add_argument("--salida", type=Path, default=BASE_DIR / "resultados_evaluacion.json")
    parser.add_argument("--informe", type=Path, default=BASE_DIR / "ANALISIS_RESULTADOS_completado.md",
                        help="Destino del informe Markdown completado")
    parser.add_argument("--sin-llm", action="store_true", help="No ejecuta el LLM")
    parser.add_argument("--refrescar-llm", action="store_true", help="Ignora llm_cache.json")
    args = parser.parse_args()

    load_dotenv(BASE_DIR / ".env")
    corpus = cargar_corpus(args.corpus)
    n_riesgo = sum(bool(c["es_riesgo"]) for c in corpus)
    print(f"Corpus: {len(corpus)} cláusulas ({n_riesgo} de riesgo, {len(corpus) - n_riesgo} sin riesgo)\n")

    # --- 1. Ejecución -------------------------------------------------------
    NOMBRE_REGLAS, NOMBRE_AFINADAS = "Reglas (línea base)", "Reglas afinadas*"
    preds = {
        NOMBRE_REGLAS: ejecutar_reglas(corpus, RISK_PATTERNS_BASE),
        NOMBRE_AFINADAS: ejecutar_reglas(corpus, RISK_PATTERNS),
    }
    modelo_llm = None
    nombre_llm = None
    if not args.sin_llm:
        print("Ejecutando clasificador LLM...")
        preds_llm, modelo_llm = ejecutar_llm(corpus, BASE_DIR / "llm_cache.json", args.refrescar_llm)
        nombre_llm = f"LLM ({modelo_llm})"
        preds[nombre_llm] = preds_llm

    # --- 2. Métricas -------------------------------------------------------
    metricas = {nombre: calcular_metricas(corpus, p) for nombre, p in preds.items()}
    t_metricas = tabla_metricas(metricas)
    t_confusion = tabla_confusion(metricas)
    t_categoria = tabla_recall_categoria(metricas)

    print("## Métricas de detección de riesgo\n")
    print(t_metricas)
    print("\n\\* Afinadas mirando este mismo corpus (sobreajuste): solo referencia.\n")
    print("## Matriz de confusión\n")
    print(t_confusion, "\n")
    print("## Recall por categoría\n")
    print(t_categoria, "\n")

    # --- 3. McNemar ---------------------------------------------------------
    mc = None
    if nombre_llm:
        print(f"## Prueba de McNemar: {NOMBRE_REGLAS} vs {nombre_llm}\n")
        mc = prueba_mcnemar(corpus, preds[NOMBRE_REGLAS], preds[nombre_llm], "Reglas", "LLM")
        imprimir_mcnemar(mc)
        print()

    # --- 4. Fallos ---------------------------------------------------------
    fallos = {nombre: identificar_fallos(corpus, p) for nombre, p in preds.items()}
    cualif_reglas = cualificar_fallos_reglas(
        fallos[NOMBRE_REGLAS], RISK_PATTERNS_BASE, preds.get(nombre_llm), corpus
    )
    n_red = sum(r["tipo_fallo"] == "redaccion_distinta" for r in cualif_reglas)
    n_sem = sum(r["tipo_fallo"] == "riesgo_semantico" for r in cualif_reglas)

    print(f"## Fallos de las reglas (línea base): {len(cualif_reglas)} "
          f"({n_red} redacción distinta, {n_sem} riesgo semántico)\n")
    print(tabla_fallos_reglas(cualif_reglas), "\n")
    if nombre_llm:
        n_fallos_llm = len(fallos[nombre_llm]["falsos_negativos"]) + len(fallos[nombre_llm]["falsos_positivos"])
        print(f"## Fallos del LLM: {n_fallos_llm}\n")
        print(tabla_fallos_llm(fallos[nombre_llm]), "\n")

    # --- 5. Exportación ----------------------------------------------------
    resultados = {
        "fecha": date.today().isoformat(),
        "corpus": {"archivo": args.corpus.name, "n": len(corpus), "n_riesgo": n_riesgo},
        "modelo_llm": modelo_llm,
        "metricas": metricas,
        "mcnemar": mc,
        "fallos": fallos,
        "fallos_reglas_cualificados": cualif_reglas,
        "predicciones": [
            {"id": c["id"], "texto": c["texto"],
             "real": {"es_riesgo": c["es_riesgo"], "categoria": c["categoria"]},
             **{nombre: p[i] for nombre, p in preds.items()}}
            for i, c in enumerate(corpus)
        ],
    }
    args.salida.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultados exportados a {args.salida.name}")

    plantilla = BASE_DIR / "ANALISIS_RESULTADOS.md"
    if plantilla.exists():
        destino = args.informe
        m_base = metricas[NOMBRE_REGLAS]
        m_llm = metricas.get(nombre_llm, {}) if nombre_llm else {}
        t = mc["tabla"] if mc else {}
        completar_plantilla(plantilla, destino, {
            "FECHA": date.today().isoformat(),
            "MODELO_LLM": modelo_llm or f"{MODEL_NAME} (no ejecutado)",
            "N_CLAUSULAS": len(corpus),
            "N_RIESGO": n_riesgo,
            "N_NORMALES": len(corpus) - n_riesgo,
            "TABLA_METRICAS": t_metricas,
            "TABLA_CONFUSION": t_confusion,
            "TABLA_RECALL_CATEGORIA": t_categoria,
            "REGLAS_RECALL": fmt(m_base["recall"]),
            "REGLAS_F1": fmt(m_base["f1"]),
            "LLM_RECALL": fmt(m_llm.get("recall")),
            "LLM_F1": fmt(m_llm.get("f1")),
            "MC_A": t.get("a", "N/A"),
            "MC_B": t.get("b", "N/A"),
            "MC_C": t.get("c", "N/A"),
            "MC_D": t.get("d", "N/A"),
            "MC_METODO": mc["metodo"] if mc else "N/A",
            "MC_ESTADISTICO": f"{mc['estadistico']:.4f}" if mc else "N/A",
            "MC_PVALOR": f"{mc['p_valor']:.4f}" if mc else "N/A",
            "MC_CONCLUSION": mc["conclusion"] if mc else "N/A",
            "N_FALLOS_REGLAS": len(cualif_reglas),
            "N_REDACCION": n_red,
            "N_SEMANTICO": n_sem,
            "N_RESUELTOS_LLM": sum(bool(r["llm_resuelve"]) for r in cualif_reglas),
            "TABLA_FALLOS_REGLAS": tabla_fallos_reglas(cualif_reglas),
            "TABLA_FALLOS_LLM": tabla_fallos_llm(fallos[nombre_llm]) if nombre_llm else "N/A",
        })
        print(f"Informe completado en {destino.name}")


if __name__ == "__main__":
    main()
