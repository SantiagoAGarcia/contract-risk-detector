# Análisis de resultados: reglas vs. modelo de lenguaje

<!--
PLANTILLA. Los marcadores {{...}} los completa automáticamente
`python experimentation_and_stats.py`, que genera ANALISIS_RESULTADOS_completado.md.
Los bloques [COMPLETAR: ...] son interpretación que debe redactar el equipo.
-->

_Fecha de ejecución: 2026-10-27 · Modelo de lenguaje: `openai/gpt-oss-120b` · Corpus: 80 cláusulas (41 de riesgo, 39 sin riesgo)._

## 1. Resultados comparativos

Ambos métodos se evaluaron sobre las mismas 80 cláusulas del corpus sintético. La tarea es binaria
(¿la cláusula es de riesgo?) y la clase positiva es "riesgo". El clasificador de reglas usa los patrones de
la línea base, definidos antes de analizar los fallos. El modelo de lenguaje se ejecutó con temperatura 0.

| Método                    | Precisión   | Recall   | F1-Score   | Exactitud   |
|:--------------------------|:------------|:---------|:-----------|:------------|
| Reglas (línea base)       | 0.83        | 0.37     | 0.51       | 0.64        |
| Reglas afinadas*          | 0.86        | 0.46     | 0.60       | 0.69        |
| LLM (openai/gpt-oss-120b) | 0.98        | 0.98     | 0.98       | 0.97        |

_Tabla 1. Detección de cláusulas de riesgo. \*Las reglas afinadas se ajustaron mirando este mismo corpus,
por lo que sus métricas están sobreajustadas y solo se muestran como referencia._

| Método                    | VP   | FP   | VN   | FN   |
|:--------------------------|:-----|:-----|:-----|:-----|
| Reglas (línea base)       | 15   | 3    | 36   | 26   |
| Reglas afinadas*          | 19   | 3    | 36   | 22   |
| LLM (openai/gpt-oss-120b) | 40   | 1    | 38   | 1    |

_Tabla 2. Matriz de confusión (VP: riesgo detectado; FP: falsa alarma; VN: normal bien descartada; FN: riesgo no detectado)._

| Categoría               | N riesgo   | Recall Reglas (línea base)   | Recall Reglas afinadas*   | Recall LLM (openai/gpt-oss-120b)   |
|:------------------------|:-----------|:-----------------------------|:--------------------------|:-----------------------------------|
| pago                    | 7          | 0.43 (3/7)                   | 0.43 (3/7)                | 1.00 (7/7)                         |
| tiempo                  | 6          | 0.33 (2/6)                   | 0.33 (2/6)                | 1.00 (6/6)                         |
| procedimiento           | 10         | 0.40 (4/10)                  | 0.60 (6/10)               | 1.00 (10/10)                       |
| roles_responsabilidades | 12         | 0.50 (6/12)                  | 0.67 (8/12)               | 1.00 (12/12)                       |
| seguridad               | 3          | 0.00 (0/3)                   | 0.00 (0/3)                | 1.00 (3/3)                         |
| definicion_referencia   | 3          | 0.00 (0/3)                   | 0.00 (0/3)                | 0.67 (2/3)                         |

_Tabla 3. Recall por categoría. Las categorías seguridad y definición/referencia no tienen cláusulas de
riesgo en el corpus, por lo que su recall no está definido._

[COMPLETAR: interpretación de las tablas. Por ejemplo, comparar el recall de las reglas (0.37)
con el del LLM (0.98), comentar si el LLM generó falsas alarmas y en qué categorías hay más diferencia.
Contrastar con las metas de la documentación (recall ≥ 0.85, F1 ≥ 0.87).]

## 2. Análisis estadístico: prueba de McNemar

Para saber si la diferencia entre los dos métodos es real o puede deberse al azar, se aplicó la prueba
de McNemar. Es la prueba adecuada para comparar dos clasificadores evaluados sobre **las mismas**
muestras, porque solo usa las cláusulas en las que los métodos discrepan.

| | LLM acierta | LLM falla |
|---|---|---|
| **Reglas aciertan** | a = 50 | b = 1 |
| **Reglas fallan** | c = 28 | d = 1 |

_Tabla 4. Matriz de contingencia de aciertos y fallos (detección binaria)._

