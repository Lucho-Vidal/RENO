"""Vista especializada para el seguimiento y gestión de Personal de Ciclo.

Permite consultar personal disponible y asignado en una fecha determinada,
con soporte interactivo de arrastrar y soltar (Drag and Drop) entre ambas tablas,
y acceso al Calendario Mensual de Ciclo.
"""

from typing import Any, Dict, List, Optional
import json
import re
import unicodedata

from PySide6.QtCore import QByteArray, QDate, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDrag
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.logger import get_logger
from src.services.excel_service import ExcelService
from src.services.pdf_service import PDFService
from src.services.records_service import RecordsService
from src.ui.models.base_table_model import BaseTableModel
from src.ui.models.multi_filter_proxy import MultiFilterProxyModel
from src.ui.views.dialogs.base_dialog import BaseDialog
from src.ui.views.dialogs.calendario_ciclo_dialog import CalendarioCicloDialog

logger = get_logger("PersonalCicloView")

_ORDEN_DIAS_FRANCO = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}


def _clave_orden_franco(emp: Dict[str, Any]) -> tuple:
    """Ordena personal de ciclo disponible por día de franco (Lun-Dom) y horario de franco."""
    franco_raw = str(emp.get("franco") or "").strip()
    nfkd = unicodedata.normalize("NFKD", franco_raw)
    franco_norm = "".join(c for c in nfkd if not unicodedata.combining(c)).lower()
    dia_idx = _ORDEN_DIAS_FRANCO.get(franco_norm, 99)

    hor_raw = str(emp.get("franco_horario") or "").strip()
    m = re.search(r"(\d{1,2})\s*[:.]\s*(\d{2})", hor_raw)
    if m:
        hor_key = (0, int(m.group(1)) * 60 + int(m.group(2)), hor_raw)
    elif hor_raw:
        hor_key = (1, 9999, hor_raw.lower())
    else:
        hor_key = (2, 9999, "")

    nombre = str(emp.get("apellidos_nombres") or "").strip().lower()
    return (dia_idx, hor_key, nombre)


class DragDropCicloTableView(QTableView):
    """Tabla con soporte Drag & Drop bidireccional y reordenamiento interno para agentes de ciclo."""

    item_soltado = Signal(dict)
    fila_reordenada = Signal(int, int)

    def __init__(self, rol: str, parent=None):
        super().__init__(parent)
        self.rol = rol  # 'LIBRES' o 'OCUPADOS'
        self._drag_start_pos: Optional[QPoint] = None
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QTableView.DragDrop)
        self.setDropIndicatorShown(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or not self._drag_start_pos:
            super().mouseMoveEvent(event)
            return

        if (event.pos() - self._drag_start_pos).manhattanLength() < 10:
            super().mouseMoveEvent(event)
            return

        indexes = self.selectionModel().selectedRows()
        if not indexes:
            return

        model = self.model()
        if hasattr(model, "mapToSource") and hasattr(model, "sourceModel"):
            src_idx = model.mapToSource(indexes[0])
            src_model = model.sourceModel()
            src_row = src_idx.row()
            row_data = src_model.get_row(src_row) if hasattr(src_model, "get_row") else None
        elif hasattr(model, "get_row"):
            src_row = indexes[0].row()
            row_data = model.get_row(src_row)
        else:
            return
        if not row_data:
            return

        drag = QDrag(self)
        mime = QMimeData()
        payload = json.dumps(
            {"origen_rol": self.rol, "origen_fila": src_row, "datos": row_data}
        ).encode("utf-8")
        mime.setData("application/x-ciclo-row", QByteArray(payload))
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-ciclo-row"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-ciclo-row"):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasFormat("application/x-ciclo-row"):
            try:
                data_dict = json.loads(bytes(event.mimeData().data("application/x-ciclo-row")).decode("utf-8"))
                origen_rol = data_dict.get("origen_rol")
                if origen_rol != self.rol:
                    event.acceptProposedAction()
                    self.item_soltado.emit(data_dict.get("datos", {}))
                    return

                origen_fila = data_dict.get("origen_fila")
                if origen_fila is None:
                    event.ignore()
                    return

                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                drop_idx = self.indexAt(pos)
                model = self.model()
                if hasattr(model, "mapToSource") and hasattr(model, "sourceModel"):
                    src_model = model.sourceModel()
                    if drop_idx.isValid():
                        destino_fila = model.mapToSource(drop_idx).row()
                    else:
                        destino_fila = max(0, src_model.rowCount() - 1)
                else:
                    if drop_idx.isValid():
                        destino_fila = drop_idx.row()
                    else:
                        destino_fila = max(0, model.rowCount() - 1) if model else 0

                event.acceptProposedAction()
                if int(origen_fila) != int(destino_fila):
                    self.fila_reordenada.emit(int(origen_fila), int(destino_fila))
                return
            except Exception as e:
                logger.error("Error en dropEvent ciclo: %s", e)
        event.ignore()


