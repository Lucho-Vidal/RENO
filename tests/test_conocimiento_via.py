"""Pruebas unitarias e integración para el módulo de Conocimiento de Vía por Sector."""

from datetime import date
from pathlib import Path

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.domain.conocimiento_via import (
    SECTORES_VIA_BASE,
    calcular_fecha_vencimiento,
    es_ayudante_conduccion,
    es_personal_conduccion,
    evaluar_vigencia_conocimiento,
    resolver_sectores_recorrido,
)
from src.services.excel_service import ExcelService
from src.services.records_service import RecordsService


def test_vencimiento_dos_anios_y_umbrales_60_30():
    assert calcular_fecha_vencimiento(date(2024, 2, 29), anios=2) == date(2026, 2, 28)

    hoy = date(2026, 11, 20)
    # Vigente (> 60 días)
    ev_vig = evaluar_vigencia_conocimiento("20/05/2025", hoy=hoy)
    assert ev_vig["estado"] == "VIGENTE"
    assert ev_vig["estado_alerta"] == "VIGENTE"
    assert ev_vig["fecha_vencimiento_display"] == "20/05/2027"

    # Alerta 60 días (entre 31 y 60 días restantes): vence el 05/01/2027 -> 46 días
    ev_60 = evaluar_vigencia_conocimiento("05/01/2025", hoy=hoy)
    assert ev_60["estado"] == "POR_VENCER_60"
    assert ev_60["estado_alerta"] == "POR_VENCER_60"

    # Alerta 30 días (entre 0 y 30 días restantes): vence el 10/12/2026 -> 20 días
    ev_30 = evaluar_vigencia_conocimiento("10/12/2024", hoy=hoy)
    assert ev_30["estado"] == "POR_VENCER_30"
    assert ev_30["estado_alerta"] == "POR_VENCER_30"

    # Vencido (< 0 días): vence el 19/11/2026
    ev_venc = evaluar_vigencia_conocimiento("19/11/2024", hoy=hoy)
    assert ev_venc["estado"] == "VENCIDO"
    assert ev_venc["estado_alerta"] == "VENCIDO"

    # Ayudante: suprime alerta visual aunque esté vencido
    ev_ayu = evaluar_vigencia_conocimiento("19/11/2024", hoy=hoy, suprimir_alertas=True)
    assert ev_ayu["estado"] == "VENCIDO"
    assert ev_ayu["estado_alerta"] == "SIN_ALERTA"


def test_filtro_personal_conduccion_y_ayudantes():
    assert es_personal_conduccion("Conductor Eléctrico")
    assert es_personal_conduccion("Conductor Diesel")
    assert es_personal_conduccion("Ayudante Habilitado")
    assert es_personal_conduccion("Ayudante Conductor")
    assert not es_personal_conduccion("Guardatren Electrico")
    assert not es_personal_conduccion("Guarda Diesel")

    assert es_ayudante_conduccion("Ayudante Conductor")
    assert es_ayudante_conduccion("Ayudante Habilitado")
    assert not es_ayudante_conduccion("Conductor Eléctrico")


def test_grafo_sectores_sentido_asc_desc_desde_pc_km0():
    sectores = [
        {"id": i + 1, "activo": 1, **s}
        for i, s in enumerate(SECTORES_VIA_BASE)
    ]

    # PC -> GW se aleja de PC (km 0) => ASC
    ida = resolver_sectores_recorrido("PC", "GW", sectores)
    assert [(p["codigo"], p["sentido"]) for p in ida] == [
        ("PC-TY", "ASC"),
        ("TY-BO", "ASC"),
        ("BO-GW", "ASC"),
    ]

    # GW -> PC regresa hacia PC => DESC
    vuelta = resolver_sectores_recorrido("GW", "PC", sectores)
    assert [(p["codigo"], p["sentido"]) for p in vuelta] == [
        ("BO-GW", "DESC"),
        ("TY-BO", "DESC"),
        ("PC-TY", "DESC"),
    ]

    # Tren Universitario: La Plata -> Medicina => LP-Univ (ASC)
    univ = resolver_sectores_recorrido("La Plata", "Medicina", sectores)
    assert [(p["codigo"], p["sentido"]) for p in univ] == [("LP-Univ", "ASC")]

    # Desambiguación por paradas intermedias: PC -> VE pasando por BQ y GT
    via_bq = resolver_sectores_recorrido("PC", "VE", sectores, paradas_intermedias=["BZ", "BQ", "GT"])
    assert [(p["codigo"], p["sentido"]) for p in via_bq] == [
        ("PC-BZ", "ASC"),
        ("BZ-BQ", "ASC"),
        ("BQ-GT", "ASC"),
        ("GT-VE", "ASC"),
    ]


