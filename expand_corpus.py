"""
expand_corpus.py
----------------
Amplía corpus.json (40 cláusulas) a 80 cláusulas en corpus_expanded_80.json.

Las 40 cláusulas nuevas están redactadas a mano por el equipo (abajo, en
NUEVAS_CLAUSULAS) siguiendo plantillas de contratos colombianos. No se generan
con un LLM, para no sesgar la comparación reglas vs. LLM a favor del modelo.

Cada cláusula nueva tiene, además de los campos del corpus original:
- "variante": tipo de caso que busca probar
    * "redaccion"            -> riesgo con redacción distinta a la de los patrones
                                (orden invertido, sinónimos jurídicos, términos dispersos)
    * "semantico"            -> riesgo que está en el sentido, sin palabra clave
    * "normal"               -> cláusula estándar sin riesgo
    * "normal_palabra_clave" -> cláusula sin riesgo que contiene una palabra clave de
                                las reglas (pone a prueba los falsos positivos)
- "basada_en": id de la cláusula original de la que es variante (si aplica).
- "origen": "ampliacion".

Balance de las 40 nuevas:
- Categorías: pago 7, tiempo 7, procedimiento 7, roles_responsabilidades 7,
  seguridad 6, definicion_referencia 6.
- Contratos: 8 por cada uno de los 5 tipos.
- 22 de riesgo / 18 sin riesgo. Por primera vez hay riesgo en seguridad y en
  definicion_referencia, que en el corpus original solo tenían cláusulas normales.

Uso:
    python expand_corpus.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from rules_classifier import RISK_PATTERNS_BASE, RuleBasedClassifier

BASE_DIR = Path(__file__).parent
ENTRADA = BASE_DIR / "corpus.json"
SALIDA = BASE_DIR / "corpus_expanded_80.json"

CATEGORIAS = {"pago", "tiempo", "procedimiento", "roles_responsabilidades",
              "seguridad", "definicion_referencia"}
CONTRATOS = {"obra_publica", "orden_compra", "subcontrato", "suministro_equipos", "consultoria"}

# (contrato, categoria, es_riesgo, variante, basada_en, texto)
NUEVAS_CLAUSULAS = [
    # ------------------------------------------------------------------ pago
    ("obra_publica", "pago", True, "redaccion", 1,
     "El valor del contrato no será objeto de ajuste alguno durante su ejecución, cualquiera que sea "
     "la variación de los precios de los materiales en el mercado."),
    ("orden_compra", "pago", True, "semantico", 2,
     "La entidad pagará las facturas una vez el ministerio le haya girado los recursos correspondientes, "
     "sin que exista un plazo máximo para ello."),
    ("suministro_equipos", "pago", True, "redaccion", 9,
     "De cada factura se descontará y conservará un veinte por ciento, que solo será devuelto al proveedor "
     "tras la liquidación final del contrato, cuya fecha no se determina."),
    ("subcontrato", "pago", True, "semantico", None,
     "El subcontratista recibirá su pago únicamente cuando el contratista principal haya recibido del "
     "cliente final el pago correspondiente a las mismas actividades."),
    ("consultoria", "pago", False, "normal", None,
     "Los honorarios se ajustarán anualmente según la variación del índice de precios al consumidor "
     "certificada por el DANE."),
    ("obra_publica", "pago", False, "normal_palabra_clave", None,
     "Se retendrá en la fuente el porcentaje que corresponda de acuerdo con la normatividad tributaria vigente."),
    ("orden_compra", "pago", False, "normal", None,
     "El comprador pagará el valor total de la orden dentro de los 45 días calendario siguientes a la "
     "recepción a satisfacción de la mercancía."),
    # ---------------------------------------------------------------- tiempo
    ("obra_publica", "tiempo", True, "redaccion", 4,
     "Si el contratista no entrega la obra en la fecha pactada, se le descontará diariamente el dos por "
     "ciento del valor del contrato, sin límite máximo."),
    ("orden_compra", "tiempo", True, "redaccion", 11,
     "Las entregas efectuadas fuera del plazo acordado no serán pagadas al proveedor, quien además cubrirá "
     "el sobrecosto de adquirir los bienes con otro proveedor."),
    ("suministro_equipos", "tiempo", True, "semantico", None,
     "El proveedor deberá instalar los equipos en el plazo que el comprador le indique por escrito, el cual "
     "podrá ser inferior a cinco días hábiles."),
    ("subcontrato", "tiempo", True, "semantico", None,
     "Los atrasos causados por otros subcontratistas o por el contratista principal no darán lugar a "
     "ampliación del plazo de ejecución."),
    ("consultoria", "tiempo", False, "normal_palabra_clave", None,
     "En caso de retraso injustificado en la entrega de informes se aplicará una penalidad del 0,1% diario "
     "sobre el valor del informe, con un tope máximo del 10% del valor del contrato."),
    ("obra_publica", "tiempo", False, "normal", None,
     "El plazo de ejecución podrá prorrogarse de común acuerdo cuando se presenten lluvias excepcionales "
     "certificadas por el IDEAM."),
    ("suministro_equipos", "tiempo", False, "normal", None,
     "El proveedor entregará los equipos dentro de los 60 días calendario siguientes a la aprobación de la "
     "orden de compra."),
    # --------------------------------------------------------- procedimiento
    ("consultoria", "procedimiento", True, "redaccion", 6,
     "El contratante se reserva la facultad de finalizar este acuerdo por decisión propia en cualquier "
     "tiempo, bastando un aviso de veinticuatro horas."),
    ("subcontrato", "procedimiento", True, "redaccion", 18,
     "Las obras complementarias que ordene el contratista se entenderán incluidas en el precio global, sin "
     "que el subcontratista pueda reclamar pago adicional por ellas."),
    ("suministro_equipos", "procedimiento", True, "semantico", 24,
     "El comprador podrá variar las cantidades solicitadas hasta en un cincuenta por ciento, en más o en "
     "menos, manteniendo los precios unitarios inicialmente pactados."),
    ("obra_publica", "procedimiento", True, "semantico", None,
     "Toda reclamación del contratista deberá presentarse dentro de los tres días siguientes al hecho que la "
     "origina; vencido este término, se entenderá que renuncia a ella."),
    ("obra_publica", "procedimiento", False, "normal_palabra_clave", None,
     "La entidad solo podrá declarar la terminación unilateral en las causales previstas en la ley, previo "
     "debido proceso y con liquidación de lo ejecutado."),
    ("consultoria", "procedimiento", False, "normal", None,
     "Las partes podrán acordar por escrito la suspensión temporal del contrato, indicando la causa y la "
     "fecha estimada de reanudación."),
    ("orden_compra", "procedimiento", False, "normal", None,
     "Cualquier cambio en las condiciones de la orden de compra requerirá la aceptación escrita de ambas partes."),
    # ----------------------------------------------- roles_responsabilidades
    ("subcontrato", "roles_responsabilidades", True, "redaccion", 20,
     "El subcontratista exonerará y defenderá al contratista frente a toda demanda o reclamo que se presente "
     "con ocasión de las obras, cualquiera que sea su causa."),
    ("orden_compra", "roles_responsabilidades", True, "redaccion", 14,
     "Los gastos de retiro, reemplazo y nueva entrega de la mercancía rechazada correrán por cuenta del "
     "proveedor, sin límite de cuantía."),
    ("consultoria", "roles_responsabilidades", True, "semantico", 34,
     "El consultor responderá por las pérdidas que sufra el cliente como consecuencia de las decisiones que "
     "adopte con base en sus informes."),
    ("suministro_equipos", "roles_responsabilidades", True, "semantico", 27,
     "El proveedor responderá por los daños de los equipos aun cuando se deriven de un uso indebido o de la "
     "manipulación por parte del personal del comprador."),
    ("suministro_equipos", "roles_responsabilidades", False, "normal", None,
     "La responsabilidad de cada parte frente a la otra se limitará al valor total del contrato, salvo dolo "
     "o culpa grave."),
    ("subcontrato", "roles_responsabilidades", False, "normal", None,
     "El contratista principal suministrará los planos y especificaciones técnicas necesarios para la "
     "ejecución de las actividades subcontratadas."),
    ("consultoria", "roles_responsabilidades", False, "normal", None,
     "El consultor será responsable únicamente de la calidad técnica de sus informes y no garantiza "
     "resultados comerciales."),
    # ------------------------------------------------------------- seguridad
    ("obra_publica", "seguridad", True, "semantico", None,
     "El contratista asumirá las multas que impongan las autoridades por incumplimientos de seguridad "
     "industrial en la obra, incluso cuando se originen en instrucciones de la interventoría."),
    ("subcontrato", "seguridad", True, "semantico", None,
     "El subcontratista deberá iniciar labores en la fecha indicada aunque las pólizas y afiliaciones de su "
     "personal no hayan sido aprobadas, asumiendo cualquier consecuencia."),
    ("suministro_equipos", "seguridad", True, "semantico", None,
     "El proveedor asumirá los costos de cualquier accidente de trabajo ocurrido en las instalaciones del "
     "comprador, sin importar a quién sea imputable."),
    ("orden_compra", "seguridad", False, "normal", None,
     "Los vehículos del proveedor deberán contar con SOAT y revisión técnico-mecánica vigentes para "
     "ingresar a las bodegas del comprador."),
    ("consultoria", "seguridad", False, "normal", None,
     "El consultor y su personal cumplirán el reglamento de higiene y seguridad del cliente durante las "
     "visitas a sus instalaciones."),
    ("obra_publica", "seguridad", False, "normal", None,
     "El contratista implementará un plan de manejo de tráfico aprobado por la secretaría de movilidad "
     "antes de intervenir la vía."),
    # ------------------------------------------------- definicion_referencia
    ("suministro_equipos", "definicion_referencia", True, "semantico", None,
     "Para efectos de este contrato, se entenderá por fuerza mayor únicamente un desastre natural declarado "
     "oficialmente por el Gobierno Nacional; ningún otro evento tendrá esta calidad."),
    ("consultoria", "definicion_referencia", True, "semantico", None,
     "Se entenderá por 'aprobación del entregable' la manifestación expresa del cliente, sin que su "
     "silencio, cualquiera que sea su duración, implique aceptación."),
    ("orden_compra", "definicion_referencia", True, "semantico", None,
     "En caso de contradicción entre los documentos del contrato, prevalecerá siempre la interpretación que "
     "determine el comprador."),
    ("subcontrato", "definicion_referencia", False, "normal", None,
     "Los términos con mayúscula inicial tendrán el significado que se les asigna en el contrato principal, "
     "el cual hace parte integral de este subcontrato."),
    ("orden_compra", "definicion_referencia", False, "normal", None,
     "Las referencias a 'días' se entenderán hechas a días hábiles, salvo que se indique expresamente que "
     "son calendario."),
    ("subcontrato", "definicion_referencia", False, "normal", None,
     "Los anexos técnicos, el cronograma y la propuesta del subcontratista forman parte integral del "
     "presente documento."),
]


def validar(nuevas: list[dict], originales: list[dict]) -> None:
    """Chequeos de consistencia antes de guardar."""
    assert len(nuevas) == 40, f"Se esperaban 40 cláusulas nuevas, hay {len(nuevas)}"
    textos = [c["texto"] for c in originales + nuevas]
    assert len(textos) == len(set(textos)), "Hay cláusulas duplicadas"
    ids_originales = {c["id"] for c in originales}
    for c in nuevas:
        assert c["categoria"] in CATEGORIAS, f"Categoría inválida en {c['id']}"
        assert c["contrato"] in CONTRATOS, f"Contrato inválido en {c['id']}"
        assert c["basada_en"] is None or c["basada_en"] in ids_originales, f"basada_en inválido en {c['id']}"
        assert c["es_riesgo"] == (c["variante"] in {"redaccion", "semantico"}), f"Variante incoherente en {c['id']}"


def main() -> None:
    originales = json.loads(ENTRADA.read_text(encoding="utf-8"))
    siguiente_id = max(c["id"] for c in originales) + 1

    nuevas = [
        {"id": siguiente_id + i, "contrato": contrato, "categoria": categoria, "es_riesgo": riesgo,
         "texto": texto, "variante": variante, "basada_en": basada_en, "origen": "ampliacion"}
        for i, (contrato, categoria, riesgo, variante, basada_en, texto) in enumerate(NUEVAS_CLAUSULAS)
    ]
    validar(nuevas, originales)

    corpus = [{**c, "origen": "original"} for c in originales] + nuevas
    SALIDA.write_text(
        "[\n" + ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in corpus) + "\n]\n",
        encoding="utf-8",
    )

    # --- Resumen -------------------------------------------------------------
    n_riesgo = sum(c["es_riesgo"] for c in corpus)
    print(f"Corpus ampliado: {len(corpus)} cláusulas ({n_riesgo} de riesgo, {len(corpus) - n_riesgo} sin riesgo)")
    print(f"Guardado en {SALIDA.name}\n")
    print("Por categoría (riesgo / total):")
    for cat in sorted(CATEGORIAS):
        total = sum(c["categoria"] == cat for c in corpus)
        riesgo = sum(c["categoria"] == cat and c["es_riesgo"] for c in corpus)
        print(f"  {cat:<25} {riesgo:>2} / {total}")
    print("\nPor contrato:", dict(Counter(c["contrato"] for c in corpus)))
    print("Variantes nuevas:", dict(Counter(c["variante"] for c in nuevas)))

    # Comprobación rápida: ¿cuántas cláusulas nuevas acierta la línea base de reglas?
    clf = RuleBasedClassifier(RISK_PATTERNS_BASE)
    aciertos = Counter()
    for c in nuevas:
        aciertos[c["variante"], clf.classify(c["texto"])["es_riesgo"] == c["es_riesgo"]] += 1
    print("\nLínea base de reglas sobre las 40 nuevas (aciertos / total por variante):")
    for variante in ("redaccion", "semantico", "normal", "normal_palabra_clave"):
        ok, mal = aciertos[variante, True], aciertos[variante, False]
        print(f"  {variante:<22} {ok:>2} / {ok + mal}")


if __name__ == "__main__":
    main()
