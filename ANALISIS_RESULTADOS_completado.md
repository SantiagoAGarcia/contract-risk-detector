# Análisis de resultados: reglas vs. modelo de lenguaje

<!--
PLANTILLA. Los marcadores {{...}} los completa automáticamente
`python experimentation_and_stats.py`, que genera ANALISIS_RESULTADOS_completado.md.
Los bloques [COMPLETAR: ...] son interpretación que debe redactar el equipo.
-->

_Fecha de ejecución: 2026-10-27 · Modelo de lenguaje: `openai/gpt-oss-120b` · Corpus: 40 cláusulas (19 de riesgo, 21 sin riesgo)._

## 1. Resultados comparativos

Ambos métodos se evaluaron sobre las mismas 40 cláusulas del corpus sintético. La tarea es binaria
(¿la cláusula es de riesgo?) y la clase positiva es "riesgo". El clasificador de reglas usa los patrones de
la línea base, definidos antes de analizar los fallos. El modelo de lenguaje se ejecutó con temperatura 0.

| Método                    | Precisión   | Recall   | F1-Score   | Exactitud   |
|:--------------------------|:------------|:---------|:-----------|:------------|
| Reglas (línea base)       | 1.00        | 0.79     | 0.88       | 0.90        |
| Reglas afinadas*          | 1.00        | 1.00     | 1.00       | 1.00        |
| LLM (openai/gpt-oss-120b) | 0.95        | 1.00     | 0.97       | 0.97        |

_Tabla 1. Detección de cláusulas de riesgo. \*Las reglas afinadas se ajustaron mirando este mismo corpus,
por lo que sus métricas están sobreajustadas y solo se muestran como referencia._

| Método                    | VP   | FP   | VN   | FN   |
|:--------------------------|:-----|:-----|:-----|:-----|
| Reglas (línea base)       | 15   | 0    | 21   | 4    |
| Reglas afinadas*          | 19   | 0    | 21   | 0    |
| LLM (openai/gpt-oss-120b) | 19   | 1    | 20   | 0    |

_Tabla 2. Matriz de confusión (VP: riesgo detectado; FP: falsa alarma; VN: normal bien descartada; FN: riesgo no detectado)._

| Categoría               | N riesgo   | Recall Reglas (línea base)   | Recall Reglas afinadas*   | Recall LLM (openai/gpt-oss-120b)   |
|:------------------------|:-----------|:-----------------------------|:--------------------------|:-----------------------------------|
| pago                    | 3          | 1.00 (3/3)                   | 1.00 (3/3)                | 1.00 (3/3)                         |
| tiempo                  | 2          | 1.00 (2/2)                   | 1.00 (2/2)                | 1.00 (2/2)                         |
| procedimiento           | 6          | 0.67 (4/6)                   | 1.00 (6/6)                | 1.00 (6/6)                         |
| roles_responsabilidades | 8          | 0.75 (6/8)                   | 1.00 (8/8)                | 1.00 (8/8)                         |
| seguridad               | 0          | N/A (sin riesgo)             | N/A (sin riesgo)          | N/A (sin riesgo)                   |
| definicion_referencia   | 0          | N/A (sin riesgo)             | N/A (sin riesgo)          | N/A (sin riesgo)                   |

_Tabla 3. Recall por categoría. Las categorías seguridad y definición/referencia no tienen cláusulas de
riesgo en el corpus, por lo que su recall no está definido._

[COMPLETAR: interpretación de las tablas. Por ejemplo, comparar el recall de las reglas (0.79)
con el del LLM (1.00), comentar si el LLM generó falsas alarmas y en qué categorías hay más diferencia.
Contrastar con las metas de la documentación (recall ≥ 0.85, F1 ≥ 0.87).]

## 2. Análisis estadístico: prueba de McNemar

Para saber si la diferencia entre los dos métodos es real o puede deberse al azar, se aplicó la prueba
de McNemar. Es la prueba adecuada para comparar dos clasificadores evaluados sobre **las mismas**
muestras, porque solo usa las cláusulas en las que los métodos discrepan.

| | LLM acierta | LLM falla |
|---|---|---|
| **Reglas aciertan** | a = 35 | b = 1 |
| **Reglas fallan** | c = 4 | d = 0 |

_Tabla 4. Matriz de contingencia de aciertos y fallos (detección binaria)._

