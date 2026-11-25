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


def test_formatear_observacion_turno_default_y_personalizada():
    from src.domain.conocimiento_via import PLANTILLA_OBS_TURNO_DEFAULT, formatear_observacion_turno

    obs_def = formatear_observacion_turno(itinerario="45", turno="401.1")
    assert obs_def == "Se da como conocida ya que en el itinerario 45 el personal tenía el turno 401.1."

    obs_custom = formatear_observacion_turno(
        itinerario="46",
        turno="402.2",
        regimen="H",
        trenes="Tren 3555 (PC → GW)",
        plantilla="Itin {itinerario} | Turno {turno} ({regimen}) - {trenes}",
    )
    assert obs_custom == "Itin 46 | Turno 402.2 (H) - Tren 3555 (PC → GW)"
    assert "{itinerario}" in PLANTILLA_OBS_TURNO_DEFAULT and "{turno}" in PLANTILLA_OBS_TURNO_DEFAULT


def test_calculo_masivo_observaciones_proteccion_fechas_sync_y_auditoria(tmp_path: Path):
    db = DatabaseManager(tmp_path / "via_masivo.sqlite")
    init_database_schema(db)
    records = RecordsService(db)
    sectores = records.listar_sectores_via()
    sec_pc_ty = [s for s in sectores if s["codigo"] == "PC-TY"][0]

    with db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO empleados
               (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, franco_horario, jornada_horas, activo, actualizado_en)
               VALUES
               (2001, 'Conductor Uno', 'Conductor Electrico', 'TY', '401.1', 'Domingo', '', 6, 1, '2026-11-01 00:00:00'),
               (2002, 'Ayudante Dos', 'Ayudante Conductor', 'TY', '401.1', 'Lunes', '', 6, 1, '2026-11-01 00:00:00'),
               (2003, 'Sin Turno Tres', 'Conductor Diesel', 'TY', '', 'Martes', '', 6, 1, '2026-11-01 00:00:00'),
               (2004, 'Turno Inexistente', 'Conductor Diesel', 'TY', '999.9', 'Miércoles', '', 6, 1, '2026-11-01 00:00:00'),
               (2005, 'Guardatren Excluido', 'Guardatren Electrico', 'TY', '401.1', 'Jueves', '', 7, 1, '2026-11-01 00:00:00')"""
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

    # Registrar un conocimiento más reciente en PC-TY ASC para el 2001 (debe quedar protegido)
    records.guardar_conocimiento_via(
        2001, sec_pc_ty["id"], ["ASC"], "25/11/2026", origen_registro="manual", observaciones="Examen manual"
    )

    prev = records.previsualizar_conocimientos_masivo(
        itinerario="45",
        fecha_conocimiento="20/11/2026",
        solo_si_mas_reciente=True,
    )
    assert prev["total_personal"] == 4  # Guardatren 2005 excluido
    assert prev["agentes_listos"] == 2  # 2001 y 2002
    assert prev["agentes_omitidos"] == 2  # 2003 (SIN_TURNO) y 2004 (TURNO_NO_ENCONTRADO)
    assert prev["total_conocimientos_a_aplicar"] == 11  # 5 para 2001 (1 protegido) + 6 para 2002
    assert prev["total_omitidos_fecha_reciente"] == 1

    por_leg = {r["legajo"]: r for r in prev["registros"]}
    assert por_leg[2003]["estado"] == "SIN_TURNO"
    assert por_leg[2004]["estado"] == "TURNO_NO_ENCONTRADO"
    assert por_leg[2001]["estado"] == "LISTO"
    assert (
        por_leg[2001]["observacion_ejemplo"]
        == "Se da como conocida ya que en el itinerario 45 el personal tenía el turno 401.1."
    )

    listos = [r for r in prev["registros"] if r["estado"] == "LISTO"]
    res_ap = records.aplicar_conocimientos_masivo(
        registros_seleccionados=listos,
        fecha_conocimiento="20/11/2026",
        solo_si_mas_reciente=True,
        user_id=None,
        windows_user="tester",
    )
    assert res_ap["agentes_actualizados"] == 2
    assert res_ap["conocimientos_guardados"] == 11
    assert res_ap["omitidos_fecha_reciente"] == 0

    with db.read_connection() as conn:
        # PC-TY ASC del 2001 mantuvo su fecha más reciente y observación manual
        row_prot = conn.execute(
            "SELECT fecha_conocimiento, origen_registro, observaciones FROM via_conocimientos "
            "WHERE legajo = 2001 AND sector_id = ? AND sentido = 'ASC'",
            (sec_pc_ty["id"],),
        ).fetchone()
        assert row_prot["fecha_conocimiento"] == "2026-11-25"
        assert row_prot["origen_registro"] == "manual"
        assert row_prot["observaciones"] == "Examen manual"

        # PC-TY DESC del 2001 se registró por cálculo masivo con la observación descriptiva
        row_mas = conn.execute(
            "SELECT fecha_conocimiento, origen_registro, turno_referencia, observaciones FROM via_conocimientos "
            "WHERE legajo = 2001 AND sector_id = ? AND sentido = 'DESC'",
            (sec_pc_ty["id"],),
        ).fetchone()
        assert row_mas["fecha_conocimiento"] == "2026-11-20"
        assert row_mas["origen_registro"] == "turno_masivo"
        assert row_mas["turno_referencia"] == "401.1"
        assert (
            row_mas["observaciones"]
            == "Se da como conocida ya que en el itinerario 45 el personal tenía el turno 401.1."
        )

        # Verificación de sync_outbox y auditoría
        outbox_cnt = conn.execute(
            "SELECT COUNT(*) FROM sync_outbox WHERE tabla = 'via_conocimientos' AND operacion = 'UPSERT'"
        ).fetchone()[0]
        assert outbox_cnt >= 11

        aud = conn.execute(
            "SELECT accion, entidad FROM auditoria WHERE accion = 'autocompletar_masivo'"
        ).fetchone()
        assert aud is not None and aud["entidad"] == "via_conocimiento"

    # Si se desactiva la protección de fecha más reciente, debe sobrescribir PC-TY ASC del 2001
    prev_forzado = records.previsualizar_conocimientos_masivo(
        itinerario="45",
        fecha_conocimiento="20/11/2026",
        solo_si_mas_reciente=False,
    )
    assert prev_forzado["total_conocimientos_a_aplicar"] == 12


def test_rbac_y_dialogo_conocimiento_via_masivo_ui(tmp_path: Path):
    from PySide6.QtWidgets import QApplication

    from src.domain.enums import PERMISOS_BASE, PERMISOS_METADATA
    from src.services.auth_service import AuthService
    from src.ui.views.conocimiento_via_view import ConocimientoViaView
    from src.ui.views.dialogs.conocimiento_via_dialogs import AutocompletarMasivoViaDialog

    app = QApplication.instance() or QApplication([])
    assert app is not None

    assert "conocimiento_via.autocompletar_masivo" in PERMISOS_BASE
    assert "conocimiento_via.autocompletar_masivo" in PERMISOS_METADATA

    db = DatabaseManager(tmp_path / "via_ui_rbac.sqlite")
    init_database_schema(db)
    auth = AuthService(db)
    auth.inicializar_permisos(PERMISOS_BASE)
    records = RecordsService(db)
    excel = ExcelService(db)

    # 1. Fail-closed sin función de permisos
    view_none = ConocimientoViaView(records, excel, usuario_actual={}, tiene_permiso_fn=None)
    assert not view_none.btn_editar.isEnabled()
    assert not view_none.btn_autocompletar.isEnabled()
    assert not view_none.btn_autocompletar_masivo.isEnabled()
    assert not view_none.btn_sectores.isEnabled()
    view_none.close()

    # 2. Rol con conocimiento_via.editar pero SIN conocimiento_via.autocompletar_masivo
    view_solo_editar = ConocimientoViaView(
        records,
        excel,
        usuario_actual={"id": 2, "username": "op"},
        tiene_permiso_fn=lambda p: p in {"conocimiento_via.ver", "conocimiento_via.editar"},
    )
    assert view_solo_editar.btn_editar.isEnabled()
    assert view_solo_editar.btn_autocompletar.isEnabled()
    assert not view_solo_editar.btn_autocompletar_masivo.isEnabled()
    view_solo_editar.close()

    # 3. Rol CON conocimiento_via.autocompletar_masivo
    view_masivo = ConocimientoViaView(
        records,
        excel,
        usuario_actual={"id": 1, "username": "sup"},
        tiene_permiso_fn=lambda p: p in {"conocimiento_via.ver", "conocimiento_via.autocompletar_masivo"},
    )
    assert view_masivo.btn_autocompletar_masivo.isEnabled()
    assert not view_masivo.btn_editar.isEnabled()
    view_masivo.close()

    # 4. Instanciación del diálogo masivo con valores por defecto
    dlg = AutocompletarMasivoViaDialog(records, usuario_actual={"id": 1, "username": "sup"})
    assert dlg.chk_no_sobrescribir.isChecked()
    assert "{itinerario}" in dlg.txt_plantilla_obs.text()
    assert "{turno}" in dlg.txt_plantilla_obs.text()
    dlg.close()

