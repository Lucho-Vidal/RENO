"""Pruebas unitarias e integración del Modo Anillo, Puenteo (Bridge) y Failover Automático (SMB ↔ Supabase)."""

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from src.data.database import DatabaseManager
from src.data.migrations import init_database_schema
from src.data.sync.outbox_queue import OutboxQueue
from src.data.sync.supabase_sync import SupabaseSyncEngine
from src.services.records_service import RecordsService
from src.services.shared_sqlite_sync import SharedSQLiteSyncEngine
from src.services.sync_service import (
    CIRCUIT_BREAKER_FALLOS,
    MODO_ANILLO,
    MODO_SHARED_SQLITE,
    MODO_SUPABASE,
    SyncService,
)
from src.ui.views.dialogs.sincronizacion_config_dialog import SincronizacionConfigDialog


def _get_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _crear_db(path: Path) -> DatabaseManager:
    db = DatabaseManager(path, journal_mode="WAL")
    init_database_schema(db)
    return db


def _encolar_dotacion(db: DatabaseManager, nombre: str) -> None:
    now = db.now()
    with db.write_transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dotaciones (nombre, activo, sync_status, updated_at) VALUES (?, 1, 'pending', ?);",
            (nombre, now),
        )
        payload = json.dumps({"nombre": nombre, "activo": 1, "updated_at": now}, ensure_ascii=False)
        conn.execute(
            """INSERT INTO sync_outbox (tabla, registro_pk, operacion, payload_json, creado_en, synced_smb, synced_supabase)
               VALUES ('dotaciones', ?, 'UPSERT', ?, ?, 0, 0)
               ON CONFLICT(tabla, registro_pk) DO UPDATE SET
                   payload_json=excluded.payload_json, creado_en=excluded.creado_en,
                   synced_smb=0, synced_supabase=0;""",
            (nombre, payload, now),
        )


def test_anillo_completo_entrega_a_ambos_y_purga_outbox(tmp_path: Path, monkeypatch):
    db_local = _crear_db(tmp_path / "pc_puente.sqlite")
    smb_path = tmp_path / "shared_smb.sqlite"
    _encolar_dotacion(db_local, "DOT_ANILLO_1")

    subidos_supabase: list[dict] = []

    def fake_http(config, url, method="GET", data=None, headers=None, timeout_sec=15):
        if method == "POST" and "reno_dotaciones" in url:
            body_json = json.loads(data.decode("utf-8"))
            subidos_supabase.extend(body_json)
            return 201, b"[]", {}
        if method == "GET":
            return 200, b"[]", {}
        return 200, b"[]", {}

    cfg = {
        "sync_mode": MODO_ANILLO,
        "sync_ring_primary": MODO_SHARED_SQLITE,
        "shared_sqlite_path": str(smb_path),
        "supabase_url": "https://demo.supabase.co",
        "supabase_anon_key": "sb_publishable_demo",
    }
    monkeypatch.setattr("src.data.sync.supabase_sync.ejecutar_request_http", fake_http)
    monkeypatch.setattr("src.data.sync.supabase_sync.cargar_configuracion_app", lambda: dict(cfg))

    svc = SyncService(db_local, config_provider=lambda: dict(cfg))
    res = svc.sincronizar_pendientes()

    assert res["ok"] is True
    assert res["conectado"] is True
    assert res["degradado"] is False
    assert res["smb_ok"] is True
    assert res["supabase_ok"] is True
    assert svc.contar_pendientes() == 0

    # Verificar que llegó tanto a SMB como a Supabase
    assert any(r.get("nombre") == "DOT_ANILLO_1" for r in subidos_supabase)
    db_smb = DatabaseManager(smb_path, journal_mode="DELETE")
    with db_smb.read_connection() as conn:
        row = conn.execute("SELECT nombre FROM dotaciones WHERE nombre='DOT_ANILLO_1'").fetchone()
    assert row is not None