def test_flujo_completo_bd_sectores_matriz_autocompletar_y_excel(tmp_path: Path):
    db = DatabaseManager(tmp_path / "via_test.sqlite")
    init_database_schema(db)
    records = RecordsService(db)
    excel = ExcelService(db)

    # 1. Semilla de los 19 sectores iniciales
    sectores = records.listar_sectores_via()
    assert len(sectores) == 19
    codigos = [s["codigo"] for s in sectores]
    assert "PC-TY" in codigos and "LP-Univ" in codigos and "ZZ-CÑ" in codigos

    # 2. Edición y alta de sector
    sec_nuevo_id = records.guardar_sector_via(
        {"codigo": "AK-CHC", "origen_sigla": "AK", "destino_sigla": "CHC", "descripcion": "Korn - Chascomús"}
    )
    assert sec_nuevo_id > 0
    assert len(records.listar_sectores_via()) == 20
    records.toggle_sector_via(sec_nuevo_id, False)
    assert len(records.listar_sectores_via(incluir_inactivos=False)) == 19

    # 3. Cargar empleados (Conductor, Ayudante y Guardatren) y turno 401.1 con tren 3555 (PC->GW) y 3556 (GW->PC)
    with db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO empleados
               (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario, jornada_horas, activo, actualizado_en)
               VALUES
               (1001, 'Perez Juan', 'Conductor Electrico', 'TY', '401.1', 'Domingo', '', 6, 1, '2026-11-01 00:00:00'),
               (1002, 'Gomez Luis', 'Ayudante Conductor', 'TY', '401.1', 'Lunes', '', 6, 1, '2026-11-01 00:00:00'),
               (1003, 'Diaz Ana', 'Guardatren Electrico', 'TY', '401.1', 'Martes', '', 7, 1, '2026-11-01 00:00:00')"""
        )
        cur = conn.execute(
            """INSERT INTO turnos_itinerario
               (codigo_turno, regimen, turno_base, subindice, tipo_turno, deposito_base, itinerario_vigente, activo)
               VALUES ('401.1', 'H', '401', 1, 'H', 'TY', '45', 1)"""
        )
        tid = cur.lastrowid
        conn.execute(
            """INSERT INTO turnos_vueltas (turno_id, nro_vuelta, nro_tren, origen, hora_salida, destino, hora_llegada)
               VALUES (?, 1, '3555', 'PC', '06:00', 'GW', '06:50'),
                      (?, 2, '3556', 'GW', '07:05', 'PC', '07:55')""",
            (tid, tid),
        )

    # 4. Previsualizar y aplicar autocompletado por turno para el conductor 1001
    prev = records.previsualizar_conocimientos_por_turno(1001)
    pares_detectados = {(p["codigo"], p["sentido"]) for p in prev["propuestas"]}
    assert pares_detectados == {
        ("PC-TY", "ASC"), ("TY-BO", "ASC"), ("BO-GW", "ASC"),
        ("PC-TY", "DESC"), ("TY-BO", "DESC"), ("BO-GW", "DESC"),
    }

    aplicados = records.aplicar_conocimientos_por_turno(
        1001, prev["propuestas"], "20/11/2026", turno_referencia="401.1"
    )
    assert aplicados == 6

    # 5. Cargar conocimiento vencido para el ayudante 1002 y verificar que NO genera alertas
    sec_pc_ty = [s for s in sectores if s["codigo"] == "PC-TY"][0]
    records.guardar_conocimiento_via(1002, sec_pc_ty["id"], ["ASC", "DESC"], "01/01/2020")

    matriz = records.obtener_matriz_conocimiento_via(hoy=date(2026, 11, 20))
    legajos_matriz = [f["legajo"] for f in matriz["filas"]]
    assert legajos_matriz == [1002, 1001]  # Guardatren 1003 excluido
    assert matriz["resumen"]["vencidos"] == 0  # Ayudante 1002 no dispara alerta de vencido
    assert matriz["resumen"]["vigentes"] == 6

    # 6. Exportación a Excel
    xlsx_out = excel.export_conocimiento_via(str(tmp_path / "conocimiento.xlsx"), matriz["sectores"], matriz["filas"])
    assert Path(xlsx_out).exists() and Path(xlsx_out).stat().st_size > 1000
