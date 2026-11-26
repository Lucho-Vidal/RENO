"""Pruebas de integración para rol, dotación, unicidad ampliada y normalización tolerante en turnos_itinerario."""

import sqlite3
from pathlib import Path

from openpyxl import Workbook

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.domain.validators import (
    coincide_deposito_empleado,
    coincide_rol_empleado,
    normalizar_clave_turno,
    normalizar_deposito_turno,
    normalizar_rol_turno,
    resolver_jornada_itinerario,
)
from src.services.records_service import RecordsService


def test_normalizadores_dominio():
    # Roles (Eléctrico vs Diesel indistinto)
    assert normalizar_rol_turno("Conductor Eléctrico") == "Conductor"
    assert normalizar_rol_turno("Conductor Diesel") == "Conductor"
    assert normalizar_rol_turno("COND") == "Conductor"
    assert normalizar_rol_turno("Guarda Eléctrico") == "Guarda"
    assert normalizar_rol_turno("Guarda Diesel") == "Guarda"
    assert normalizar_rol_turno("GD") == "Guarda"
    assert normalizar_rol_turno("") == "TODOS"

    # Claves de turno tolerantes
    assert normalizar_clave_turno("Programa 01") == "PROGRAMA 1"
    assert normalizar_clave_turno("Programa 1") == "PROGRAMA 1"
    assert normalizar_clave_turno("Prog.1") == "PROGRAMA 1"
    assert normalizar_clave_turno("Prog,1") == "PROGRAMA 1"
    assert normalizar_clave_turno("Prog.1CPC") == "PROGRAMA 1"
    assert normalizar_clave_turno("Prog,1CSPC") == "PROGRAMA 1"
    assert normalizar_clave_turno("401,1") == "401.1"
    assert normalizar_clave_turno("401.1") == "401.1"

    # Depósitos
    assert normalizar_deposito_turno("K5") == "KM5"
    assert normalizar_deposito_turno("KM5") == "KM5"
    assert normalizar_deposito_turno("Plaza Constitución") == "PC"
    assert normalizar_deposito_turno("Temperley") == "TY"

    # Coincidencia rol y depósito
    assert coincide_rol_empleado("Conductor", "Conductor Eléctrico") is True
    assert coincide_rol_empleado("Conductor", "Conductor Diesel") is True
    assert coincide_rol_empleado("Conductor", "Guarda Eléctrico") is False
    assert coincide_rol_empleado("Guarda", "Guarda Diesel") is True
    assert coincide_rol_empleado("TODOS", "Guarda Eléctrico") is True

    assert coincide_deposito_empleado("KM5", "K5") is True
    assert coincide_deposito_empleado("PC", "LLV") is False