def test_anillo_efecto_puente_pc_solo_smb_hacia_supabase_sin_bucle(tmp_path: Path, monkeypatch):
    """PC A (solo SMB) escribe en SMB -> PC C (Puente) hace pull de SMB y propaga SOLO a Supabase."""
    smb_path = tmp_path / "shared_ring.sqlite"

    # 1. PC A (sin internet, solo SMB) crea una dotación y la sube a SMB
    db_pc_a = _crear_db(tmp_path / "pc_a.sqlite")
    _encolar_dotacion(db_pc_a, "DOT_DESDE_PC_A")
    smb_engine_a = SharedSQLiteSyncEngine(db_pc_a, shared_path=smb_path)
    assert smb_engine_a.push_changes() >= 1

    # 2. PC C (Nodo Puente con SMB + Supabase) sincroniza en Modo Anillo
    db_pc_c = _crear_db(tmp_path / "pc_c.sqlite")
    subidos_a_nube: list[dict] = []

    def fake_http(config, url, method="GET", data=None, headers=None, timeout_sec=15):
        if method == "POST" and "reno_dotaciones" in url:
            subidos_a_nube.extend(json.loads(data.decode("utf-8")))
            return 201, b"[]", {}
        return 200, b"[]", {}

    cfg_c = {
        "sync_mode": MODO_ANILLO,
        "sync_ring_primary": MODO_SHARED_SQLITE,
        "shared_sqlite_path": str(smb_path),
        "supabase_url": "https://demo.supabase.co",
        "supabase_anon_key": "sb_publishable_demo",
    }
    monkeypatch.setattr("src.data.sync.supabase_sync.ejecutar_request_http", fake_http)
    monkeypatch.setattr("src.data.sync.supabase_sync.cargar_configuracion_app", lambda: dict(cfg_c))

    svc_c = SyncService(db_pc_c, config_provider=lambda: dict(cfg_c))
    res = svc_c.sincronizar_pendientes()

    assert res["ok"] is True
    assert res["smb_ok"] is True
    assert res["supabase_ok"] is True
    # La dotación de PC A llegó a PC C y se puenteó automáticamente a Supabase
    assert any(r.get("nombre") == "DOT_DESDE_PC_A" for r in subidos_a_nube)
    # Y la cola quedó purgada (cero bucles pendientes)
    assert svc_c.contar_pendientes() == 0


