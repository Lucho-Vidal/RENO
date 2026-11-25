"""Diálogo de configuración de sincronización multimodo (Solo Local / Carpeta Compartida SMB / Supabase)."""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.core.config import cargar_configuracion_app, guardar_configuracion_app
from src.services.shared_sqlite_sync import SharedSQLiteSyncEngine
from src.services.sync_service import (
    MODO_ANILLO,
    MODO_LOCAL,
    MODO_SHARED_SQLITE,
    MODO_SUPABASE,
    SyncService,
)
from src.ui.views.dialogs.base_dialog import BaseDialog


class SincronizacionConfigDialog(BaseDialog):
    """Ventana de configuración de sincronización Local-First, SMB/CIFS, Supabase y Modo Anillo."""

    configuracion_guardada = Signal(dict)

    def __init__(
        self,
        sync_service: Optional[SyncService] = None,
        on_trigger_sync: Optional[Callable[[bool], None]] = None,
        on_abrir_supabase_avanzado: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, maximizable=True)
        self.sync_service = sync_service
        self._on_trigger_sync = on_trigger_sync
        self._on_abrir_supabase_avanzado = on_abrir_supabase_avanzado

        self.setWindowTitle("Configuración de Sincronización — RENO")
        self.resize(780, 620)
        self.setMinimumSize(680, 500)

        self._init_ui()
        self._cargar_config()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        lbl_titulo = QLabel("🔄 Modo de Sincronización de Base de Datos (Local-First)")
        lbl_titulo.setStyleSheet("font-size: 14pt; font-weight: bold; color: #1E3A8A;")
        layout.addWidget(lbl_titulo)

        lbl_sub = QLabel(
            "La aplicación siempre opera de forma inmediata sobre su SQLite local (WAL). "
            "La sincronización se ejecuta en segundo plano sin bloquear la interfaz."
        )
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet("color: #475569; font-size: 9.5pt;")
        layout.addWidget(lbl_sub)

        # 1. Selector visual de modo
        grp_modo = QGroupBox("1. Seleccionar Destino de Sincronización")
        lay_modo = QVBoxLayout(grp_modo)
        lay_modo.setSpacing(8)

        self.bg_modo = QButtonGroup(self)
        self.rb_local = QRadioButton("🏠 Solo Local (Sin sincronización externa)")
        self.rb_shared = QRadioButton("📁 Carpeta Compartida en Red (SMB/CIFS / Ruta UNC / Unidad Mapeada)")
        self.rb_supabase = QRadioButton("☁️ Nube (Supabase PostgreSQL)")
        self.rb_ring = QRadioButton("🔁 Modo Anillo / Failover Automático (Red Compartida ↔ Supabase)")

        for rb in (self.rb_local, self.rb_shared, self.rb_supabase, self.rb_ring):
            rb.setStyleSheet("font-weight: 600;")
            self.bg_modo.addButton(rb)
            lay_modo.addWidget(rb)
            rb.toggled.connect(self._actualizar_visibilidad_paneles)

        # Sub-panel de prioridad del anillo
        self.widget_ring_opts = QWidget()
        lay_ring = QVBoxLayout(self.widget_ring_opts)
        lay_ring.setContentsMargins(20, 4, 0, 4)
        lay_ring.setSpacing(6)
        lbl_ring_info = QLabel(
            "Sincroniza contra ambos canales y hace failover automático si uno cae o está bloqueado. "
            "Si este puesto solo dispone de uno de los dos canales, dejá el otro vacío y operará igual."
        )
        lbl_ring_info.setWordWrap(True)
        lbl_ring_info.setStyleSheet("color: #0369A1; font-size: 9pt;")
        lay_ring.addWidget(lbl_ring_info)

        fila_prio = QHBoxLayout()
        fila_prio.addWidget(QLabel("Canal primario:"))
        self.combo_ring_primary = QComboBox()
        self.combo_ring_primary.addItem("📁 Red Compartida primero (LAN → Nube)", MODO_SHARED_SQLITE)
        self.combo_ring_primary.addItem("☁️ Supabase primero (Nube → LAN)", MODO_SUPABASE)
        fila_prio.addWidget(self.combo_ring_primary)
        fila_prio.addStretch(1)
        lay_ring.addLayout(fila_prio)
        lay_modo.addWidget(self.widget_ring_opts)

        layout.addWidget(grp_modo)

        # 2. Panel Carpeta Compartida SMB/CIFS
        self.grp_shared = QGroupBox("2. Base de Datos Compartida en Red (Segura: DELETE + FULL)")
        lay_shared = QVBoxLayout(self.grp_shared)
        lay_shared.setSpacing(10)

        lbl_info_smb = QLabel(
            "Las conexiones a la base compartida fuerzan <b>journal_mode = DELETE</b>, "
            "<b>synchronous = FULL</b> y transacciones atómicas efímeras para prevenir corrupción en SMB/CIFS."
        )
        lbl_info_smb.setWordWrap(True)
        lbl_info_smb.setStyleSheet("color: #1E40AF; font-size: 9pt;")
        lay_shared.addWidget(lbl_info_smb)

        fila_ruta = QHBoxLayout()
        self.txt_shared_path = QLineEdit()
        self.txt_shared_path.setPlaceholderText(r"Ej: \\servidor\compartido\reno_shared.sqlite o Z:\RENO\reno_shared.sqlite")
        self.btn_examinar_shared = QPushButton("📂 Examinar...")
        self.btn_examinar_shared.setObjectName("btnSecondary")
        self.btn_examinar_shared.setMinimumHeight(34)
        self.btn_examinar_shared.clicked.connect(self._examinar_ruta_compartida)
        fila_ruta.addWidget(self.txt_shared_path, 1)
        fila_ruta.addWidget(self.btn_examinar_shared)
        lay_shared.addLayout(fila_ruta)

        fila_test = QHBoxLayout()
        self.btn_probar_red = QPushButton("🔌 Probar Conexión y Permisos de Red")
        self.btn_probar_red.setObjectName("btnSecondary")
        self.btn_probar_red.setMinimumHeight(36)
        self.btn_probar_red.clicked.connect(self._probar_conexion_red)
        fila_test.addWidget(self.btn_probar_red)
        fila_test.addStretch(1)
        lay_shared.addLayout(fila_test)

        self.lbl_diagnostico = QLabel("")
        self.lbl_diagnostico.setWordWrap(True)
        self.lbl_diagnostico.setVisible(False)
        lay_shared.addWidget(self.lbl_diagnostico)

        layout.addWidget(self.grp_shared)

        # 3. Panel Supabase
        self.grp_supabase = QGroupBox("2. Configuración Rápida de Supabase")
        lay_supa = QVBoxLayout(self.grp_supabase)
        form_supa = QFormLayout()
        self.txt_supa_url = QLineEdit()
        self.txt_supa_url.setPlaceholderText("https://xxxx.supabase.co")
        self.txt_supa_key = QLineEdit()
        self.txt_supa_key.setEchoMode(QLineEdit.Password)
        self.txt_supa_key.setPlaceholderText("sb_publishable_...")
        form_supa.addRow("Supabase URL:", self.txt_supa_url)
        form_supa.addRow("Supabase Key:", self.txt_supa_key)
        lay_supa.addLayout(form_supa)

        if self._on_abrir_supabase_avanzado is not None:
            btn_avanzado = QPushButton("⚙️ Abrir Parámetros Avanzados y Proxy Supabase...")
            btn_avanzado.setObjectName("btnSecondary")
            btn_avanzado.setMinimumHeight(34)
            btn_avanzado.clicked.connect(self._abrir_supabase_avanzado)
            lay_supa.addWidget(btn_avanzado)

        layout.addWidget(self.grp_supabase)

        # 4. Programación y acciones
        grp_prog = QGroupBox("3. Sincronización Automática en Segundo Plano")
        lay_prog = QFormLayout(grp_prog)
        self.chk_auto_sync = QCheckBox("Activar sincronización periódica automática en segundo plano")
        lay_prog.addRow(self.chk_auto_sync)

        self.spin_intervalo_seg = QSpinBox()
        self.spin_intervalo_seg.setRange(15, 600)
        self.spin_intervalo_seg.setValue(45)
        self.spin_intervalo_seg.setSuffix(" seg")
        self.spin_intervalo_seg.setFixedWidth(120)
        lay_prog.addRow("Intervalo de sincronización:", self.spin_intervalo_seg)
        layout.addWidget(grp_prog)

        layout.addStretch(1)

        # Botonera inferior
        botones = QHBoxLayout()
        self.btn_sync_todo = QPushButton("🔄 Sincronizar Todo Ahora")
        self.btn_sync_todo.setObjectName("btnSecondary")
        self.btn_sync_todo.setMinimumHeight(38)
        self.btn_sync_todo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_sync_todo.clicked.connect(self._sincronizar_todo_ahora)
        botones.addWidget(self.btn_sync_todo)

        botones.addStretch(1)

        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setObjectName("btnSecondary")
        self.btn_cancelar.setMinimumHeight(38)
        self.btn_cancelar.clicked.connect(self.reject)
        botones.addWidget(self.btn_cancelar)

        self.btn_guardar = QPushButton("💾 Guardar Configuración")
        self.btn_guardar.setMinimumHeight(38)
        self.btn_guardar.clicked.connect(self._guardar_y_cerrar)
        botones.addWidget(self.btn_guardar)

        layout.addLayout(botones)

    def _cargar_config(self) -> None:
        cfg = cargar_configuracion_app()
        modo = str(cfg.get("sync_mode") or "").strip().upper()
        if modo not in (MODO_LOCAL, MODO_SHARED_SQLITE, MODO_SUPABASE, MODO_ANILLO):
            if self.sync_service is not None:
                modo = self.sync_service.get_mode()
            else:
                modo = MODO_SUPABASE if (cfg.get("supabase_url") and cfg.get("supabase_anon_key")) else MODO_LOCAL

        if modo == MODO_SHARED_SQLITE:
            self.rb_shared.setChecked(True)
        elif modo == MODO_SUPABASE:
            self.rb_supabase.setChecked(True)
        elif modo == MODO_ANILLO:
            self.rb_ring.setChecked(True)
        else:
            self.rb_local.setChecked(True)

        primario = str(cfg.get("sync_ring_primary") or MODO_SHARED_SQLITE).strip().upper()
        idx_prio = self.combo_ring_primary.findData(primario)
        if idx_prio >= 0:
            self.combo_ring_primary.setCurrentIndex(idx_prio)

        self.txt_shared_path.setText(str(cfg.get("shared_sqlite_path") or ""))
        self.txt_supa_url.setText(str(cfg.get("supabase_url") or ""))
        self.txt_supa_key.setText(str(cfg.get("supabase_anon_key") or ""))
        self.chk_auto_sync.setChecked(bool(cfg.get("sync_auto_enabled", True)))
        self.spin_intervalo_seg.setValue(int(cfg.get("sync_interval_sec") or 45))
        self._actualizar_visibilidad_paneles()

    def _modo_seleccionado(self) -> str:
        if self.rb_shared.isChecked():
            return MODO_SHARED_SQLITE
        if self.rb_supabase.isChecked():
            return MODO_SUPABASE
        if self.rb_ring.isChecked():
            return MODO_ANILLO
        return MODO_LOCAL

    def _actualizar_visibilidad_paneles(self) -> None:
        modo = self._modo_seleccionado()
        self.widget_ring_opts.setVisible(modo == MODO_ANILLO)
        self.grp_shared.setVisible(modo in (MODO_SHARED_SQLITE, MODO_ANILLO))
        self.grp_supabase.setVisible(modo in (MODO_SUPABASE, MODO_ANILLO))
        self.btn_sync_todo.setEnabled(modo != MODO_LOCAL)

    def _examinar_ruta_compartida(self) -> None:
        ruta_actual = self.txt_shared_path.text().strip() or "reno_shared.sqlite"
        ruta, _ = QFileDialog.getSaveFileName(
            self,
            "Seleccionar o crear base SQLite compartida en red",
            ruta_actual,
            "SQLite (*.sqlite *.sqlite3 *.db);;Todos (*.*)",
            options=QFileDialog.DontConfirmOverwrite,
        )
        if ruta:
            self.txt_shared_path.setText(ruta)

    def _probar_conexion_red(self) -> None:
        ruta = self.txt_shared_path.text().strip()
        ok, msg = SharedSQLiteSyncEngine.test_connection(ruta)
        self.lbl_diagnostico.setVisible(True)
        if ok:
            self.lbl_diagnostico.setText(f"✅ {msg}")
            self.lbl_diagnostico.setStyleSheet(
                "padding: 8px; border-radius: 4px; background-color: #DCFCE7; color: #166534; font-weight: 600;"
            )
        else:
            self.lbl_diagnostico.setText(f"❌ {msg}")
            self.lbl_diagnostico.setStyleSheet(
                "padding: 8px; border-radius: 4px; background-color: #FEE2E2; color: #991B1B; font-weight: 600;"
            )

    def _abrir_supabase_avanzado(self) -> None:
        if self._on_abrir_supabase_avanzado:
            self._on_abrir_supabase_avanzado()

    def _construir_config_actualizada(self) -> dict[str, Any]:
        cfg = cargar_configuracion_app()
        modo = self._modo_seleccionado()
        cfg["sync_mode"] = modo
        cfg["sync_ring_primary"] = str(self.combo_ring_primary.currentData() or MODO_SHARED_SQLITE)
        cfg["shared_sqlite_path"] = self.txt_shared_path.text().strip()
        cfg["supabase_url"] = self.txt_supa_url.text().strip().rstrip("/")
        cfg["supabase_anon_key"] = self.txt_supa_key.text().strip()
        cfg["sync_auto_enabled"] = self.chk_auto_sync.isChecked()
        cfg["sync_interval_sec"] = int(self.spin_intervalo_seg.value())
        cfg["autosync_habilitado"] = bool(modo in (MODO_SUPABASE, MODO_ANILLO) and cfg["sync_auto_enabled"])
        return cfg

    def _validar_antes_de_guardar(self) -> bool:
        modo = self._modo_seleccionado()
        tiene_smb = bool(self.txt_shared_path.text().strip())
        tiene_sb = bool(self.txt_supa_url.text().strip() and self.txt_supa_key.text().strip())
        if modo == MODO_SHARED_SQLITE and not tiene_smb:
            QMessageBox.warning(
                self,
                "Ruta Requerida",
                "Ingrese la ruta local o UNC de la base SQLite compartida antes de guardar.",
            )
            return False
        if modo == MODO_SUPABASE and not tiene_sb:
            QMessageBox.warning(
                self,
                "Credenciales Requeridas",
                "Ingrese tanto la URL como la Key de Supabase antes de guardar.",
            )
            return False
        if modo == MODO_ANILLO and not (tiene_smb or tiene_sb):
            QMessageBox.warning(
                self,
                "Destino Requerido",
                "En Modo Anillo debe configurar al menos un destino (Carpeta Compartida SMB o Supabase).",
            )
            return False
        return True

    def _sincronizar_todo_ahora(self) -> None:
        if not self._validar_antes_de_guardar():
            return
        cfg = self._construir_config_actualizada()
        guardar_configuracion_app(cfg)
        self.configuracion_guardada.emit(cfg)

        if self._on_trigger_sync is not None:
            self._on_trigger_sync(True)
            QMessageBox.information(
                self,
                "Sincronización Iniciada",
                "Se inició la sincronización completa en segundo plano.\n"
                "Podés seguir operando normalmente; el estado se actualizará en la barra inferior.",
            )
        elif self.sync_service is not None:
            res = self.sync_service.sincronizar_pendientes(forzar_todo=True)
            if res.get("ok"):
                QMessageBox.information(
                    self,
                    "Sincronización Completada",
                    f"Enviados: {res.get('enviados', 0)} | Recibidos: {res.get('recibidos', 0)}",
                )
            else:
                QMessageBox.warning(
                    self,
                    "Sincronización en Modo Offline",
                    res.get("error") or res.get("mensaje") or "No se pudo conectar al destino.",
                )

    def _guardar_y_cerrar(self) -> None:
        if not self._validar_antes_de_guardar():
            return
        cfg = self._construir_config_actualizada()
        guardar_configuracion_app(cfg)
        self.configuracion_guardada.emit(cfg)
        self.accept()
