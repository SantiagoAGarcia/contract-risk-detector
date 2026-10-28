# Análisis de resultados: reglas vs. modelo de lenguaje

<!--
PLANTILLA. Los marcadores {{...}} los completa automáticamente
`python experimentation_and_stats.py`, que genera ANALISIS_RESULTADOS_completado.md.
Los bloques [COMPLETAR: ...] son interpretación que debe redactar el equipo.
-->

_Fecha de ejecución: {{FECHA}} · Modelo de lenguaje: `{{MODELO_LLM}}` · Corpus: {{N_CLAUSULAS}} cláusulas ({{N_RIESGO}} de riesgo, {{N_NORMALES}} sin riesgo)._

## 1. Resultados comparativos

Ambos métodos se evaluaron sobre las mismas {{N_CLAUSULAS}} cláusulas del corpus sintético. La tarea es binaria
(¿la cláusula es de riesgo?) y la clase positiva es "riesgo". El clasificador de reglas usa los patrones de
la línea base, definidos antes de analizar los fallos. El modelo de lenguaje se ejecutó con temperatura 0.

{{TABLA_METRICAS}}

_Tabla 1. Detección de cláusulas de riesgo. \*Las reglas afinadas se ajustaron mirando este mismo corpus,
por lo que sus métricas están sobreajustadas y solo se muestran como referencia._

{{TABLA_CONFUSION}}

_Tabla 2. Matriz de confusión (VP: riesgo detectado; FP: falsa alarma; VN: normal bien descartada; FN: riesgo no detectado)._

{{TABLA_RECALL_CATEGORIA}}

_Tabla 3. Recall por categoría. Las categorías seguridad y definición/referencia no tienen cláusulas de
riesgo en el corpus, por lo que su recall no está definido._

[COMPLETAR: interpretación de las tablas. Por ejemplo, comparar el recall de las reglas ({{REGLAS_RECALL}})
con el del LLM ({{LLM_RECALL}}), comentar si el LLM generó falsas alarmas y en qué categorías hay más diferencia.
Contrastar con las metas de la documentación (recall ≥ 0.85, F1 ≥ 0.87).]

## 2. Análisis estadístico: prueba de McNemar

Para saber si la diferencia entre los dos métodos es real o puede deberse al azar, se aplicó la prueba
de McNemar. Es la prueba adecuada para comparar dos clasificadores evaluados sobre **las mismas**
muestras, porque solo usa las cláusulas en las que los métodos discrepan.

| | LLM acierta | LLM falla |
|---|---|---|
| **Reglas aciertan** | a = {{MC_A}} | b = {{MC_B}} |
| **Reglas fallan** | c = {{MC_C}} | d = {{MC_D}} |

_Tabla 4. Matriz de contingencia de aciertos y fallos (detección binaria)._

- **a**: cláusulas en las que ambos aciertan; **d**: ambos fallan. No aportan información sobre cuál es mejor.
- **b**: las reglas aciertan y el LLM falla; **c**: las reglas fallan y el LLM acierta.
- **Hipótesis nula (H0)**: ambos métodos tienen la misma tasa de error (b y c son igual de probables).
- **Método**: {{MC_METODO}} (se usa la prueba binomial exacta cuando b + c < 25).
- **Estadístico**: {{MC_ESTADISTICO}} · **p-valor**: {{MC_PVALOR}} · **α** = 0.05

**Conclusión:** {{MC_CONCLUSION}}

[COMPLETAR: interpretación práctica. ¿Aporta la IA una mejora estadísticamente significativa frente a las
reglas? Si no es significativa, explicar que con {{N_CLAUSULAS}} cláusulas y pocos casos discordantes la
prueba tiene poca potencia: un resultado no significativo no demuestra que los métodos sean iguales.
Relacionar con la ampliación del corpus a ~80 cláusulas.]

## 3. Cualificación de errores

### 3.1 Fallos de las reglas

Las reglas fallaron en **{{N_FALLOS_REGLAS}}** cláusulas. Se clasificaron en dos tipos:

- **Redacción distinta a la esperada ({{N_REDACCION}} casos):** la frase clave existe, pero con otro orden
  de palabras, otra conjugación o sinónimos que el patrón no captura. Se pueden corregir afinando las reglas,
  con riesgo de sobreajuste.
- **Riesgo en el sentido, no en una palabra ({{N_SEMANTICO}} casos):** no hay una frase clave que buscar.
  El riesgo depende de interpretar la cláusula completa o de combinar varias obligaciones.

La clasificación inicial es automática: se marca como "redacción distinta" cuando al menos la mitad de las
palabras de algún patrón aparece en la cláusula. Después se revisó manualmente.

{{TABLA_FALLOS_REGLAS}}

_Tabla 5. Fallos de las reglas y si el LLM los resolvió. El LLM resolvió {{N_RESUELTOS_LLM}} de {{N_FALLOS_REGLAS}}._

[COMPLETAR: análisis caso por caso. Para cada cláusula, explicar por qué fallaron las reglas y citar la razón
que dio el LLM (está en resultados_evaluacion.json, campo `llm_razon`). Señalar si alguna clasificación
automática se corrigió a mano.]

### 3.2 Fallos del modelo de lenguaje

{{TABLA_FALLOS_LLM}}

_Tabla 6. Fallos del modelo de lenguaje._

[COMPLETAR: ¿los fallos del LLM son falsas alarmas (más conservador) o riesgos no detectados? ¿Coinciden
con fallos de las reglas (casilla d de McNemar)?]

## 4. Limitaciones

- El corpus es pequeño ({{N_CLAUSULAS}} cláusulas) y sintético, y las cláusulas de riesgo las redactó el propio equipo.
- Las reglas de la línea base se escribieron conociendo el corpus, lo que probablemente las favorece.
- Aunque se use temperatura 0, el LLM puede variar entre versiones del modelo. Las respuestas usadas
  quedaron guardadas en `llm_cache.json`.
- Seguridad y definición/referencia no tienen cláusulas de riesgo, así que no se mide el recall en esas categorías.