def test_migracion_sqlite_legacy_a_clave_5_columnas(tmp_path: Path):
    db_file = tmp_path / "legacy_reno.sqlite"
    # Crear esquema legacy de turnos_itinerario sin columna rol y con UNIQUE(codigo_turno, regimen, itinerario_vigente)
    conn = sqlite3.connect(db_file)
    conn.execute(
        """
        CREATE TABLE turnos_itinerario (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo_turno TEXT NOT NULL,
            regimen TEXT NOT NULL DEFAULT 'TODOS',
            turno_base TEXT NOT NULL,
            subindice INTEGER,
            tipo_turno TEXT NOT NULL,
            deposito_base TEXT NOT NULL,
            toma_servicio TEXT,
            deje_servicio TEXT,
            duracion_jornada TEXT,
            tiempo_toma TEXT,
            tiempo_deje TEXT,
            itinerario_vigente TEXT NOT NULL,
            observaciones_turno TEXT,
            activo INTEGER NOT NULL DEFAULT 1,
            sync_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(codigo_turno, regimen, itinerario_vigente)
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE turnos_vueltas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            turno_id INTEGER NOT NULL REFERENCES turnos_itinerario(id) ON DELETE CASCADE,
            nro_vuelta INTEGER NOT NULL,
            ref TEXT,
            nro_tren TEXT,
            origen TEXT,
            hora_salida TEXT,
            destino TEXT,
            hora_llegada TEXT,
            observaciones_tramo TEXT,
            sync_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.execute(
        """INSERT INTO turnos_itinerario
           (codigo_turno, regimen, turno_base, tipo_turno, deposito_base, toma_servicio, deje_servicio, itinerario_vigente)
           VALUES ('Programa 01', 'H', 'Programa 01', 'PROGRAMA', 'PC', '05:00', '11:00', 'ITI5')"""
    )
    conn.execute(
        """INSERT INTO turnos_vueltas (turno_id, nro_vuelta, nro_tren, origen, hora_salida, destino, hora_llegada)
           VALUES (1, 1, '1001', 'PC', '05:30', 'TY', '06:00')"""
    )
    conn.commit()
    conn.close()

    db = DatabaseManager(db_file, journal_mode="WAL")
    init_database_schema(db)

    with db.read_connection() as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(turnos_itinerario)").fetchall()}
        assert "rol" in cols
        row = c.execute("SELECT codigo_turno, rol, deposito_base FROM turnos_itinerario WHERE id = 1").fetchone()
        assert row["codigo_turno"] == "Programa 01"
        assert row["rol"] == "TODOS"
        vueltas = c.execute("SELECT COUNT(*) FROM turnos_vueltas WHERE turno_id = 1").fetchone()[0]
        assert vueltas == 1

    # Verificar que ahora permite insertar homónimos con distinto rol o depósito
    svc = RecordsService(db)
    id_cond = svc.crear_turno_itinerario(
        {
            "codigo_turno": "Programa 01",
            "regimen": "H",
            "deposito_base": "PC",
            "rol": "Conductor",
            "tipo_turno": "PROGRAMA",
            "itinerario_vigente": "ITI5",
        }
    )
    id_gd = svc.crear_turno_itinerario(
        {
            "codigo_turno": "Programa 01",
            "regimen": "H",
            "deposito_base": "PC",
            "rol": "Guarda",
            "tipo_turno": "PROGRAMA",
            "itinerario_vigente": "ITI5",
        }
    )
    assert id_cond != id_gd


def test_import_reno_sin_colisiones_y_cruce_tolerante_con_empleados(tmp_path: Path):
    db_file = tmp_path / "reno_test.sqlite"
    db = DatabaseManager(db_file, journal_mode="WAL")
    init_database_schema(db)
    svc = RecordsService(db)

    # Crear Excel con el nuevo formato RENO exportado por Matrices-de-turnos
    excel_reno = tmp_path / "Indice_RENO_1.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    ws.append([
        "turno_normalizado", "rol", "dotacion", "dia", "toma", "deja", "vuelta", "ref",
        "tren", "origen", "sale", "destino", "llega", "observaciones",
        "nombre_itinerario", "nombre_hoja", "nombre_archivo", "turno",
    ])
    # 4 turnos homónimos 'Programa 01' que NO deben pisarse
    ws.append(["Programa 01", "Conductor", "PC", "H", "05:00", "11:00", 1, "A", "1001", "PC", "05:30", "TY", "06:00", "", "ITI5", "PC COND LU-VI", "CondElecH.xlsx", "Prog,1CPC"])
    ws.append(["Programa 01", "Guarda", "PC", "H", "05:05", "11:05", 1, "B", "1001", "PC", "05:30", "TY", "06:00", "", "ITI5", "PC GD LU-VI", "GdElecH.xlsx", "Prog,1GPC"])
    ws.append(["Programa 01", "Conductor", "LLV", "H", "06:00", "12:00", 1, "C", "2001", "LLV", "06:20", "PC", "07:00", "", "ITI5", "LLV COND LU-VI", "CondDieselH.xlsx", "Prog,1CLLV"])
    ws.append(["Programa 01", "Conductor", "PC", "S", "07:00", "13:00", 1, "D", "3001", "PC", "07:30", "LP", "08:45", "", "ITI5", "PC COND SAB", "CondElecS.xlsx", "Prog,1CSPC"])
    wb.save(excel_reno)

    turnos_imp, vueltas_imp = svc.importar_itinerario_excel(str(excel_reno))
    assert turnos_imp == 4
    assert vueltas_imp == 4

    # Cargar empleados con distintas grafías del turno y especialidades Eléctrico/Diesel
    with db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO empleados (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, activo, actualizado_en)
               VALUES (1001, 'PEREZ JUAN', 'Conductor Eléctrico', 'PC', 'Programa 01', 'Domingo', 1, '2026-11-23 00:00:00')"""
        )
        conn.execute(
            """INSERT INTO empleados (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, activo, actualizado_en)
               VALUES (1002, 'GOMEZ CARLOS', 'Guarda Eléctrico', 'PC', 'Programa 1', 'Domingo', 1, '2026-11-23 00:00:00')"""
        )
        conn.execute(
            """INSERT INTO empleados (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, activo, actualizado_en)
               VALUES (1003, 'LOPEZ MARIO', 'Conductor Diesel', 'LLV', 'Prog.1', 'Domingo', 1, '2026-11-23 00:00:00')"""
        )

    # Buscar usando abreviatura 'Prog.1' en día Hábil (lunes 2026-11-23)
    resultados = svc.buscar_turnos_con_cobertura(
        criterio="Prog.1",
        fecha_target_iso="2026-11-23",
        tipo_dia="Día Hábil (Lunes a Viernes)",
    )
    assert len(resultados) == 3

    por_clave = {(r["deposito_base"], r["rol"]): r for r in resultados}
    assert "PEREZ JUAN" in por_clave[("PC", "Conductor")]["titular"]
    assert "GOMEZ CARLOS" in por_clave[("PC", "Guarda")]["titular"]
    assert "LOPEZ MARIO" in por_clave[("LLV", "Conductor")]["titular"]

    # Filtrar solo Guardas
    solo_guardas = svc.buscar_turnos_con_cobertura(
        criterio="Programa 1",
        fecha_target_iso="2026-11-23",
        tipo_dia="H",
        rol="Guarda",
    )
    assert len(solo_guardas) == 1
    assert solo_guardas[0]["rol"] == "Guarda"
    assert "GOMEZ CARLOS" in solo_guardas[0]["titular"]

    # obtener_turno_por_codigo respeta depósito y rol y normalización tolerante
    t_llv = svc.obtener_turno_por_codigo("Prog.1", regimen="H", deposito="LLV", rol="Conductor Diesel")
    assert t_llv is not None
    assert t_llv["deposito_base"] == "LLV"
    assert t_llv["rol"] == "Conductor"
    assert t_llv["toma_servicio"] == "06:00"

    # listar_turnos_itinerario_map + resolver_jornada_itinerario
    mapa_gd = svc.listar_turnos_itinerario_map(regimen="H", deposito="PC", rol="Guarda Eléctrico")
    res_j = resolver_jornada_itinerario("Prog.1", "2026-11-23", "Domingo", mapa_gd)
    assert res_j["toma"] == "05:05"
    assert res_j["deposito"] == "PC"


