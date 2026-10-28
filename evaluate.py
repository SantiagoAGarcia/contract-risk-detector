"""
evaluate.py
-----------
Evalúa el RuleBasedClassifier sobre el corpus anotado (corpus.json).

Métricas reportadas:
- Precisión, Recall y F1 global para la detección binaria de riesgo (es_riesgo).
- Recall por categoría: sobre las cláusulas riesgosas de cada categoría, qué
  fracción fue detectada como riesgo y qué fracción además recibió la
  categoría correcta.

Uso:
    python evaluate.py                 # reglas afinadas, ./corpus.json
    python evaluate.py --base          # reglas de la línea base (paper)
    python evaluate.py ruta/corpus.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score

from rules_classifier import RISK_PATTERNS_BASE, RuleBasedClassifier


def load_corpus(path: Path) -> list[dict]:
    """Carga el corpus y valida que cada registro tenga las llaves esperadas."""
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    for i, item in enumerate(data):
        missing = {"texto", "es_riesgo", "categoria"} - item.keys()
        if missing:
            raise ValueError(f"Registro {i} sin llaves requeridas: {missing}")
    return data


def evaluate(corpus: list[dict], clf: RuleBasedClassifier) -> None:
    y_true, y_pred = [], []
    # categoria -> [total_riesgosas, detectadas, detectadas_con_categoria_correcta]
    por_categoria: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    errores = []

    for item in corpus:
        pred = clf.classify(item["texto"])
        y_true.append(bool(item["es_riesgo"]))
        y_pred.append(pred["es_riesgo"])

        if item["es_riesgo"]:
            stats = por_categoria[item["categoria"]]
            stats[0] += 1
            if pred["es_riesgo"]:
                stats[1] += 1
                if pred["categoria"] == item["categoria"]:
                    stats[2] += 1

        if pred["es_riesgo"] != item["es_riesgo"] or (
            item["es_riesgo"] and pred["categoria"] != item["categoria"]
        ):
            errores.append((item, pred))

    # --- Métricas globales (clase positiva = riesgo) -----------------------
    precision = precision_score(y_true, y_pred, zero_division=0)
    recall = recall_score(y_true, y_pred, zero_division=0)
    f1 = f1_score(y_true, y_pred, zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[False, True]).ravel()

    linea = "=" * 62
    print(linea)
    print(f" Evaluación del clasificador de reglas  ({len(corpus)} cláusulas)")
    print(linea)
    print(f" {'Métrica':<28}{'Valor':>10}")
    print("-" * 62)
    print(f" {'Precisión':<28}{precision:>10.3f}")
    print(f" {'Recall':<28}{recall:>10.3f}")
    print(f" {'F1 Score':<28}{f1:>10.3f}")
    print("-" * 62)
    print(f" Matriz de confusión: TP={tp}  FP={fp}  FN={fn}  TN={tn}")
    print()

    # --- Recall por categoría ----------------------------------------------
    print(f" {'Categoría':<26}{'N':>4}{'Recall det.':>14}{'Recall cat.':>14}")
    print("-" * 62)
    for categoria in sorted(por_categoria):
        total, detectadas, correctas = por_categoria[categoria]
        print(
            f" {categoria:<26}{total:>4}"
            f"{detectadas / total:>14.3f}{correctas / total:>14.3f}"
        )
    print(linea)
    print(" Recall det. = detectada como riesgo")
    print(" Recall cat. = detectada como riesgo y con la categoría correcta")

    # --- Detalle de errores --------------------------------------------------
    if errores:
        print()
        print(f" Errores ({len(errores)}):")
        for item, pred in errores:
            ident = item.get("id", "?")
            print(
                f"  [{ident}] real=({item['es_riesgo']}, {item['categoria']}) "
                f"pred=({pred['es_riesgo']}, {pred['categoria']})"
            )
            print(f"       {item['texto'][:100]}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--base"]
    usar_base = "--base" in sys.argv[1:]
    path = Path(args[0]) if args else Path(__file__).parent / "corpus.json"
    clf = RuleBasedClassifier(RISK_PATTERNS_BASE if usar_base else None)
    print(f" Patrones: {'línea base' if usar_base else 'afinados'}")
    evaluate(load_corpus(path), clf)


if __name__ == "__main__":
    main()
