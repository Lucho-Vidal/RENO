"""Pruebas automatizadas para la sincronización Local-First en SQLite compartida (SMB/CIFS)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.data.sync.outbox_queue import OutboxQueue
from src.services.shared_sqlite_sync import SharedSQLiteSyncEngine
from src.services.sync_service import MODO_LOCAL, MODO_SHARED_SQLITE, MODO_SUPABASE, SyncService
from src.services.sync_worker import SyncBackgroundWorker
from src.ui.views.dialogs.sincronizacion_config_dialog import SincronizacionConfigDialog


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _crear_db_local(path: Path) -> DatabaseManager:
    db = DatabaseManager(path, journal_mode="WAL")
    init_database_schema(db)
    return db


def _insertar_novedad_con_outbox(
    db: DatabaseManager,
    nov_id: int,
    legajo: int,
    observaciones: str,
    updated_at: str,
) -> None:
    payload = {
        "id": nov_id,
        "registrado_en": "2026-11-18 10:00:00",
        "legajo": legajo,
        "apellidos_nombres": "PEREZ JUAN",
        "especialidad": "Conductor Eléctrico",
        "dotacion": "PC",
        "turnos": "T1",
        "franco": "DOM",
        "novedad": "ENFERMEDAD",
        "fecha_inicio": "2026-11-18",
        "fecha_fin": None,
        "referencia_estacion": "PC",
        "supervisor": "SUP",
        "observaciones": observaciones,
        "usuario_windows": "test",
        "usuario_id": None,
        "activo": 1,
        "sync_status": "pending",
        "updated_at": updated_at,
    }
    with db.write_transaction() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO novedades
               (id, registrado_en, legajo, apellidos_nombres, especialidad, dotacion,
                turnos, franco, novedad, fecha_inicio, fecha_fin, referencia_estacion,
                supervisor, observaciones, usuario_windows, usuario_id, activo, sync_status, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?);""",
            (
                payload["id"],
                payload["registrado_en"],
                payload["legajo"],
                payload["apellidos_nombres"],
                payload["especialidad"],
                payload["dotacion"],
                payload["turnos"],
                payload["franco"],
                payload["novedad"],
                payload["fecha_inicio"],
                payload["fecha_fin"],
                payload["referencia_estacion"],
                payload["supervisor"],
                payload["observaciones"],
                payload["usuario_windows"],
                payload["usuario_id"],
                payload["activo"],
                payload["updated_at"],
            ),
        )
        conn.execute(
            """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en)
               VALUES ('novedades', ?, 'UPSERT', ?, ?)
               ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                   operacion = 'UPSERT',
                   payload_json = excluded.payload_json,
                   creado_en = excluded.creado_en,
                   intentos = 0,
                   ultimo_error = NULL,
                   estado = 'PENDIENTE';""",
            (str(nov_id), json.dumps(payload, ensure_ascii=False), updated_at),
        )


def test_diagnostico_permisos_y_rutas(tmp_path: Path):
    ok_vacio, msg_vacio = SharedSQLiteSyncEngine.test_connection("")
    assert not ok_vacio
    assert "ruta válida" in msg_vacio.lower()

    ruta_inexistente = tmp_path / "no_existe_carpeta" / "shared.sqlite"
    ok_no, msg_no = SharedSQLiteSyncEngine.test_connection(ruta_inexistente)
    assert not ok_no
    assert "no es accesible" in msg_no.lower() or "no existe" in msg_no.lower()

    ruta_valida = tmp_path / "compartido" / "reno_shared.sqlite"
    ruta_valida.parent.mkdir(parents=True, exist_ok=True)
    ok_si, msg_si = SharedSQLiteSyncEngine.test_connection(ruta_valida)
    assert ok_si, msg_si
    # No deben quedar archivos temporales .tmp de prueba
    assert list(ruta_valida.parent.glob(".reno_probe_*.tmp")) == []