- **a**: cláusulas en las que ambos aciertan; **d**: ambos fallan. No aportan información sobre cuál es mejor.
- **b**: las reglas aciertan y el LLM falla; **c**: las reglas fallan y el LLM acierta.
- **Hipótesis nula (H0)**: ambos métodos tienen la misma tasa de error (b y c son igual de probables).
- **Método**: chi-cuadrado con corrección de continuidad (se usa la prueba binomial exacta cuando b + c < 25).
- **Estadístico**: 23.3103 · **p-valor**: 0.0000 · **α** = 0.05

**Conclusión:** p = 0.0000 < α = 0.05: se rechaza H0. Existe una diferencia estadísticamente significativa entre Reglas y LLM; LLM comete menos errores.

[COMPLETAR: interpretación práctica. ¿Aporta la IA una mejora estadísticamente significativa frente a las
reglas? Si no es significativa, explicar que con 80 cláusulas y pocos casos discordantes la
prueba tiene poca potencia: un resultado no significativo no demuestra que los métodos sean iguales.
Relacionar con la ampliación del corpus a ~80 cláusulas.]

## 3. Cualificación de errores

### 3.1 Fallos de las reglas

Las reglas fallaron en **29** cláusulas. Se clasificaron en dos tipos:

- **Redacción distinta a la esperada (22 casos):** la frase clave existe, pero con otro orden
  de palabras, otra conjugación o sinónimos que el patrón no captura. Se pueden corregir afinando las reglas,
  con riesgo de sobreajuste.
- **Riesgo en el sentido, no en una palabra (7 casos):** no hay una frase clave que buscar.
  El riesgo depende de interpretar la cláusula completa o de combinar varias obligaciones.

La clasificación inicial es automática: se marca como "redacción distinta" cuando al menos la mitad de las
palabras de algún patrón aparece en la cláusula. Después se revisó manualmente.

