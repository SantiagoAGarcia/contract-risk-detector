"""
llm_classifier.py
-----------------
Clasificador de riesgo contractual usando un LLM de OpenAI (gpt-4o-mini).

- Usa la librería oficial `openai` (v1.0+).
- La API Key se lee de la variable de entorno OPENAI_API_KEY (o LLM_API_KEY).
- Opcionalmente, LLM_BASE_URL y LLM_MODEL permiten usar cualquier proveedor
  compatible con la API de OpenAI (Groq, Gemini, Ollama local, etc.).
- temperature=0.0 para obtener respuestas lo más deterministas posible.
- Se fuerza la salida en JSON (response_format=json_object) y se valida.
"""

from __future__ import annotations

import json
import os

from openai import OpenAI

MODEL_NAME = "gpt-4o-mini"  # modelo por defecto (OpenAI)

CATEGORIAS = (
    "pago",
    "tiempo",
    "procedimiento",
    "roles_responsabilidades",
    "seguridad",
    "definicion_referencia",
)

SYSTEM_PROMPT = """Eres un abogado experto en contratación de obra civil y proveeduría en países hispanohablantes.
Tu tarea es analizar UNA cláusula contractual en español y determinar si representa un riesgo
contractual para el contratista, proveedor, subcontratista o consultor (la parte ejecutora).

## Qué constituye riesgo contractual
Una cláusula es riesgosa cuando traslada de forma desproporcionada cargas, costos o incertidumbre
a la parte ejecutora, o limita sus derechos sin contraprestación razonable. Ejemplos:
- Precios fijos sin reajuste, retenciones prolongadas, pagos sujetos a disponibilidad presupuestal,
  retrasos de pago sin intereses de mora.
- Penalidades desproporcionadas por retraso, pérdida del derecho de cobro por mora.
- Terminación, modificación o suspensión unilateral sin indemnización; trabajos adicionales sin
  reconocimiento económico; prohibiciones absolutas de cesión.
- Responsabilidad exclusiva o ilimitada, obligaciones de mantener indemne, garantías de resultados
  comerciales, cesión total de propiedad intelectual previa, asunción de todos los riesgos.

Una cláusula NO es riesgosa cuando es estándar, equilibrada u operativa (plazos razonables,
procedimientos de facturación, obligaciones recíprocas, definiciones, normas de seguridad comunes).

## Taxonomía de categorías (usa exactamente una)
- "pago": precio, forma y plazos de pago, reajustes, retenciones, facturación, intereses.
- "tiempo": plazos, cronogramas, entregas, retrasos, mora, penalidades por demora.
- "procedimiento": terminación, cesión, modificaciones, suspensión, trabajos adicionales,
  resolución de controversias, confidencialidad, trámites contractuales.
- "roles_responsabilidades": responsabilidades, indemnidades, garantías, asunción de riesgos,
  propiedad intelectual, obligaciones de cada parte.
- "seguridad": seguridad y salud en el trabajo, EPP, ARL, pólizas y seguros.
- "definicion_referencia": definiciones de términos y reglas de interpretación del contrato.

Asigna siempre la categoría temática de la cláusula, sea o no riesgosa.

## Formato de respuesta
Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, con estas llaves:
{"es_riesgo": true|false, "categoria": "<una de las categorías>", "razon": "<explicación breve en español>"}"""


class LLMClassifierError(RuntimeError):
    """Error al obtener o interpretar la respuesta del LLM."""


class LLMClassifier:
    """Clasificador de cláusulas basado en gpt-4o-mini."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        timeout: float = 30.0,
        max_retries: int = 2,
    ) -> None:
        api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMClassifierError(
                "La variable de entorno OPENAI_API_KEY (o LLM_API_KEY) no está definida."
            )
        # base_url=None usa el endpoint oficial de OpenAI.
        base_url = base_url or os.getenv("LLM_BASE_URL") or None
        # max_retries: el cliente reintenta solo ante 429/5xx respetando Retry-After.
        self.client = OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries
        )
        self.model = model or os.getenv("LLM_MODEL") or MODEL_NAME
        self.temperature = temperature

    def classify(self, texto: str) -> dict:
        """
        Clasifica una cláusula con el LLM.

        Retorna: {"es_riesgo": bool, "categoria": str, "razon": str}
        """
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"Cláusula a analizar:\n\"\"\"{texto}\"\"\""},
                ],
            )
        except Exception as exc:  # errores de red, autenticación, cuota, etc.
            raise LLMClassifierError(f"Error al invocar el modelo {self.model}: {exc}") from exc

        return self._parse(response.choices[0].message.content or "")

    @staticmethod
    def _parse(content: str) -> dict:
        """Valida y normaliza el JSON devuelto por el modelo."""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMClassifierError(f"El modelo no devolvió JSON válido: {content!r}") from exc

        es_riesgo = data.get("es_riesgo")
        if isinstance(es_riesgo, str):
            es_riesgo = es_riesgo.strip().lower() in {"true", "si", "sí", "1"}
        categoria = str(data.get("categoria", "")).strip().lower()
        if categoria not in CATEGORIAS:
            raise LLMClassifierError(f"Categoría no reconocida en la respuesta: {categoria!r}")

        return {
            "es_riesgo": bool(es_riesgo),
            "categoria": categoria,
            "razon": str(data.get("razon", "")).strip(),
        }


if __name__ == "__main__":
    # Prueba rápida (requiere OPENAI_API_KEY).
    clf = LLMClassifier()
    print(clf.classify("El comprador retendrá el 50% del pago durante un año como garantía."))