def test_anillo_failover_bloqueo_supabase_y_circuit_breaker(tmp_path: Path, monkeypatch):
    """Si la empresa bloquea Supabase, SMB sigue operando, outbox guarda pendientes para Supabase y se activa circuit breaker."""
    db_local = _crear_db(tmp_path / "pc_failover.sqlite")
    smb_path = tmp_path / "shared_failover.sqlite"
    _encolar_dotacion(db_local, "DOT_CON_NUBE_CAIDA")

    llamadas_nube = {"count": 0, "online": False}
    subidos_nube: list[dict] = []

    def fake_http(config, url, method="GET", data=None, headers=None, timeout_sec=15):
        llamadas_nube["count"] += 1
        if not llamadas_nube["online"]:
            raise ConnectionError("Supabase bloqueado por firewall corporativo")
        if method == "POST" and "reno_dotaciones" in url:
            subidos_nube.extend(json.loads(data.decode("utf-8")))
            return 201, b"[]", {}
        return 200, b"[]", {}

    cfg = {
        "sync_mode": MODO_ANILLO,
        "sync_ring_primary": MODO_SHARED_SQLITE,
        "shared_sqlite_path": str(smb_path),
        "supabase_url": "https://demo.supabase.co",
        "supabase_anon_key": "sb_publishable_demo",
    }
    monkeypatch.setattr("src.data.sync.supabase_sync.ejecutar_request_http", fake_http)
    monkeypatch.setattr("src.data.sync.supabase_sync.cargar_configuracion_app", lambda: dict(cfg))

    svc = SyncService(db_local, config_provider=lambda: dict(cfg))

    # 1. Primer intento: SMB OK, Supabase caído -> degradado=True, SMB al día, Supabase con 1 pendiente
    res1 = svc.sincronizar_pendientes()
    assert res1["ok"] is True
    assert res1["degradado"] is True
    assert res1["smb_ok"] is True
    assert res1["supabase_ok"] is False
    assert svc.contar_pendientes("smb") == 0
    assert svc.contar_pendientes("supabase") == 1

    # 2. Completar 3 fallos seguidos para activar circuit breaker en Supabase
    for _ in range(CIRCUIT_BREAKER_FALLOS - 1):
        svc.sincronizar_pendientes()

    llamadas_antes_cooldown = llamadas_nube["count"]
    res_cd = svc.sincronizar_pendientes()
    assert res_cd["smb_ok"] is True
    assert res_cd["supabase_ok"] is False
    # En cooldown no debe haber disparado nuevas peticiones HTTP a Supabase
    assert llamadas_nube["count"] == llamadas_antes_cooldown

    # 3. Cuando vuelve Supabase y el usuario dispara sync manual (ignorar_cooldown=True), se sube lo pendiente y se purga
    llamadas_nube["online"] = True
    res_rec = svc.sincronizar_pendientes(ignorar_cooldown=True)
    assert res_rec["ok"] is True
    assert res_rec["degradado"] is False
    assert res_rec["supabase_ok"] is True
    assert any(r.get("nombre") == "DOT_CON_NUBE_CAIDA" for r in subidos_nube)
    assert svc.contar_pendientes() == 0


def test_anillo_con_un_solo_canal_configurado(tmp_path: Path):
    """Un puesto en Modo Anillo que solo tiene SMB (sin Supabase) sincroniza sin error y purga outbox."""
    db_local = _crear_db(tmp_path / "pc_solo_smb.sqlite")
    smb_path = tmp_path / "shared_only.sqlite"
    _encolar_dotacion(db_local, "DOT_SOLO_RED")

    cfg = {
        "sync_mode": MODO_ANILLO,
        "shared_sqlite_path": str(smb_path),
        "supabase_url": "",
        "supabase_anon_key": "",
    }
    svc = SyncService(db_local, config_provider=lambda: dict(cfg))
    res = svc.sincronizar_pendientes()

    assert res["ok"] is True
    assert res["degradado"] is False
    assert res["smb_ok"] is True
    assert svc.contar_pendientes() == 0


def test_dialogo_config_modo_anillo_ui(tmp_path: Path, monkeypatch):
    _get_app()
    cfg_mock = {
        "sync_mode": MODO_ANILLO,
        "sync_ring_primary": MODO_SUPABASE,
        "shared_sqlite_path": str(tmp_path / "shared.sqlite"),
        "supabase_url": "https://demo.supabase.co",
        "supabase_anon_key": "sb_publishable_demo",
        "sync_auto_enabled": True,
        "sync_interval_sec": 30,
    }
    monkeypatch.setattr("src.ui.views.dialogs.sincronizacion_config_dialog.cargar_configuracion_app", lambda: dict(cfg_mock))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)

    dlg = SincronizacionConfigDialog()
    assert dlg.rb_ring.isChecked() is True
    assert dlg._modo_seleccionado() == MODO_ANILLO
    assert dlg.combo_ring_primary.currentData() == MODO_SUPABASE
    assert dlg._validar_antes_de_guardar() is True

    # Permitir guardar con un solo canal (solo SMB)
    dlg.txt_supa_url.clear()
    dlg.txt_supa_key.clear()
    assert dlg._validar_antes_de_guardar() is True

    # Rechazar si ambos canales quedan vacíos en Modo Anillo
    dlg.txt_shared_path.clear()
    assert dlg._validar_antes_de_guardar() is False
    dlg.close()