| ID   | Error   | Categoría real          | Tipo de fallo                           | ¿LLM lo resuelve?   | Cláusula                                                                                    |
|:-----|:--------|:------------------------|:----------------------------------------|:--------------------|:--------------------------------------------------------------------------------------------|
| 6    | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | La entidad contratante podrá dar por terminado el contrato de manera unilateral y anticipa… |
| 18   | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | El subcontratista no tendrá derecho a reconocimiento económico alguno por actividades comp… |
| 21   | FN      | roles_responsabilidades | Redacción distinta a la esperada        | Sí                  | El subcontratista responde de manera ilimitada por los perjuicios que sus empleados ocasio… |
| 27   | FN      | roles_responsabilidades | Riesgo en el sentido, no en una palabra | Sí                  | El proveedor asume todo el riesgo por pérdida o deterioro de los equipos hasta el momento … |
| 41   | FN      | pago                    | Redacción distinta a la esperada        | Sí                  | El valor del contrato no será objeto de ajuste alguno durante su ejecución, cualquiera que… |
| 42   | FN      | pago                    | Riesgo en el sentido, no en una palabra | Sí                  | La entidad pagará las facturas una vez el ministerio le haya girado los recursos correspon… |
| 43   | FN      | pago                    | Redacción distinta a la esperada        | Sí                  | De cada factura se descontará y conservará un veinte por ciento, que solo será devuelto al… |
| 44   | FN      | pago                    | Redacción distinta a la esperada        | Sí                  | El subcontratista recibirá su pago únicamente cuando el contratista principal haya recibid… |
| 46   | FP      | pago                    | Riesgo en el sentido, no en una palabra | Sí                  | Se retendrá en la fuente el porcentaje que corresponda de acuerdo con la normatividad trib… |
| 48   | FN      | tiempo                  | Redacción distinta a la esperada        | Sí                  | Si el contratista no entrega la obra en la fecha pactada, se le descontará diariamente el … |
| 49   | FN      | tiempo                  | Redacción distinta a la esperada        | Sí                  | Las entregas efectuadas fuera del plazo acordado no serán pagadas al proveedor, quien adem… |
| 50   | FN      | tiempo                  | Riesgo en el sentido, no en una palabra | Sí                  | El proveedor deberá instalar los equipos en el plazo que el comprador le indique por escri… |
| 51   | FN      | tiempo                  | Redacción distinta a la esperada        | Sí                  | Los atrasos causados por otros subcontratistas o por el contratista principal no darán lug… |
| 52   | FP      | tiempo                  | Riesgo en el sentido, no en una palabra | Sí                  | En caso de retraso injustificado en la entrega de informes se aplicará una penalidad del 0… |
| 55   | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | El contratante se reserva la facultad de finalizar este acuerdo por decisión propia en cua… |
| 56   | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | Las obras complementarias que ordene el contratista se entenderán incluidas en el precio g… |
| 57   | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | El comprador podrá variar las cantidades solicitadas hasta en un cincuenta por ciento, en … |
| 58   | FN      | procedimiento           | Redacción distinta a la esperada        | Sí                  | Toda reclamación del contratista deberá presentarse dentro de los tres días siguientes al … |
| 59   | FP      | procedimiento           | Riesgo en el sentido, no en una palabra | Sí                  | La entidad solo podrá declarar la terminación unilateral en las causales previstas en la l… |
| 62   | FN      | roles_responsabilidades | Redacción distinta a la esperada        | Sí                  | El subcontratista exonerará y defenderá al contratista frente a toda demanda o reclamo que… |
| 63   | FN      | roles_responsabilidades | Redacción distinta a la esperada        | Sí                  | Los gastos de retiro, reemplazo y nueva entrega de la mercancía rechazada correrán por cue… |
| 64   | FN      | roles_responsabilidades | Redacción distinta a la esperada        | Sí                  | El consultor responderá por las pérdidas que sufra el cliente como consecuencia de las dec… |
| 65   | FN      | roles_responsabilidades | Redacción distinta a la esperada        | Sí                  | El proveedor responderá por los daños de los equipos aun cuando se deriven de un uso indeb… |
| 69   | FN      | seguridad               | Redacción distinta a la esperada        | Sí                  | El contratista asumirá las multas que impongan las autoridades por incumplimientos de segu… |
| 70   | FN      | seguridad               | Riesgo en el sentido, no en una palabra | Sí                  | El subcontratista deberá iniciar labores en la fecha indicada aunque las pólizas y afiliac… |
| 71   | FN      | seguridad               | Redacción distinta a la esperada        | Sí                  | El proveedor asumirá los costos de cualquier accidente de trabajo ocurrido en las instalac… |
| 75   | FN      | definicion_referencia   | Redacción distinta a la esperada        | Sí                  | Para efectos de este contrato, se entenderá por fuerza mayor únicamente un desastre natura… |
| 76   | FN      | definicion_referencia   | Redacción distinta a la esperada        | No                  | Se entenderá por 'aprobación del entregable' la manifestación expresa del cliente, sin que… |
| 77   | FN      | definicion_referencia   | Redacción distinta a la esperada        | Sí                  | En caso de contradicción entre los documentos del contrato, prevalecerá siempre la interpr… |

_Tabla 5. Fallos de las reglas y si el LLM los resolvió. El LLM resolvió 28 de 29._

[COMPLETAR: análisis caso por caso. Para cada cláusula, explicar por qué fallaron las reglas y citar la razón
que dio el LLM (está en resultados_evaluacion.json, campo `llm_razon`). Señalar si alguna clasificación
automática se corrigió a mano.]

### 3.2 Fallos del modelo de lenguaje

| ID   | Error   | Categoría real        | Razón del LLM                                                                                                            |
|:-----|:--------|:----------------------|:-------------------------------------------------------------------------------------------------------------------------|
| 19   | FP      | procedimiento         | Obliga a resolver controversias solo mediante arreglo directo entre los directores del proyecto, limitando al subcontrat |
| 76   | FN      | definicion_referencia | Establece que la aceptación del entregable debe ser expresa del cliente, evitando que el silencio implique aceptación y  |

_Tabla 6. Fallos del modelo de lenguaje._

[COMPLETAR: ¿los fallos del LLM son falsas alarmas (más conservador) o riesgos no detectados? ¿Coinciden
con fallos de las reglas (casilla d de McNemar)?]

## 4. Limitaciones

- El corpus es pequeño (80 cláusulas) y sintético, y las cláusulas de riesgo las redactó el propio equipo.
- Las reglas de la línea base se escribieron conociendo el corpus, lo que probablemente las favorece.
- Aunque se use temperatura 0, el LLM puede variar entre versiones del modelo. Las respuestas usadas
  quedaron guardadas en `llm_cache.json`.
- Seguridad y definición/referencia no tienen cláusulas de riesgo, así que no se mide el recall en esas categorías.