class PersonalCicloView(QWidget):
    """Vista dividida en dos tablas: Personal Libre (arriba) y Personal Asignado (abajo) con Drag & Drop."""

    def __init__(
        self,
        records_service: RecordsService,
        pdf_service: PDFService,
        excel_service: ExcelService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        tiene_permiso_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.pdf_service = pdf_service
        self.excel_service = excel_service
        self.usuario_actual = usuario_actual or {}
        self.tiene_permiso_fn = tiene_permiso_fn
        self._calendarios_abiertos: List[CalendarioCicloDialog] = []
        self._orden_manual_libres: List[int] = []
        self._orden_manual_ocupados: List[int] = []

        self._init_ui()
        self.consultar()

    def _can(self, perm: str) -> bool:
        if self.tiene_permiso_fn:
            return bool(self.tiene_permiso_fn(perm))
        return False

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 1. Barra superior de filtros
        bar_frame = QFrame()
        bar_frame.setObjectName("filterBar")
        bar_layout = QHBoxLayout(bar_frame)
        bar_layout.setContentsMargins(8, 4, 8, 4)
        bar_layout.setSpacing(10)

        bar_layout.addWidget(QLabel("Fecha de Consulta:"))
        self.date_picker = QDateEdit()
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("dd/MM/yyyy")
        self.date_picker.setDate(QDate.currentDate())
        bar_layout.addWidget(self.date_picker)

        bar_layout.addWidget(QLabel("Dotación:"))
        self.combo_dotacion = QComboBox()
        self.combo_dotacion.addItem("Todas")
        dots = self.records_service.listar_dotaciones(incluir_inactivos=False)
        for d in dots:
            self.combo_dotacion.addItem(d["nombre"])
        bar_layout.addWidget(self.combo_dotacion)

        bar_layout.addWidget(QLabel("Especialidad:"))
        self.combo_esp = QComboBox()
        self.combo_esp.addItem("Todas")
        for esp in self.records_service.listar_especialidades_empleados():
            self.combo_esp.addItem(esp)
        self.combo_esp.currentIndexChanged.connect(self.consultar)
        self.combo_dotacion.currentIndexChanged.connect(self.consultar)
        bar_layout.addWidget(self.combo_esp)

        self.btn_consultar = QPushButton("🔍 Actualizar")
        self.btn_consultar.clicked.connect(self.consultar)
        bar_layout.addWidget(self.btn_consultar)

        bar_layout.addWidget(QLabel("Buscar:"))
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Legajo, nombre, franco...")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.setFixedWidth(200)
        self.txt_search.textChanged.connect(self._on_search_changed)
        bar_layout.addWidget(self.txt_search)

        bar_layout.addStretch()

        self.btn_ver_calendario = QPushButton("📅 Ver Calendario Mensual")
        self.btn_ver_calendario.setObjectName("btnSecondary")
        self.btn_ver_calendario.clicked.connect(self._abrir_calendario_seleccionado)
        bar_layout.addWidget(self.btn_ver_calendario)

        self.btn_exportar_pdf = QPushButton("📄 Exportar PDF")
        self.btn_exportar_pdf.setObjectName("btnSecondary")
        self.btn_exportar_pdf.clicked.connect(self._abrir_exportar_pdf)
        bar_layout.addWidget(self.btn_exportar_pdf)

        layout.addWidget(bar_frame)

        # 2. Splitter vertical con las dos tablas
        splitter = QSplitter(Qt.Vertical)

        # Tabla Superior: Personal Libre / Sin Relevo
        grp_libres = QGroupBox("Personal de Ciclo DISPONIBLE (Arrastre hacia abajo para asignar a un turno sin cubrir)")
        lay_libres = QVBoxLayout(grp_libres)
        headers_libres = ["LEGAJO", "APELLIDOS Y NOMBRES", "ESPECIALIDAD", "DOTACIÓN", "TURNO HABITUAL", "FRANCO", "HORARIO FRANCO"]
        keys_libres = ["legajo", "apellidos_nombres", "especialidad", "dotacion", "turnos", "franco", "franco_horario"]

        self.model_libres = BaseTableModel(headers_libres, keys_libres, parent=self)
        self.proxy_libres = MultiFilterProxyModel(self)
        self.proxy_libres.setSourceModel(self.model_libres)
        self.table_libres = DragDropCicloTableView("LIBRES", parent=self)
        self.table_libres.setModel(self.proxy_libres)
        self.table_libres.setSelectionBehavior(QTableView.SelectRows)
        self.table_libres.setSelectionMode(QTableView.SingleSelection)
        self.table_libres.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_libres.setColumnWidth(0, 80)   # LEGAJO
        self.table_libres.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)  # APELLIDOS Y NOMBRES
        self.table_libres.setColumnWidth(2, 180)  # ESPECIALIDAD (sin truncar)
        self.table_libres.setColumnWidth(3, 90)   # DOTACIÓN
        self.table_libres.setColumnWidth(4, 120)  # TURNO HABITUAL
        self.table_libres.setColumnWidth(5, 90)   # FRANCO
        self.table_libres.setColumnWidth(6, 130)  # HORARIO FRANCO
        self.table_libres.doubleClicked.connect(lambda idx: self._on_double_click(self.proxy_libres, idx))
        self.table_libres.item_soltado.connect(self._on_soltado_en_libres)
        self.table_libres.fila_reordenada.connect(self._on_reordenar_libres)
        lay_libres.addWidget(self.table_libres)
        splitter.addWidget(grp_libres)

        # Tabla Inferior: Personal Ocupado / Con Relevo o Novedad Activa
        grp_ocupados = QGroupBox("Personal de Ciclo ASIGNADO / CON NOVEDAD ACTIVA (Arrastre un relevo hacia arriba para liberarlo)")
        lay_ocupados = QVBoxLayout(grp_ocupados)
        headers_ocupados = [
            "LEGAJO", "APELLIDOS Y NOMBRES", "ESPECIALIDAD", "DOTACIÓN", "REEMPLAZA A (TITULAR)",
            "TURNO CUBIERTO", "MOTIVO / NOVEDAD", "DESDE", "HASTA",
        ]
        keys_ocupados = [
            "legajo", "apellidos_nombres", "especialidad", "dotacion", "reemplaza_a",
            "turno_titular", "tipo_novedad", "fecha_inicio_relevo", "fecha_fin_relevo",
        ]

        def _colores_ocupados(row_item: Dict[str, Any], col_idx: int):
            if row_item.get("_es_novedad_propia"):
                from src.core.theme import obtener_tema_visual
                es_oscuro = obtener_tema_visual() == "oscuro"
                return (QColor("#3D1B1B"), QColor("#FCA5A5")) if es_oscuro else (QColor("#FFEBEE"), QColor("#B71C1C"))
            return None

        self.model_ocupados = BaseTableModel(
            headers_ocupados,
            keys_ocupados,
            conditional_colors=_colores_ocupados,
            parent=self,
        )
        self.proxy_ocupados = MultiFilterProxyModel(self)
        self.proxy_ocupados.setSourceModel(self.model_ocupados)
        self.table_ocupados = DragDropCicloTableView("OCUPADOS", parent=self)
        self.table_ocupados.setModel(self.proxy_ocupados)
        self.table_ocupados.setSelectionBehavior(QTableView.SelectRows)
        self.table_ocupados.setSelectionMode(QTableView.SingleSelection)
        self.table_ocupados.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_ocupados.setColumnWidth(0, 80)   # LEGAJO
        self.table_ocupados.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)  # APELLIDOS Y NOMBRES
        self.table_ocupados.setColumnWidth(2, 180)  # ESPECIALIDAD
        self.table_ocupados.setColumnWidth(3, 90)   # DOTACIÓN
        self.table_ocupados.setColumnWidth(4, 220)  # REEMPLAZA A
        self.table_ocupados.setColumnWidth(5, 110)  # TURNO CUBIERTO
        self.table_ocupados.setColumnWidth(6, 160)  # MOTIVO / NOVEDAD
        self.table_ocupados.setColumnWidth(7, 95)   # DESDE
        self.table_ocupados.setColumnWidth(8, 95)   # HASTA
        self.table_ocupados.doubleClicked.connect(lambda idx: self._on_double_click(self.proxy_ocupados, idx))
        self.table_ocupados.item_soltado.connect(self._on_soltado_en_ocupados)
        self.table_ocupados.fila_reordenada.connect(self._on_reordenar_ocupados)
        lay_ocupados.addWidget(self.table_ocupados)
        splitter.addWidget(grp_ocupados)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        # 3. Pie con instrucciones
        lbl_hint = QLabel("💡 Arrastre y suelte (Drag & Drop) entre tablas para asignar/liberar relevos o dentro de la misma tabla para reordenar. Doble clic para abrir el Calendario Mensual.")
        lbl_hint.setStyleSheet("color: #6B7280; font-size: 8.5pt;")
        layout.addWidget(lbl_hint)

    @staticmethod
    def _aplicar_orden_manual(filas: List[Dict[str, Any]], orden_legajos: List[int]) -> List[Dict[str, Any]]:
        if not orden_legajos:
            return filas
        pos = {leg: idx for idx, leg in enumerate(orden_legajos)}
        en_orden = []
        nuevos = []
        for f in filas:
            try:
                leg = int(f.get("legajo"))
            except (TypeError, ValueError):
                leg = None
            if leg is not None and leg in pos:
                en_orden.append((pos[leg], f))
            else:
                nuevos.append(f)
        en_orden.sort(key=lambda pair: pair[0])
        return [f for _, f in en_orden] + nuevos

    def _on_reordenar_tabla(
        self,
        table: DragDropCicloTableView,
        proxy: MultiFilterProxyModel,
        model: BaseTableModel,
        from_row: int,
        to_row: int,
    ) -> List[int]:
        if model.move_row(from_row, to_row):
            src_idx = model.index(to_row, 0)
            proxy_idx = proxy.mapFromSource(src_idx)
            if proxy_idx.isValid():
                table.selectRow(proxy_idx.row())
        return [
            int(r["legajo"])
            for r in model.get_all_data()
            if r.get("legajo") is not None
        ]

    def _on_reordenar_libres(self, from_row: int, to_row: int) -> None:
        self._orden_manual_libres = self._on_reordenar_tabla(
            self.table_libres, self.proxy_libres, self.model_libres, from_row, to_row
        )

    def _on_reordenar_ocupados(self, from_row: int, to_row: int) -> None:
        self._orden_manual_ocupados = self._on_reordenar_tabla(
            self.table_ocupados, self.proxy_ocupados, self.model_ocupados, from_row, to_row
        )

    def _on_search_changed(self, text: str) -> None:
        self.proxy_libres.set_global_search(text)
        self.proxy_ocupados.set_global_search(text)

    def consultar(self) -> None:
        f_str = self.date_picker.date().toString("dd/MM/yyyy")
        dot = self.combo_dotacion.currentText()
        esp = self.combo_esp.currentText()

        try:
            res = self.records_service.obtener_estado_personal_ciclo(f_str, dotacion=dot, especialidad=esp)
            libres_ordenados = sorted(res.get("libres", []), key=_clave_orden_franco)
            libres_ordenados = self._aplicar_orden_manual(libres_ordenados, self._orden_manual_libres)
            self.model_libres.set_data(libres_ordenados)

            filas_ocupados = list(res.get("ocupados", []))
            for emp_nov in res.get("con_novedad", []):
                item_nov = dict(emp_nov)
                item_nov["_es_novedad_propia"] = True
                nov_id = emp_nov.get("novedad_propia_id", "")
                item_nov["reemplaza_a"] = f"NOVEDAD PROPIA #{nov_id}" if nov_id else "NOVEDAD PROPIA"
                item_nov["turno_titular"] = "-"
                item_nov["tipo_novedad"] = emp_nov.get("novedad_propia") or "-"
                item_nov["fecha_inicio_relevo"] = emp_nov.get("novedad_fecha_inicio") or "-"
                item_nov["fecha_fin_relevo"] = emp_nov.get("novedad_fecha_fin") or "Sin fecha fin"
                filas_ocupados.append(item_nov)

            filas_ocupados = self._aplicar_orden_manual(filas_ocupados, self._orden_manual_ocupados)
            self.model_ocupados.set_data(filas_ocupados)
        except Exception as e:
            logger.error(f"Error al consultar personal de ciclo: {e}")

    def _obtener_fila_desde_proxy(self, proxy: MultiFilterProxyModel, proxy_index) -> Optional[Dict[str, Any]]:
        if not proxy_index or not proxy_index.isValid():
            return None
        src_idx = proxy.mapToSource(proxy_index)
        src_model = proxy.sourceModel()
        if hasattr(src_model, "get_row"):
            return src_model.get_row(src_idx.row())
        return None

    def _on_double_click(self, proxy: MultiFilterProxyModel, index) -> None:
        emp = self._obtener_fila_desde_proxy(proxy, index)
        if emp:
            self._abrir_calendario(emp)

    def _abrir_calendario_seleccionado(self) -> None:
        sel_l = self.table_libres.selectionModel().selectedRows()
        if sel_l:
            emp = self._obtener_fila_desde_proxy(self.proxy_libres, sel_l[0])
            if emp:
                self._abrir_calendario(emp)
                return

        sel_o = self.table_ocupados.selectionModel().selectedRows()
        if sel_o:
            emp = self._obtener_fila_desde_proxy(self.proxy_ocupados, sel_o[0])
            if emp:
                self._abrir_calendario(emp)
                return

        QMessageBox.information(self, "Selección", "Seleccione un empleado en cualquiera de las dos tablas para ver su calendario.")

    def _abrir_calendario(self, emp: Dict[str, Any]) -> None:
        qdate = self.date_picker.date()
        leg_obj = emp.get("legajo")
        for abierto in list(self._calendarios_abiertos):
            try:
                if abierto.empleado.get("legajo") == leg_obj:
                    abierto.raise_()
                    abierto.activateWindow()
                    return
            except RuntimeError:
                if abierto in self._calendarios_abiertos:
                    self._calendarios_abiertos.remove(abierto)

        empleados_lista: List[Dict[str, Any]] = []
        vistos_leg = set()
        for proxy in (self.proxy_libres, self.proxy_ocupados):
            src_m = proxy.sourceModel()
            for r in range(proxy.rowCount()):
                src_idx = proxy.mapToSource(proxy.index(r, 0))
                fila_emp = src_m.get_row(src_idx.row()) if hasattr(src_m, "get_row") else None
                if fila_emp and fila_emp.get("legajo") not in vistos_leg:
                    vistos_leg.add(fila_emp.get("legajo"))
                    empleados_lista.append(dict(fila_emp))

        dlg = CalendarioCicloDialog(
            empleado=emp,
            records_service=self.records_service,
            pdf_service=self.pdf_service,
            excel_service=self.excel_service,
            mes=qdate.month(),
            anio=qdate.year(),
            usuario_actual=self.usuario_actual,
            tiene_permiso_fn=self.tiene_permiso_fn,
            empleados_lista=empleados_lista,
            parent=self,
        )
        dlg.setWindowModality(Qt.NonModal)
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose, True)
        self._calendarios_abiertos.append(dlg)

        def _on_closed(_res: int = 0, ref_dlg: CalendarioCicloDialog = dlg) -> None:
            if ref_dlg in self._calendarios_abiertos:
                self._calendarios_abiertos.remove(ref_dlg)
            self.consultar()

        dlg.finished.connect(_on_closed)
        dlg.show()

    def _on_soltado_en_libres(self, emp: Dict[str, Any]) -> None:
        """Al arrastrar un agente ocupado hacia la grilla de libres: liberar del relevo."""
        if not self._can("reemplazos.asignar"):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permiso para modificar o desasignar personal de ciclo.")
            return

        if emp.get("_es_novedad_propia"):
            nov_id = emp.get("novedad_propia_id", "")
            nov_tipo = emp.get("novedad_propia") or emp.get("tipo_novedad") or ""
            QMessageBox.information(
                self,
                "Novedad Propia Activa",
                f"El agente <b>{emp.get('apellidos_nombres', '')}</b> posee una novedad propia activa "
                f"(<b>#{nov_id} - {nov_tipo}</b>). Para liberarlo, cierre o modifique la novedad en la pestaña Novedades.",
            )
            return

        reemplazo_id = emp.get("reemplazo_id")
        nombre = emp.get("apellidos_nombres", "")
        titular = emp.get("reemplaza_a", "")
        nov_id = emp.get("novedad_id", "")

        resp = QMessageBox.question(
            self,
            "Liberar Personal de Ciclo",
            f"¿Desea liberar al agente <b>{nombre}</b> del relevo activo de la novedad #{nov_id} (Titular: {titular})?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp == QMessageBox.Yes and reemplazo_id:
            try:
                self.records_service.desactivar_reemplazo(
                    int(reemplazo_id),
                    user_id=self.usuario_actual.get("id"),
                    windows_user=self.usuario_actual.get("username"),
                )
                self.consultar()
                QMessageBox.information(self, "Relevo Finalizado", f"El agente {nombre} ha sido liberado del relevo.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"No se pudo liberar al agente:\n{e}")

    def _on_soltado_en_ocupados(self, emp: Dict[str, Any]) -> None:
        """Al arrastrar un agente libre hacia la grilla de ocupados: asignar como relevo a una novedad sin cubrir."""
        if not self._can("reemplazos.asignar"):
            QMessageBox.warning(self, "Permiso Denegado", "No posee permiso para asignar relevos a personal de ciclo.")
            return

        f_str = self.date_picker.date().toString("dd/MM/yyyy")
        dot = emp.get("dotacion") or self.combo_dotacion.currentText()
        nombre = emp.get("apellidos_nombres", "")
        legajo = emp.get("legajo")

        try:
            descubiertos = self.records_service.obtener_turnos_sin_cubrir(f_str, dotacion=dot if dot != "Todas" else None)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo consultar turnos descubiertos:\n{e}")
            return

        if not descubiertos:
            QMessageBox.information(
                self,
                "Sin Novedades Descubiertas",
                f"No se encontraron turnos descubiertos registrados en la fecha <b>{f_str}</b> (Dotación: {dot}).<br/><br/>"
                "Para cargar una nueva novedad, ingrese en la pestaña 'Novedades Operativas'.",
            )
            return

        if len(descubiertos) == 1:
            nov = descubiertos[0]
            resp = QMessageBox.question(
                self,
                "Asignar Relevo de Ciclo",
                f"¿Asignar a <b>{nombre}</b> (Leg. {legajo}) como relevo de la novedad #{nov['id']}?<br/><br/>"
                f"<b>Titular:</b> {nov.get('apellidos_nombres')} (Leg. {nov.get('legajo')})<br/>"
                f"<b>Turno:</b> {nov.get('turnos')} | <b>Motivo:</b> {nov.get('novedad')}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if resp == QMessageBox.Yes:
                try:
                    self.records_service.asignar_reemplazo_rapido(
                        novedad_id=nov["id"],
                        legajo_reemplazo=int(legajo),
                        fecha_inicio=f_str,
                        fecha_fin=nov.get("fecha_fin"),
                        user_id=self.usuario_actual.get("id"),
                        windows_user=self.usuario_actual.get("username"),
                    )
                    self.consultar()
                    QMessageBox.information(self, "Asignación Exitosa", f"Se asignó a {nombre} al relevo #{nov['id']}.")
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"No se pudo asignar el relevo:\n{e}")
        else:
            dlg = BaseDialog(self)
            dlg.setWindowTitle("Seleccionar Novedad a Cubrir")
            dlg.resize(520, 320)
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel(f"Seleccione la novedad a cubrir por <b>{nombre}</b>:"))
            lw = QListWidget()
            for nov in descubiertos:
                item_txt = f"#{nov['id']} | {nov.get('apellidos_nombres')} (Leg. {nov.get('legajo')}) - Turno {nov.get('turnos')} - {nov.get('novedad')}"
                l_item = QListWidgetItem(item_txt)
                l_item.setData(Qt.UserRole, nov)
                lw.addItem(l_item)
            lw.setCurrentRow(0)
            lay.addWidget(lw)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            lay.addWidget(bb)

            if dlg.exec():
                sel = lw.currentItem()
                if sel:
                    nov_sel = sel.data(Qt.UserRole)
                    try:
                        self.records_service.asignar_reemplazo_rapido(
                            novedad_id=nov_sel["id"],
                            legajo_reemplazo=int(legajo),
                            fecha_inicio=f_str,
                            fecha_fin=nov_sel.get("fecha_fin"),
                            user_id=self.usuario_actual.get("id"),
                            windows_user=self.usuario_actual.get("username"),
                        )
                        self.consultar()
                        QMessageBox.information(self, "Asignación Exitosa", f"Se asignó a {nombre} al relevo #{nov_sel['id']}.")
                    except Exception as e:
                        QMessageBox.critical(self, "Error", f"No se pudo asignar el relevo:\n{e}")

    def _abrir_exportar_pdf(self) -> None:
        dlg = ExportarCicloPDFDialog(
            records_service=self.records_service,
            pdf_service=self.pdf_service,
            dotacion_inicial=self.combo_dotacion.currentText(),
            especialidad_inicial=self.combo_esp.currentText(),
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        dlg.exec()


class DestinatariosCicloDialog(BaseDialog):
    """ABM liviano de destinatarios exclusivos para envío de informes de Personal de Ciclo."""

    def __init__(
        self,
        records_service: RecordsService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.usuario_actual = usuario_actual or {}
        self._items: List[Dict[str, Any]] = []
        self._edit_id: Optional[int] = None

        self.setWindowTitle("Destinatarios — Personal de Ciclo")
        self.resize(620, 420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["NOMBRE", "CORREO ELECTRÓNICO", "ESTADO"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 180)
        self.table.setColumnWidth(2, 85)
        self.table.itemSelectionChanged.connect(self._on_sel)
        layout.addWidget(self.table, 1)

        form = QFormLayout()
        self.txt_nombre = QLineEdit()
        self.txt_nombre.setPlaceholderText("Nombre o sector (ej. Jefatura TY)")
        self.txt_email = QLineEdit()
        self.txt_email.setPlaceholderText("correo@empresa.com")
        self.chk_activo = QCheckBox("Activo")
        self.chk_activo.setChecked(True)
        form.addRow("Nombre:", self.txt_nombre)
        form.addRow("Email:", self.txt_email)
        form.addRow("", self.chk_activo)
        layout.addLayout(form)

        btns = QHBoxLayout()
        btn_nuevo = QPushButton("➕ Nuevo")
        btn_nuevo.setObjectName("btnSecondary")
        btn_nuevo.clicked.connect(self._limpiar)
        btn_guardar = QPushButton("💾 Guardar")
        btn_guardar.clicked.connect(self._guardar)
        btn_borrar = QPushButton("🗑️ Eliminar")
        btn_borrar.setObjectName("btnDanger")
        btn_borrar.clicked.connect(self._eliminar)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("btnSecondary")
        btn_cerrar.clicked.connect(self.accept)
        btns.addWidget(btn_nuevo)
        btns.addWidget(btn_guardar)
        btns.addWidget(btn_borrar)
        btns.addStretch()
        btns.addWidget(btn_cerrar)
        layout.addLayout(btns)

        self._recargar()

    def _recargar(self) -> None:
        self._items = self.records_service.listar_destinatarios_ciclo(incluir_inactivos=True)
        self.table.setRowCount(len(self._items))
        for r, item in enumerate(self._items):
            self.table.setItem(r, 0, QTableWidgetItem(str(item.get("nombre") or "")))
            self.table.setItem(r, 1, QTableWidgetItem(str(item.get("email") or "")))
            est = QTableWidgetItem("Activo" if item.get("activo", 1) else "Inactivo")
            est.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, 2, est)

    def _on_sel(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._items):
            it = self._items[idx]
            self._edit_id = int(it["id"])
            self.txt_nombre.setText(str(it.get("nombre") or ""))
            self.txt_email.setText(str(it.get("email") or ""))
            self.chk_activo.setChecked(bool(it.get("activo", 1)))

    def _limpiar(self) -> None:
        self._edit_id = None
        self.table.clearSelection()
        self.txt_nombre.clear()
        self.txt_email.clear()
        self.chk_activo.setChecked(True)
        self.txt_nombre.setFocus()

    def _guardar(self) -> None:
        try:
            uid = self.usuario_actual.get("id")
            win_u = self.usuario_actual.get("username")
            if self._edit_id is None:
                self.records_service.crear_destinatario_ciclo(
                    self.txt_nombre.text(),
                    self.txt_email.text(),
                    user_id=uid,
                    windows_user=win_u,
                )
            else:
                self.records_service.actualizar_destinatario_ciclo(
                    self._edit_id,
                    self.txt_nombre.text(),
                    self.txt_email.text(),
                    1 if self.chk_activo.isChecked() else 0,
                    user_id=uid,
                    windows_user=win_u,
                )
            self._recargar()
            self._limpiar()
        except Exception as e:
            QMessageBox.warning(self, "Destinatarios", str(e))

    def _eliminar(self) -> None:
        if self._edit_id is None:
            return
        try:
            self.records_service.eliminar_destinatario_ciclo(
                self._edit_id,
                user_id=self.usuario_actual.get("id"),
                windows_user=self.usuario_actual.get("username"),
            )
            self._recargar()
            self._limpiar()
        except Exception as e:
            QMessageBox.warning(self, "Destinatarios", str(e))


class ExportarCicloPDFDialog(BaseDialog):
    """Diálogo para exportar el servicio de Personal de Ciclo en PDF horizontal por rango de fechas y dotación, con envío por Outlook."""

    def __init__(
        self,
        records_service: RecordsService,
        pdf_service: PDFService,
        fecha_inicial: Optional[QDate] = None,
        dotacion_inicial: str = "Todas",
        especialidad_inicial: str = "Todas",
        usuario_actual: Optional[Dict[str, Any]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.pdf_service = pdf_service
        self.usuario_actual = usuario_actual or {}
        self.setWindowTitle("Exportar Personal de Ciclo a PDF (Horizontal)")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        form = QFormLayout()
        # Por defecto: fecha de mañana (hoy + 1 día)
        f_base = fecha_inicial if (fecha_inicial and fecha_inicial.isValid()) else QDate.currentDate().addDays(1)

        self.date_desde = QDateEdit()
        self.date_desde.setCalendarPopup(True)
        self.date_desde.setDisplayFormat("dd/MM/yyyy")
        self.date_desde.setDate(f_base)
        form.addRow("Fecha desde:", self.date_desde)

        self.date_hasta = QDateEdit()
        self.date_hasta.setCalendarPopup(True)
        self.date_hasta.setDisplayFormat("dd/MM/yyyy")
        self.date_hasta.setDate(f_base)
        form.addRow("Fecha hasta:", self.date_hasta)

        # Al cambiar fecha desde, actualizar fecha hasta a la misma fecha
        self.date_desde.dateChanged.connect(self.date_hasta.setDate)

        self.combo_dot = QComboBox()
        self.combo_dot.addItem("Todas")
        for d in self.records_service.listar_dotaciones(incluir_inactivos=False):
            self.combo_dot.addItem(d["nombre"])
        idx_d = self.combo_dot.findText(dotacion_inicial or "Todas")
        if idx_d >= 0:
            self.combo_dot.setCurrentIndex(idx_d)
        form.addRow("Dotación:", self.combo_dot)

        self.combo_esp = QComboBox()
        self.combo_esp.addItem("Todas")
        for esp in self.records_service.listar_especialidades_empleados():
            self.combo_esp.addItem(esp)
        idx_e = self.combo_esp.findText(especialidad_inicial or "Todas")
        if idx_e >= 0:
            self.combo_esp.setCurrentIndex(idx_e)
        form.addRow("Especialidad:", self.combo_esp)

        layout.addLayout(form)

        # Sección Outlook (lista independiente destinatarios_ciclo)
        grp_ol = QGroupBox("Envío por Microsoft Outlook (Lista Personal de Ciclo)")
        lay_ol = QVBoxLayout(grp_ol)

        self.chk_outlook = QCheckBox("Enviar PDF adjunto por Outlook")
        lay_ol.addWidget(self.chk_outlook)

        h_dest = QHBoxLayout()
        h_dest.addWidget(QLabel("Destinatarios (;):"))
        self.txt_dest = QLineEdit()
        self.txt_dest.setPlaceholderText("correo1@empresa.com; correo2@empresa.com")
        h_dest.addWidget(self.txt_dest, 1)
        self.btn_dest = QPushButton("⚙️ Lista")
        self.btn_dest.setObjectName("btnSecondary")
        self.btn_dest.setToolTip("Administrar lista de destinatarios de Personal de Ciclo")
        self.btn_dest.clicked.connect(self._administrar_destinatarios)
        h_dest.addWidget(self.btn_dest)
        lay_ol.addLayout(h_dest)

        self.chk_borrador = QCheckBox("Abrir en Outlook como borrador antes de enviar")
        self.chk_borrador.setChecked(True)
        lay_ol.addWidget(self.chk_borrador)

        layout.addWidget(grp_ol)

        self._cargar_destinatarios_ciclo()
        self.chk_outlook.toggled.connect(self._toggle_outlook)
        self._toggle_outlook(False)

        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.clicked.connect(self.reject)
        btn_export = QPushButton("📄 Exportar PDF")
        btn_export.clicked.connect(self._ejecutar)
        btns.addWidget(btn_cancel)
        btns.addWidget(btn_export)
        layout.addLayout(btns)

    def _cargar_destinatarios_ciclo(self) -> None:
        try:
            dests = self.records_service.listar_destinatarios_ciclo(incluir_inactivos=False)
            self.txt_dest.setText("; ".join(str(d.get("email") or "").strip() for d in dests if d.get("email")))
        except Exception as e:
            logger.warning("No se pudo cargar destinatarios_ciclo: %s", e)

    def _administrar_destinatarios(self) -> None:
        dlg = DestinatariosCicloDialog(
            records_service=self.records_service,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        dlg.exec()
        self._cargar_destinatarios_ciclo()

    def _toggle_outlook(self, checked: bool) -> None:
        self.txt_dest.setEnabled(checked)
        self.btn_dest.setEnabled(checked)
        self.chk_borrador.setEnabled(checked)

    def _ejecutar(self) -> None:
        if self.date_desde.date() > self.date_hasta.date():
            QMessageBox.warning(self, "Fechas inválidas", "La fecha desde no puede ser mayor a la fecha hasta.")
            return

        f_desde = self.date_desde.date().toString("dd/MM/yyyy")
        f_hasta = self.date_hasta.date().toString("dd/MM/yyyy")
        dot = self.combo_dot.currentText()
        esp = self.combo_esp.currentText()
        dot_filtro = None if dot in ("", "Todas") else dot
        esp_filtro = None if esp in ("", "Todas") else esp

        enviar_ol = self.chk_outlook.isChecked()
        abrir_borrador = self.chk_borrador.isChecked()
        lista_dest = [
            d.strip()
            for d in self.txt_dest.text().replace(",", ";").split(";")
            if d.strip()
        ]
        if enviar_ol and not abrir_borrador and not lista_dest:
            QMessageBox.warning(
                self,
                "Destinatarios",
                "Ingrese al menos un destinatario para enviar directamente por Outlook (o marque abrir como borrador).",
            )
            return

        dot_tag = (dot_filtro or "Todas").replace(" ", "_")
        sugerido = f"Personal_Ciclo_{dot_tag}_{f_desde.replace('/', '-')}_al_{f_hasta.replace('/', '-')}.pdf"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Guardar PDF de Personal de Ciclo",
            sugerido,
            "PDF Files (*.pdf)",
        )
        if not file_path:
            return

        try:
            dias = self.records_service.obtener_estado_personal_ciclo_rango(
                f_desde,
                f_hasta,
                dotacion=dot_filtro,
                especialidad=esp_filtro,
            )
            pdf_out = self.pdf_service.generar_servicio_ciclo_rango_pdf(
                dias,
                f_desde,
                f_hasta,
                file_path,
                dotacion=dot_filtro,
                especialidad=esp_filtro,
            )

            if enviar_ol:
                from src.services.mail_service import MailService

                dot_txt = dot_filtro or "Todas"
                asunto = f"Personal de Ciclo - Dotación {dot_txt} ({f_desde} al {f_hasta})"
                cuerpo_html = (
                    f"<p>Se adjunta el informe en PDF (hoja horizontal) de <b>Personal de Ciclo</b>.</p>"
                    f"<ul>"
                    f"<li><b>Período:</b> {f_desde} al {f_hasta}</li>"
                    f"<li><b>Dotación:</b> {dot_txt}</li>"
                    f"<li><b>Especialidad:</b> {esp_filtro or 'Todas'}</li>"
                    f"</ul>"
                )
                ok, msg_ol = MailService.crear_borrador_outlook(
                    destinatarios=lista_dest,
                    asunto=asunto,
                    cuerpo_html=cuerpo_html,
                    adjuntos=[pdf_out],
                    enviar_directo=not abrir_borrador,
                )
                if not ok:
                    QMessageBox.warning(
                        self,
                        "PDF generado — Error al abrir Outlook",
                        f"El PDF se guardó en:\n{pdf_out}\n\nNo se pudo abrir/enviar en Outlook:\n{msg_ol}",
                    )
                    self.accept()
                    return
                if abrir_borrador:
                    # Cerrar diálogo sin tapar la ventana de borrador de Outlook
                    self.accept()
                    return
                QMessageBox.information(
                    self,
                    "Exportación y Envío Exitosos",
                    f"PDF horizontal generado correctamente en:\n{pdf_out}\n\n{msg_ol}",
                )
                self.accept()
                return

            QMessageBox.information(
                self,
                "Exportación Exitosa",
                f"PDF horizontal generado correctamente en:\n{pdf_out}",
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo generar el PDF:\n{e}")

