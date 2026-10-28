"""
rules_classifier.py
-------------------
Clasificador basado en reglas (expresiones regulares) para detectar cláusulas
de riesgo en contratos de obra y proveeduría redactados en español.

Estrategia:
1. Se normaliza el texto (minúsculas y espacios colapsados).
2. Se evalúan los patrones de cada categoría de riesgo.
3. Si al menos un patrón se activa, la cláusula se marca como riesgosa.
4. La categoría asignada es la que acumula más patrones activados; en caso de
   empate se elige la categoría cuyo primer patrón aparece antes en el texto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Pattern

# Valor de categoría cuando la cláusula no presenta riesgo.
SIN_CATEGORIA = "ninguna"

# ---------------------------------------------------------------------------
# Patrones BASE (línea base documentada en el paper).
# Son exactamente los patrones definidos en la especificación inicial, escritos
# antes de analizar los fallos. Se usan en la experimentación para no
# sobreajustar las reglas al corpus de evaluación.
# ---------------------------------------------------------------------------
RISK_PATTERNS_BASE: Dict[str, List[str]] = {
    "pago": [
        r"precio fijo",
        r"inamovible",
        r"retendr[áa]",
        r"sin reajuste",
        r"disponibilidad presupuestal",
        r"sin.*intereses",
    ],
    "tiempo": [
        r"penalidad",
        r"por cada d[íi]a de retraso",
        r"mora en la entrega",
        r"perder[áa] el derecho",
    ],
    "procedimiento": [
        r"terminaci[óo]n unilateral",
        r"ceder el presente contrato",
        r"trabajos adicionales",
        r"sin derecho a.*reconocimiento",
        r"modificar unilateralmente",
        r"suspender indefinidamente",
    ],
    "roles_responsabilidades": [
        r"responsabilidad exclusiva",
        r"indemne",
        r"a su entero costo",
        r"respuesta ilimitada",
        r"titularidad exclusiva",
        r"garantiza un resultado",
    ],
}

# ---------------------------------------------------------------------------
# Patrones AFINADOS (usados por defecto en la API).
# Extienden la línea base con variantes encontradas al revisar el corpus, por
# lo que sus métricas sobre corpus.json están sobreajustadas.
# Se usan clases como [áa] para tolerar textos con o sin tilde.
# Las distancias acotadas (.{0,N}) evitan que un patrón "salte" entre frases.
# ---------------------------------------------------------------------------
RISK_PATTERNS: Dict[str, List[str]] = {
    "pago": [
        r"precios?\s+fijos?",                      # "precio fijo", "precios fijos"
        r"precio\b.{0,25}\bfij[oa]",               # "el precio pactado es fijo"
        r"inamovible",
        r"retendr[áa]",
        r"sin\s+(ning[úu]n\s+)?reajuste",
        r"renunci\w*\s+a\s+(cualquier|todo)\s+reajuste",
        r"disponibilidad\s+presupuestal",
        r"sin.{0,40}intereses",                    # "sin que esto genere intereses"
        r"garant[íi]a\s+indefinida",
    ],
    "tiempo": [
        r"penalidad",
        r"por\s+cada\s+d[íi]a\s+de\s+(retraso|atraso|demora)",
        r"mora\s+en\s+la\s+entrega",
        r"perder[áa]\s+el\s+derecho",
    ],
    "procedimiento": [
        r"terminaci[óo]n\s+unilateral",
        r"termin\w*.{0,40}unilateral",             # "dar por terminado ... de manera unilateral"
        r"ceder\s+el\s+presente\s+contrato",
        r"trabajos\s+adicionales",
        r"sin\s+derecho\s+a.{0,40}reconocimiento",
        r"no\s+tendr[áa]\s+derecho\s+a.{0,40}reconocimiento",
        r"modificar\s+unilateralmente",
        r"suspender\s+indefinidamente",
        r"sin\s+(necesidad\s+de\s+)?.{0,30}indemnizaci[óo]n",
    ],
    "roles_responsabilidades": [
        r"responsabilidad\s+exclusiva",
        r"indemne",
        r"a\s+su\s+entero\s+costo",
        r"responsabilidad\s+ilimitada",
        r"respond\w*\s+(de\s+manera\s+)?ilimitad[ao]",
        r"ilimitad[ao]",
        r"titularidad\s+exclusiva",
        r"garantiza\s+un\s+resultado",
        r"asum\w+\s+todo\s+(el\s+)?(riesgo|penalidad)",
    ],
}


@dataclass(frozen=True)
class _Match:
    """Representa un patrón activado dentro del texto."""

    categoria: str
    patron: str
    posicion: int


class RuleBasedClassifier:
    """Clasificador de riesgo contractual basado en expresiones regulares."""

    def __init__(self, patterns: Dict[str, List[str]] | None = None) -> None:
        source = patterns if patterns is not None else RISK_PATTERNS
        # Compilamos una sola vez para reutilizar en cada llamada.
        self._compiled: Dict[str, List[Pattern[str]]] = {
            categoria: [re.compile(p, re.IGNORECASE) for p in lista]
            for categoria, lista in source.items()
        }

    @staticmethod
    def _normalize(texto: str) -> str:
        """Pasa a minúsculas y colapsa espacios en blanco."""
        return re.sub(r"\s+", " ", texto.lower()).strip()

    def _find_matches(self, texto: str) -> List[_Match]:
        matches: List[_Match] = []
        for categoria, patrones in self._compiled.items():
            for patron in patrones:
                m = patron.search(texto)
                if m:
                    matches.append(_Match(categoria, patron.pattern, m.start()))
        return matches

    def classify(self, texto: str) -> dict:
        """
        Clasifica una cláusula.

        Retorna:
            {
                "es_riesgo": bool,
                "categoria": str,              # "ninguna" si no hay riesgo
                "patrones_activados": list[str]
            }
        """
        normalizado = self._normalize(texto or "")
        matches = self._find_matches(normalizado)

        if not matches:
            return {"es_riesgo": False, "categoria": SIN_CATEGORIA, "patrones_activados": []}

        # Agrupamos por categoría: (conteo, primera posición)
        resumen: Dict[str, tuple[int, int]] = {}
        for m in matches:
            conteo, pos = resumen.get(m.categoria, (0, m.posicion))
            resumen[m.categoria] = (conteo + 1, min(pos, m.posicion))

        # Más patrones gana; en empate, el que aparece primero en el texto.
        categoria = min(resumen, key=lambda c: (-resumen[c][0], resumen[c][1]))

        return {
            "es_riesgo": True,
            "categoria": categoria,
            "patrones_activados": [f"{m.categoria}:{m.patron}" for m in matches],
        }


if __name__ == "__main__":
    # Prueba rápida desde la línea de comandos.
    clf = RuleBasedClassifier()
    ejemplo = "Por cada día de retraso el contratista pagará una penalidad del 3%."
    print(clf.classify(ejemplo))
