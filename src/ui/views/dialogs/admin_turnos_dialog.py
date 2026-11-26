"""Panel y Diálogo de Administración de Turnos de Itinerario con roles y permisos (CRONO-PCT Standard)."""

from typing import Any, Dict, List, Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from src.core.logger import get_logger
from src.services.records_service import RecordsService
from src.ui.models.base_table_model import BaseTableModel
from src.ui.views.dialogs.base_dialog import BaseMaximizedDialog
from src.ui.views.dialogs.turno_dialog import TurnoDialog

logger = get_logger("AdminTurnos")


class AdminTurnosWidget(QWidget):
    """Panel de administración y CRUD completo de turnos de itinerario con selección múltiple y borrado por filtros."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        tiene_permiso_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.usuario_actual = usuario_actual or {}
        self.tiene_permiso_fn = tiene_permiso_fn

        self._turnos: List[Dict[str, Any]] = []
        self.hubo_cambios: bool = False
        self._init_ui()
        self.recargar()

    def _can(self, permission_code: str) -> bool:
        if self.tiene_permiso_fn is None:
            return True
        return bool(self.tiene_permiso_fn(permission_code))

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # Fila 1: Acciones CRUD, borrado múltiple/por filtro e importación Excel
        bar_top = QHBoxLayout()

        self.btn_nuevo = QPushButton("➕ Nuevo Turno")
        self.btn_nuevo.setObjectName("btnPrimary")
        self.btn_nuevo.clicked.connect(self._nuevo_turno)
        bar_top.addWidget(self.btn_nuevo)

        self.btn_modificar = QPushButton("✏️ Modificar Turno")
        self.btn_modificar.setObjectName("btnSecondary")
        self.btn_modificar.clicked.connect(self._modificar_turno)
        bar_top.addWidget(self.btn_modificar)

        self.btn_toggle = QPushButton("🔄 Activar / Desactivar")
        self.btn_toggle.setObjectName("btnSecondary")
        self.btn_toggle.clicked.connect(self._toggle_estado)
        bar_top.addWidget(self.btn_toggle)

        self.btn_eliminar = QPushButton("🗑️ Eliminar Seleccionados")
        self.btn_eliminar.setObjectName("btnDanger")
        self.btn_eliminar.setToolTip("Elimina uno o múltiples turnos seleccionados de la tabla.")
        self.btn_eliminar.clicked.connect(self._eliminar_definitivo)
        bar_top.addWidget(self.btn_eliminar)

        self.btn_eliminar_todos = QPushButton("🗑️ Eliminar Todos (Filtro)")
        self.btn_eliminar_todos.setObjectName("btnDanger")
        self.btn_eliminar_todos.setToolTip(
            "Elimina permanentemente todos los turnos que coincidan con los filtros actuales (Itinerario, Día, Depósito, Búsqueda)."
        )
        self.btn_eliminar_todos.clicked.connect(self._eliminar_todos_filtrados)
        bar_top.addWidget(self.btn_eliminar_todos)

        bar_top.addStretch()

        self.btn_importar_turnos = QPushButton("📥 Importar Turnos")
        self.btn_importar_turnos.setObjectName("btnSecondary")
        self.btn_importar_turnos.clicked.connect(self._importar_turnos)
        bar_top.addWidget(self.btn_importar_turnos)

        layout.addLayout(bar_top)

        # Fila 2: Filtros por Itinerario, Día (H/S/D), Depósito, Búsqueda e Inactivos
        bar_filters = QHBoxLayout()

        bar_filters.addWidget(QLabel("Itinerario:"))
        self.combo_itinerario = QComboBox()
        self.combo_itinerario.setMinimumWidth(140)
        self.combo_itinerario.currentIndexChanged.connect(lambda _: self.recargar())
        bar_filters.addWidget(self.combo_itinerario)

        bar_filters.addWidget(QLabel("Día:"))
        self.combo_regimen = QComboBox()
        self.combo_regimen.addItem("Todos", "Todos")
        self.combo_regimen.addItem("H - Hábil", "H")
        self.combo_regimen.addItem("S - Sábado", "S")
        self.combo_regimen.addItem("D - Domingo", "D")
        self.combo_regimen.addItem("TODOS - Sin distinción", "TODOS")
        self.combo_regimen.currentIndexChanged.connect(lambda _: self.recargar())
        bar_filters.addWidget(self.combo_regimen)

        bar_filters.addWidget(QLabel("Rol:"))
        self.combo_rol = QComboBox()
        self.combo_rol.addItem("Todos", "Todos")
        self.combo_rol.addItem("Conductor", "Conductor")
        self.combo_rol.addItem("Guarda", "Guarda")
        self.combo_rol.addItem("TODOS - Sin distinción", "TODOS")
        self.combo_rol.currentIndexChanged.connect(lambda _: self.recargar())
        bar_filters.addWidget(self.combo_rol)

        bar_filters.addWidget(QLabel("Depósito:"))
        self.combo_deposito = QComboBox()
        self.combo_deposito.setMinimumWidth(95)
        self.combo_deposito.currentIndexChanged.connect(lambda _: self.recargar())
        bar_filters.addWidget(self.combo_deposito)

        bar_filters.addWidget(QLabel("Buscar:"))
        self.txt_buscar = QLineEdit()
        self.txt_buscar.setPlaceholderText("Código, base, observaciones...")
        self.txt_buscar.setClearButtonEnabled(True)
        self.txt_buscar.textChanged.connect(lambda _: self.recargar())
        bar_filters.addWidget(self.txt_buscar, stretch=1)

        self.chk_inactivos = QCheckBox("Incluir Inactivos")
        self.chk_inactivos.setChecked(True)
        self.chk_inactivos.stateChanged.connect(lambda _: self.recargar())
        bar_filters.addWidget(self.chk_inactivos)

        self.btn_limpiar_filtros = QPushButton("Limpiar Filtros")
        self.btn_limpiar_filtros.setObjectName("btnSecondary")
        self.btn_limpiar_filtros.clicked.connect(self._limpiar_filtros)
        bar_filters.addWidget(self.btn_limpiar_filtros)

        layout.addLayout(bar_filters)

        # Tabla de Turnos con selección múltiple (Ctrl+Clic, Shift+Clic, Ctrl+A)
        headers = [
            "ID", "CÓDIGO TURNO", "DÍA", "ROL", "TURNO BASE", "SUB", "TIPO TURNO", "DEPÓSITO",
            "TOMA", "DEJE", "DURACIÓN", "VUELTAS", "ITIN.", "ESTADO",
        ]
        keys = [
            "id", "codigo_turno", "regimen_label", "rol", "turno_base", "subindice", "tipo_turno", "deposito_base",
            "toma_servicio", "deje_servicio", "duracion_jornada", "cant_vueltas", "itinerario_vigente", "estado_str",
        ]

        self.model = BaseTableModel(headers, keys, parent=self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(0, 55)   # ID
        self.table.setColumnWidth(1, 105)  # CÓDIGO TURNO
        self.table.setColumnWidth(2, 95)   # DÍA
        self.table.setColumnWidth(3, 85)   # ROL
        self.table.setColumnWidth(4, 85)   # TURNO BASE
        self.table.setColumnWidth(5, 48)   # SUB
        self.table.setColumnWidth(6, 115)  # TIPO TURNO
        self.table.setColumnWidth(7, 75)   # DEPÓSITO
        self.table.setColumnWidth(8, 65)   # TOMA
        self.table.setColumnWidth(9, 65)   # DEJE
        self.table.setColumnWidth(10, 75)  # DURACIÓN
        self.table.setColumnWidth(11, 68)  # VUELTAS
        self.table.setColumnWidth(12, 95)  # ITIN
        self.table.horizontalHeader().setSectionResizeMode(13, QHeaderView.Stretch)  # ESTADO
        self.table.doubleClicked.connect(lambda _: self._modificar_turno())
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._actualizar_conteo())
        layout.addWidget(self.table)

        # Pie con conteo y selección actual
        self.lbl_conteo = QLabel("Mostrando 0 turnos")
        self.lbl_conteo.setStyleSheet("color: #6B7280; font-size: 8.5pt;")
        layout.addWidget(self.lbl_conteo)

        self._sincronizar_combos_filtro(inicial=True)

    def _sincronizar_combos_filtro(self, inicial: bool = False) -> None:
        """Actualiza opciones de Itinerario y Depósito conservando selección vigente si aún existe."""
        prev_iti = "Todos" if inicial else self.combo_itinerario.currentText()
        prev_dep = "Todos" if inicial else self.combo_deposito.currentText()

        itis = self.records_service.listar_itinerarios_turnos(incluir_inactivos=True)
        self.combo_itinerario.blockSignals(True)
        self.combo_itinerario.clear()
        self.combo_itinerario.addItem("Todos")
        self.combo_itinerario.addItems(itis)
        idx_i = self.combo_itinerario.findText(prev_iti)
        self.combo_itinerario.setCurrentIndex(idx_i if idx_i >= 0 else 0)
        self.combo_itinerario.blockSignals(False)

        deps = self.records_service.listar_depositos_turnos() or ["PC", "TY", "LLV"]
        self.combo_deposito.blockSignals(True)
        self.combo_deposito.clear()
        self.combo_deposito.addItem("Todos")
        self.combo_deposito.addItems(deps)
        idx_d = self.combo_deposito.findText(prev_dep)
        self.combo_deposito.setCurrentIndex(idx_d if idx_d >= 0 else 0)
        self.combo_deposito.blockSignals(False)

    def _limpiar_filtros(self) -> None:
        for w in (self.combo_itinerario, self.combo_regimen, self.combo_rol, self.combo_deposito, self.txt_buscar, self.chk_inactivos):
            w.blockSignals(True)
        self.combo_itinerario.setCurrentIndex(0)
        self.combo_regimen.setCurrentIndex(0)
        self.combo_rol.setCurrentIndex(0)
        self.combo_deposito.setCurrentIndex(0)
        self.txt_buscar.clear()
        self.chk_inactivos.setChecked(True)
        for w in (self.combo_itinerario, self.combo_regimen, self.combo_rol, self.combo_deposito, self.txt_buscar, self.chk_inactivos):
            w.blockSignals(False)
        self.recargar()

    def _filtros_actuales(self) -> Dict[str, Any]:
        iti = self.combo_itinerario.currentText().strip()
        reg = self.combo_regimen.currentData()
        rol = self.combo_rol.currentData()
        dep = self.combo_deposito.currentText().strip()
        return {
            "criterio": self.txt_buscar.text().strip(),
            "deposito": None if dep in ("", "Todos", "Todas") else dep,
            "itinerario": None if iti in ("", "Todos", "Todas") else iti,
            "regimen": None if reg in (None, "", "Todos", "Todas") else str(reg),
            "rol": None if rol in (None, "", "Todos", "Todas") else str(rol),
            "incluir_inactivos": self.chk_inactivos.isChecked(),
        }

    def recargar(self) -> None:
        self._sincronizar_combos_filtro(inicial=False)
        filtros = self._filtros_actuales()

        self._turnos = self.records_service.listar_turnos_admin(
            criterio=filtros["criterio"],
            deposito=filtros["deposito"],
            itinerario=filtros["itinerario"],
            regimen=filtros["regimen"],
            rol=filtros["rol"],
            incluir_inactivos=filtros["incluir_inactivos"],
        )
        for t in self._turnos:
            t["estado_str"] = "Activo" if t.get("activo") else "Inactivo"
            if t.get("subindice") is None:
                t["subindice"] = "-"
            if not t.get("regimen_label"):
                t["regimen_label"] = self.records_service.formatear_regimen_turno(t.get("regimen"))
        self.model.set_data(self._turnos)
        self._actualizar_conteo()

    def _actualizar_conteo(self) -> None:
        total = len(self._turnos)
        sel_rows = len(self._obtener_seleccionados())
        if sel_rows > 0:
            self.lbl_conteo.setText(
                f"Mostrando {total} turnos de itinerario | {sel_rows} seleccionado(s)"
            )
        else:
            self.lbl_conteo.setText(f"Mostrando {total} turnos de itinerario")

    def _obtener_seleccionados(self) -> List[Dict[str, Any]]:
        sel_model = self.table.selectionModel()
        if not sel_model:
            return []
        filas = []
        for idx in sorted(sel_model.selectedRows(), key=lambda i: i.row()):
            row_data = self.model.get_row(idx.row())
            if row_data:
                filas.append(row_data)
        return filas

    def _obtener_seleccionado(self) -> Optional[Dict[str, Any]]:
        sel = self._obtener_seleccionados()
        return sel[0] if sel else None

    def _nuevo_turno(self) -> None:
        if not (self._can("turnos.crear") or self._can("turnos.administrar")):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permisos para dar de alta turnos de itinerario.")
            return

        dlg = TurnoDialog(
            self.records_service,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        if dlg.exec():
            self.hubo_cambios = True
            self.recargar()

    def _modificar_turno(self) -> None:
        if not (self._can("turnos.editar") or self._can("turnos.administrar")):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permisos para modificar turnos de itinerario.")
            return

        sels = self._obtener_seleccionados()
        if not sels:
            QMessageBox.information(self, "Selección", "Seleccione un turno de la tabla para modificar.")
            return
        if len(sels) > 1:
            QMessageBox.information(self, "Selección", "Seleccione un único turno para modificar su diagrama.")
            return
        item = sels[0]

        dlg = TurnoDialog(
            self.records_service,
            turno_id=item["id"],
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        if dlg.exec():
            self.hubo_cambios = True
            self.recargar()

    def _toggle_estado(self) -> None:
        if not (self._can("turnos.editar") or self._can("turnos.administrar")):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permisos para cambiar el estado del turno.")
            return

        sels = self._obtener_seleccionados()
        if not sels:
            QMessageBox.information(self, "Selección", "Seleccione al menos un turno de la tabla.")
            return

        u_id = self.usuario_actual.get("id")
        win_u = self.usuario_actual.get("username")
        try:
            if len(sels) == 1:
                item = sels[0]
                nuevo_act = 0 if item.get("activo") else 1
                self.records_service.cambiar_estado_turno(
                    item["id"],
                    activo=nuevo_act,
                    user_id=u_id,
                    windows_user=win_u,
                )
            else:
                self.records_service.cambiar_estado_turnos_lote(
                    [int(it["id"]) for it in sels],
                    activo=None,
                    user_id=u_id,
                    windows_user=win_u,
                )
            self.hubo_cambios = True
            self.recargar()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo cambiar el estado de los turnos:\n{e}")

    def _eliminar_definitivo(self) -> None:
        if not (self._can("turnos.eliminar") or self._can("turnos.administrar")):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permisos para eliminar turnos de itinerario.")
            return

        sels = self._obtener_seleccionados()
        if not sels:
            QMessageBox.information(self, "Selección", "Seleccione al menos un turno de la tabla para eliminar.")
            return

        if len(sels) == 1:
            it0 = sels[0]
            msg = (
                f"¿Está seguro de eliminar permanentemente el turno '{it0.get('codigo_turno')}' "
                f"({it0.get('regimen_label', '')} - Itin. {it0.get('itinerario_vigente', '')}) "
                "y todas sus vueltas asociadas?\n\nEsta acción no se puede deshacer."
            )
        else:
            muestra = ", ".join(str(x.get("codigo_turno") or "") for x in sels[:8])
            if len(sels) > 8:
                muestra += f", ... (+{len(sels) - 8} más)"
            msg = (
                f"¿Está seguro de eliminar permanentemente los {len(sels)} turnos seleccionados "
                f"({muestra}) y todas sus vueltas asociadas?\n\nEsta acción no se puede deshacer."
            )

        resp = QMessageBox.warning(
            self,
            "Confirmación de Eliminación",
            msg,
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        u_id = self.usuario_actual.get("id")
        win_u = self.usuario_actual.get("username")
        try:
            ids = [int(it["id"]) for it in sels]
            if len(ids) == 1:
                self.records_service.eliminar_turno_definitivo(
                    ids[0],
                    user_id=u_id,
                    windows_user=win_u,
                )
            else:
                self.records_service.eliminar_turnos_lote(
                    ids,
                    user_id=u_id,
                    windows_user=win_u,
                )
            self.hubo_cambios = True
            self.recargar()
        except Exception as e:
            QMessageBox.critical(self, "Error al Eliminar", f"No se pudieron eliminar los turnos:\n{e}")

    def _eliminar_todos_filtrados(self) -> None:
        if not (self._can("turnos.eliminar") or self._can("turnos.administrar")):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permisos para eliminar turnos de itinerario.")
            return

        filtros = self._filtros_actuales()
        total = self.records_service.contar_turnos_por_filtros(**filtros)
        if total == 0:
            QMessageBox.information(
                self,
                "Sin Turnos",
                "No existen turnos que coincidan con los filtros seleccionados para eliminar.",
            )
            return

        detalles_filtro = []
        if filtros["itinerario"]:
            detalles_filtro.append(f"• Itinerario: {filtros['itinerario']}")
        if filtros["regimen"]:
            detalles_filtro.append(f"• Día: {self.records_service.formatear_regimen_turno(filtros['regimen'])}")
        if filtros["rol"]:
            detalles_filtro.append(f"• Rol: {filtros['rol']}")
        if filtros["deposito"]:
            detalles_filtro.append(f"• Depósito: {filtros['deposito']}")
        if filtros["criterio"]:
            detalles_filtro.append(f"• Búsqueda: '{filtros['criterio']}'")
        if not filtros["incluir_inactivos"]:
            detalles_filtro.append("• Estado: Solo activos")

        resumen_txt = "\n".join(detalles_filtro) if detalles_filtro else "• SIN FILTROS (Se eliminará TODO el catálogo de turnos)"

        resp = QMessageBox.warning(
            self,
            "Eliminar Todos los Turnos Filtrados",
            (
                f"Se eliminarán permanentemente {total} turno(s) y todas sus vueltas asociadas "
                f"según los filtros aplicados:\n\n{resumen_txt}\n\n"
                "Esta acción no se puede deshacer. ¿Desea continuar?"
            ),
            QMessageBox.Yes | QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return

        u_id = self.usuario_actual.get("id")
        win_u = self.usuario_actual.get("username")
        try:
            eliminados = self.records_service.eliminar_turnos_por_filtros(
                user_id=u_id,
                windows_user=win_u,
                **filtros,
            )
            self.hubo_cambios = True
            self.recargar()
            QMessageBox.information(
                self,
                "Eliminación Completada",
                f"Se eliminaron {eliminados} turno(s) de itinerario correctamente.",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Eliminar", f"No se pudieron eliminar los turnos filtrados:\n{e}")

    def _importar_turnos(self) -> None:
        if not (self._can("turnos.crear") or self._can("turnos.administrar")):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permisos para importar turnos de itinerario.")
            return

        f_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar Archivo de Turnos e Itinerario (Excel)",
            "",
            "Archivos Excel (*.xlsx *.xlsm)",
        )
        if not f_path:
            return

        try:
            u_id = self.usuario_actual.get("id")
            win_u = self.usuario_actual.get("username")
            turnos, vueltas = self.records_service.importar_itinerario_excel(
                f_path,
                user_id=u_id,
                windows_user=win_u,
            )
            self.hubo_cambios = True
            self.recargar()
            QMessageBox.information(
                self,
                "Turnos Importados",
                f"Se importaron con éxito {turnos} turnos y {vueltas} vueltas desde:\n{f_path}",
            )
        except Exception as e:
            QMessageBox.critical(self, "Error al Importar", f"No se pudo procesar el archivo:\n{e}")


class AdminTurnosDialog(BaseMaximizedDialog):
    """Diálogo modal maximizado para administración de turnos con permisos y roles."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        tiene_permiso_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Administración de Turnos de Itinerario")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)

        self.widget = AdminTurnosWidget(
            records_service,
            usuario_actual=usuario_actual,
            tiene_permiso_fn=tiene_permiso_fn,
            parent=self,
        )
        layout.addWidget(self.widget)

        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("btnSecondary")
        btn_cerrar.clicked.connect(self.accept)
        layout.addWidget(btn_cerrar, alignment=Qt.AlignRight)

    def recargar(self) -> None:
        self.widget.recargar()