- **a**: cláusulas en las que ambos aciertan; **d**: ambos fallan. No aportan información sobre cuál es mejor.
- **b**: las reglas aciertan y el LLM falla; **c**: las reglas fallan y el LLM acierta.
- **Hipótesis nula (H0)**: ambos métodos tienen la misma tasa de error (b y c son igual de probables).
- **Método**: binomial exacta (se usa la prueba binomial exacta cuando b + c < 25).
- **Estadístico**: 1.0000 · **p-valor**: 0.3750 · **α** = 0.05

**Conclusión:** p = 0.3750 ≥ α = 0.05: no se rechaza H0. No hay evidencia estadísticamente significativa de que Reglas y LLM difieran en su tasa de error (con n = 40 cláusulas).

[COMPLETAR: interpretación práctica. ¿Aporta la IA una mejora estadísticamente significativa frente a las
reglas? Si no es significativa, explicar que con 40 cláusulas y pocos casos discordantes la
prueba tiene poca potencia: un resultado no significativo no demuestra que los métodos sean iguales.
Relacionar con la ampliación del corpus a ~80 cláusulas.]

## 3. Cualificación de errores

### 3.1 Fallos de las reglas

Las reglas fallaron en **4** cláusulas. Se clasificaron en dos tipos:

- **Redacción distinta a la esperada (3 casos):** la frase clave existe, pero con otro orden
  de palabras, otra conjugación o sinónimos que el patrón no captura. Se pueden corregir afinando las reglas,
  con riesgo de sobreajuste.
- **Riesgo en el sentido, no en una palabra (1 casos):** no hay una frase clave que buscar.
  El riesgo depende de interpretar la cláusula completa o de combinar varias obligaciones.

La clasificación inicial es automática: se marca como "redacción distinta" cuando al menos la mitad de las
palabras de algún patrón aparece en la cláusula. Después se revisó manualmente.

| ID   | Error   | Categoría real          | Tipo de fallo                           | ¿LLM lo resuelve?   | Cláusula                                                                                    |
|:-----|:--------|:------------------------|:----------------------------------------|:--------------------|:--------------------------------------------------------------------------------------------|
| 6    | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | La entidad contratante podrá dar por terminado el contrato de manera unilateral y anticipa… |
| 18   | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | El subcontratista no tendrá derecho a reconocimiento económico alguno por actividades comp… |
| 21   | FN      | roles_responsabilidades | Redacción distinta a la esperada        | Sí                  | El subcontratista responde de manera ilimitada por los perjuicios que sus empleados ocasio… |
| 27   | FN      | roles_responsabilidades | Riesgo en el sentido, no en una palabra | Sí                  | El proveedor asume todo el riesgo por pérdida o deterioro de los equipos hasta el momento … |

_Tabla 5. Fallos de las reglas y si el LLM los resolvió. El LLM resolvió 4 de 4._

[COMPLETAR: análisis caso por caso. Para cada cláusula, explicar por qué fallaron las reglas y citar la razón
que dio el LLM (está en resultados_evaluacion.json, campo `llm_razon`). Señalar si alguna clasificación
automática se corrigió a mano.]

### 3.2 Fallos del modelo de lenguaje

| ID   | Error   | Categoría real   | Razón del LLM                                                                                                            |
|:-----|:--------|:-----------------|:-------------------------------------------------------------------------------------------------------------------------|
| 19   | FP      | procedimiento    | Obliga a resolver controversias solo mediante arreglo directo entre los directores del proyecto, limitando al subcontrat |

_Tabla 6. Fallos del modelo de lenguaje._

[COMPLETAR: ¿los fallos del LLM son falsas alarmas (más conservador) o riesgos no detectados? ¿Coinciden
con fallos de las reglas (casilla d de McNemar)?]

## 4. Limitaciones

- El corpus es pequeño (40 cláusulas) y sintético, y las cláusulas de riesgo las redactó el propio equipo.
- Las reglas de la línea base se escribieron conociendo el corpus, lo que probablemente las favorece.
- Aunque se use temperatura 0, el LLM puede variar entre versiones del modelo. Las respuestas usadas
  quedaron guardadas en `llm_cache.json`.
- Seguridad y definición/referencia no tienen cláusulas de riesgo, así que no se mide el recall en esas categorías.