def test_import_excel_legacy_retrocompatible(tmp_path: Path):
    db_file = tmp_path / "reno_legacy_import.sqlite"
    db = DatabaseManager(db_file, journal_mode="WAL")
    init_database_schema(db)
    svc = RecordsService(db)

    excel_legacy = tmp_path / "Indice_legacy.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Datos"
    ws.append([
        "turno", "dia", "toma", "deja", "vuelta", "ref",
        "tren", "origen", "sale", "destino", "llega", "observaciones",
        "nombre_itinerario", "nombre_hoja", "nombre_archivo",
    ])
    ws.append(["Prog,1CPC", "H", "05:00", "11:00", 1, "A", "1001", "PC", "05:30", "TY", "06:00", "", "ITI5", "PC COND LU-VI", "Tur.COND.xlsx"])
    wb.save(excel_legacy)

    t_cnt, v_cnt = svc.importar_itinerario_excel(str(excel_legacy))
    assert t_cnt == 1
    assert v_cnt == 1
    turnos = svc.listar_turnos_admin()
    assert len(turnos) == 1
    assert turnos[0]["deposito_base"] == "PC"
    assert turnos[0]["rol"] == "Conductor"
    assert turnos[0]["regimen"] == "H"


def test_conocimiento_via_no_mezcla_turnos_homonimos(tmp_path: Path):
    db_file = tmp_path / "reno_cv.sqlite"
    db = DatabaseManager(db_file, journal_mode="WAL")
    init_database_schema(db)
    svc = RecordsService(db)

    # Dos turnos 'Programa 01' con recorridos distintos: PC Conductor (PC->TY) y LLV Conductor (EZ->PC)
    svc.crear_turno_itinerario(
        {
            "codigo_turno": "Programa 01",
            "regimen": "H",
            "deposito_base": "PC",
            "rol": "Conductor",
            "tipo_turno": "PROGRAMA",
            "itinerario_vigente": "ITI5",
        },
        vueltas=[{"nro_vuelta": 1, "nro_tren": "1001", "origen": "PC", "hora_salida": "05:30", "destino": "TY", "hora_llegada": "06:00"}],
    )
    svc.crear_turno_itinerario(
        {
            "codigo_turno": "Programa 01",
            "regimen": "H",
            "deposito_base": "PC",
            "rol": "Guarda",
            "tipo_turno": "PROGRAMA",
            "itinerario_vigente": "ITI5",
        },
        vueltas=[{"nro_vuelta": 1, "nro_tren": "9999", "origen": "PC", "hora_salida": "05:30", "destino": "LP", "hora_llegada": "07:00"}],
    )

    with db.write_transaction() as conn:
        conn.execute(
            """INSERT INTO empleados (legajo, apellidos_nombres, especialidad, dotacion, turnos, franco, activo, actualizado_en)
               VALUES (5001, 'DIAZ LUCAS', 'Conductor Eléctrico', 'PC', 'Programa 1', 'Domingo', 1, '2026-11-23 00:00:00')"""
        )

    prev = svc.previsualizar_conocimientos_por_turno(legajo=5001)
    assert len(prev["turnos_encontrados"]) == 1
    assert prev["turnos_encontrados"][0]["rol"] == "Conductor"
    assert [v["nro_tren"] for v in prev["vueltas"]] == ["1001"]