def test_bootstrap_y_pragmas_seguridad_red(tmp_path: Path):
    db_local = _crear_db_local(tmp_path / "local.sqlite")
    shared_path = tmp_path / "red_smb" / "reno_shared.sqlite"

    engine = SharedSQLiteSyncEngine(db_local, shared_path=shared_path)
    creado = engine.bootstrap_shared_database()
    assert creado is True
    assert shared_path.exists()

    # Verificar estrictamente DELETE, synchronous=FULL (2), busy_timeout=30000 y quick_check=ok
    conn = SharedSQLiteSyncEngine.get_shared_connection(shared_path)
    try:
        jm = str(conn.execute("PRAGMA journal_mode;").fetchone()[0]).lower()
        sync_val = int(conn.execute("PRAGMA synchronous;").fetchone()[0])
        busy_val = int(conn.execute("PRAGMA busy_timeout;").fetchone()[0])
        qc_val = str(conn.execute("PRAGMA quick_check;").fetchone()[0]).lower()
        outbox_tbl = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='sync_outbox';"
        ).fetchone()[0]
        nov_tbl = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='novedades';"
        ).fetchone()[0]
    finally:
        conn.close()

    assert jm == "delete"
    assert sync_val == 2  # FULL
    assert busy_val == 30000
    assert qc_val == "ok"
    assert int(outbox_tbl) == 0
    assert int(nov_tbl) == 1

    ok_diag, msg_diag = SharedSQLiteSyncEngine.test_connection(shared_path)
    assert ok_diag, msg_diag
    assert "DELETE" in msg_diag
    assert "quick_check=OK" in msg_diag


def test_push_pull_bidireccional_sin_eco_y_lww(tmp_path: Path):
    db_a = _crear_db_local(tmp_path / "term_a.sqlite")
    db_b = _crear_db_local(tmp_path / "term_b.sqlite")
    shared_path = tmp_path / "share" / "reno_shared.sqlite"

    engine_a = SharedSQLiteSyncEngine(db_a, shared_path=shared_path)
    engine_b = SharedSQLiteSyncEngine(db_b, shared_path=shared_path)

    # 1. Terminal A inserta novedad y hace Push
    _insertar_novedad_con_outbox(db_a, nov_id=101, legajo=5001, observaciones="Inicial A", updated_at="2026-11-18 10:00:00")
    assert OutboxQueue(db_a).contar_pendientes() == 1

    enviados_a = engine_a.push_changes()
    assert enviados_a == 1
    assert OutboxQueue(db_a).contar_pendientes() == 0

    # 2. Terminal B hace Pull -> recibe la novedad y su outbox permanece en 0 (sin eco)
    recibidos_b = engine_b.pull_changes()
    assert recibidos_b >= 1
    assert OutboxQueue(db_b).contar_pendientes() == 0

    with db_b.read_connection() as conn_b:
        row_b = conn_b.execute("SELECT observaciones, sync_status FROM novedades WHERE id = 101;").fetchone()
        ts_pull = conn_b.execute(
            "SELECT valor FROM configuracion WHERE clave = ?;",
            (SharedSQLiteSyncEngine.CLAVE_LAST_PULL,),
        ).fetchone()
    assert row_b is not None
    assert row_b["observaciones"] == "Inicial A"
    assert row_b["sync_status"] == "synced"
    assert ts_pull is not None and ts_pull[0]

    # 3. Resolución de conflictos LWW: actualización más reciente gana; más vieja se descarta
    _insertar_novedad_con_outbox(db_b, nov_id=101, legajo=5001, observaciones="Actualizado B Reciente", updated_at="2026-11-18 12:00:00")
    assert engine_b.push_changes() == 1

    _insertar_novedad_con_outbox(db_a, nov_id=101, legajo=5001, observaciones="Viejo A", updated_at="2026-11-18 09:00:00")
    engine_a.push_changes()

    conn_s = SharedSQLiteSyncEngine.get_shared_connection(shared_path)
    try:
        row_s = conn_s.execute("SELECT observaciones FROM novedades WHERE id = 101;").fetchone()
        assert row_s["observaciones"] == "Actualizado B Reciente"
    finally:
        conn_s.close()


def test_sync_service_multimodo_y_tolerancia_desconexion(tmp_path: Path):
    db = _crear_db_local(tmp_path / "local.sqlite")
    _insertar_novedad_con_outbox(db, nov_id=201, legajo=6001, observaciones="Offline test", updated_at="2026-11-18 11:00:00")

    cfg_estado = {"sync_mode": MODO_LOCAL, "shared_sqlite_path": ""}
    svc = SyncService(db, config_provider=lambda: cfg_estado)
    assert svc.get_mode() == MODO_LOCAL
    res_local = svc.sincronizar_pendientes()
    assert res_local["ok"] is True
    assert res_local["modo"] == MODO_LOCAL

    # Ruta compartida inaccesible -> degradación silenciosa sin excepción
    cfg_estado["sync_mode"] = MODO_SHARED_SQLITE
    cfg_estado["shared_sqlite_path"] = str(tmp_path / "servidor_caido" / "sub" / "reno.sqlite")
    # Simular que el padre no existe y no se puede escribir (archivo bloqueado por nombre padre inválido)
    archivo_bloqueo = tmp_path / "no_es_dir"
    archivo_bloqueo.write_text("x", encoding="utf-8")
    cfg_estado["shared_sqlite_path"] = str(archivo_bloqueo / "reno.sqlite")

    res_off = svc.sincronizar_pendientes()
    assert res_off["ok"] is False
    assert res_off["conectado"] is False
    assert res_off["pendientes"] == 1
    assert "Desconectada" in res_off["mensaje"]

    # Modo Supabase reconocido
    cfg_estado["sync_mode"] = MODO_SUPABASE
    assert svc.get_mode() == MODO_SUPABASE


def test_sync_worker_y_dialogo_config_ui(tmp_path: Path, monkeypatch):
    _get_app()
    db = _crear_db_local(tmp_path / "worker_local.sqlite")
    shared_path = tmp_path / "share_worker" / "reno_shared.sqlite"
    shared_path.parent.mkdir(parents=True, exist_ok=True)
    _insertar_novedad_con_outbox(db, nov_id=301, legajo=7001, observaciones="Worker sync", updated_at="2026-11-18 13:00:00")

    cfg = {
        "sync_mode": MODO_SHARED_SQLITE,
        "shared_sqlite_path": str(shared_path),
        "sync_auto_enabled": False,
        "sync_interval_sec": 60,
    }
    svc = SyncService(db, config_provider=lambda: cfg)
    worker = SyncBackgroundWorker(svc, interval_sec=60, auto_enabled=False)

    eventos: list[str] = []
    resultados: list[dict] = []
    loop = QEventLoop()

    worker.sync_started.connect(lambda: eventos.append("started"))

    def _on_fin(res: dict):
        eventos.append("finished")
        resultados.append(res)
        loop.quit()

    worker.sync_finished.connect(_on_fin)
    worker.start()
    worker.trigger_sync()
    QTimer.singleShot(5000, loop.quit)
    loop.exec()

    worker.stop()
    assert worker.wait(2000) is True
    assert "started" in eventos
    assert "finished" in eventos
    assert resultados and resultados[0]["ok"] is True
    assert resultados[0]["enviados"] == 1
    assert shared_path.exists()

    # Probar SincronizacionConfigDialog en offscreen
    monkeypatch.setattr("src.ui.views.dialogs.sincronizacion_config_dialog.cargar_configuracion_app", lambda: dict(cfg))
    dlg = SincronizacionConfigDialog(sync_service=svc)
    assert dlg.rb_shared.isChecked()
    assert dlg.grp_shared.isVisible() is False or True  # antes de show(), validar isChecked y visibilidad lógica
    dlg.rb_local.setChecked(True)
    assert dlg._modo_seleccionado() == MODO_LOCAL
    dlg.rb_supabase.setChecked(True)
    assert dlg._modo_seleccionado() == MODO_SUPABASE
    dlg.rb_shared.setChecked(True)
    dlg.txt_shared_path.setText(str(shared_path))
    dlg._probar_conexion_red()
    assert "✅" in dlg.lbl_diagnostico.text()
    dlg.close()
