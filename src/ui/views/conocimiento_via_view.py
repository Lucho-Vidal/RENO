"""Vista principal de Conocimiento de Vía por Sector para Personal de Conducción.

Muestra la matriz (Personal de Conducción x Sectores de Vía) con soporte para:
- Registro de fecha de conocimiento en vía Ascendente (alejándose de PC km 0) y Descendente (hacia PC).
- Control automático de vencimiento por convenio a 2 años con doble umbral de alerta (60 y 30 días).
- Inclusión de Conductores y Ayudantes (suprimiendo alertas de vencimiento para Ayudantes).
- Catálogo editable de sectores y autocompletado por turno asignado.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
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
from src.services.excel_service import ExcelService
from src.services.records_service import RecordsService
from src.ui.models.multi_filter_proxy import MultiFilterProxyModel
from src.ui.views.dialogs.conocimiento_via_dialogs import (
    AdminSectoresViaDialog,
    AutocompletarMasivoViaDialog,
    AutocompletarTurnoViaDialog,
    EditarConocimientoDialog,
)

logger = get_logger("ConocimientoViaView")

_PRIORIDAD_ALERTA = {
    "VENCIDO": 4,
    "POR_VENCER_30": 3,
    "POR_VENCER_60": 2,
    "VIGENTE": 1,
    "SIN_ALERTA": 1,
    "SIN_REGISTRO": 0,
}

_COLS_FIJAS = ("LEGAJO", "APELLIDOS Y NOMBRES", "ESPECIALIDAD", "DOTACIÓN", "TURNO")
_KEYS_FIJAS = ("legajo", "apellidos_nombres", "especialidad", "dotacion", "turnos")


def _fecha_corta(fecha_display: str) -> str:
    if len(fecha_display) == 10 and fecha_display[2] == "/" and fecha_display[5] == "/":
        return f"{fecha_display[:6]}{fecha_display[8:]}"
    return fecha_display


def _peor_estado_celda(info_sec: Dict[str, Dict[str, Any]]) -> str:
    est_asc = (info_sec.get("ASC") or {}).get("estado_alerta", "SIN_REGISTRO")
    est_desc = (info_sec.get("DESC") or {}).get("estado_alerta", "SIN_REGISTRO")
    return est_asc if _PRIORIDAD_ALERTA.get(est_asc, 0) >= _PRIORIDAD_ALERTA.get(est_desc, 0) else est_desc


class ConocimientoViaTableModel(QAbstractTableModel):
    """Modelo matricial de Personal de Conducción x Sectores de Vía."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sectores: List[Dict[str, Any]] = []
        self._filas: List[Dict[str, Any]] = []

    def set_matriz(self, sectores: List[Dict[str, Any]], filas: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._sectores = list(sectores)
        self._filas = list(filas)
        self.endResetModel()

    def get_row(self, row_idx: int) -> Optional[Dict[str, Any]]:
        if 0 <= row_idx < len(self._filas):
            return self._filas[row_idx]
        return None

    def get_sector_for_column(self, col_idx: int) -> Optional[Dict[str, Any]]:
        sec_idx = col_idx - len(_COLS_FIJAS)
        if 0 <= sec_idx < len(self._sectores):
            return self._sectores[sec_idx]
        return None

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._filas)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(_COLS_FIJAS) + len(self._sectores)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:
        if role == Qt.DisplayRole:
            if orientation == Qt.Horizontal:
                if section < len(_COLS_FIJAS):
                    return _COLS_FIJAS[section]
                sec = self.get_sector_for_column(section)
                return sec["codigo"] if sec else ""
            return str(section + 1)
        if role == Qt.ToolTipRole and orientation == Qt.Horizontal:
            sec = self.get_sector_for_column(section)
            if sec:
                desc = f" — {sec['descripcion']}" if sec.get("descripcion") else ""
                return (
                    f"Sector {sec['codigo']}{desc}\n"
                    f"▲ ASC: {sec['origen_sigla']} → {sec['destino_sigla']}\n"
                    f"▼ DESC: {sec['destino_sigla']} → {sec['origen_sigla']}"
                )
        if role == Qt.TextAlignmentRole and orientation == Qt.Horizontal:
            return int(Qt.AlignCenter)
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._filas)):
            return None
        fila = self._filas[index.row()]
        col = index.column()

        if col < len(_COLS_FIJAS):
            val = fila.get(_KEYS_FIJAS[col], "")
            if role == Qt.DisplayRole:
                return "-" if val is None or val == "" else str(val)
            if role == Qt.TextAlignmentRole:
                if col in (0, 3, 4):
                    return int(Qt.AlignCenter)
                return int(Qt.AlignLeft | Qt.AlignVCenter)
            return None

        sec = self.get_sector_for_column(col)
        if not sec:
            return None
        sid = int(sec["id"])
        info_sec = (fila.get("celdas") or {}).get(sid, {})
        asc = info_sec.get("ASC") or {}
        desc = info_sec.get("DESC") or {}

        if role == Qt.DisplayRole:
            f_asc = _fecha_corta(asc.get("fecha_display", ""))
            f_desc = _fecha_corta(desc.get("fecha_display", ""))
            if f_asc and f_desc:
                if f_asc == f_desc:
                    return f"▲▼ {f_asc}"
                return f"▲ {f_asc} | ▼ {f_desc}"
            if f_asc:
                return f"▲ {f_asc}"
            if f_desc:
                return f"▼ {f_desc}"
            return "-"

        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter)

        if role == Qt.ToolTipRole:
            lineas = [
                f"{fila.get('apellidos_nombres', '')} (Leg. {fila.get('legajo', '')})",
                f"Sector: {sec['codigo']} ({sec.get('origen_sigla', '')} ↔ {sec.get('destino_sigla', '')})",
            ]
            if fila.get("es_ayudante"):
                lineas.append("Rol: Ayudante (sin alertas de vencimiento)")
            for etiqueta, ev in (("▲ Ascendente", asc), ("▼ Descendente", desc)):
                if ev.get("fecha_display"):
                    dias = ev.get("dias_restantes")
                    dias_txt = f"{dias} días" if dias is not None else "-"
                    lineas.append(
                        f"{etiqueta}: {ev['fecha_display']} | Vence: {ev.get('fecha_vencimiento_display', '-')} ({dias_txt})"
                    )
                    if ev.get("observaciones"):
                        lineas.append(f"   Obs: {ev['observaciones']}")
                else:
                    lineas.append(f"{etiqueta}: Sin registro")
            return "\n".join(lineas)

        if role in (Qt.BackgroundRole, Qt.ForegroundRole):
            peor = _peor_estado_celda(info_sec)
            if peor == "SIN_REGISTRO":
                return None
            from src.core.theme import obtener_tema_visual

            es_oscuro = obtener_tema_visual() == "oscuro"
            paleta = {
                "VENCIDO": (QColor("#3D1B1B"), QColor("#FCA5A5")) if es_oscuro else (QColor("#FFEBEE"), QColor("#B71C1C")),
                "POR_VENCER_30": (QColor("#4A280A"), QColor("#FDBA74")) if es_oscuro else (QColor("#FFE0B2"), QColor("#E65100")),
                "POR_VENCER_60": (QColor("#3D3214"), QColor("#FDE047")) if es_oscuro else (QColor("#FFF9C4"), QColor("#8D6E00")),
                "VIGENTE": (QColor("#132E1F"), QColor("#6EE7B7")) if es_oscuro else (QColor("#E8F5E9"), QColor("#2E7D32")),
                "SIN_ALERTA": (QColor("#172554"), QColor("#93C5FD")) if es_oscuro else (QColor("#EFF6FF"), QColor("#1E40AF")),
            }
            par = paleta.get(peor)
            if not par:
                return None
            return QBrush(par[0] if role == Qt.BackgroundRole else par[1])

        if role == Qt.FontRole:
            peor = _peor_estado_celda(info_sec)
            if peor in ("VENCIDO", "POR_VENCER_30"):
                f = QFont()
                f.setBold(True)
                return f

        return None


class ConocimientoViaView(QWidget):
    """Pestaña de Conocimiento de Vía por Sector para Personal de Conducción."""

    def __init__(
        self,
        records_service: RecordsService,
        excel_service: ExcelService,
        usuario_actual: Optional[Dict[str, Any]] = None,
        tiene_permiso_fn=None,
        parent=None,
    ):
        super().__init__(parent)
        self.records_service = records_service
        self.excel_service = excel_service
        self.usuario_actual = usuario_actual or {}
        self.tiene_permiso_fn = tiene_permiso_fn

        self._sectores: List[Dict[str, Any]] = []
        self._filas_raw: List[Dict[str, Any]] = []

        self._init_ui()
        self.consultar()

    def _can(self, perm: str) -> bool:
        if self.tiene_permiso_fn:
            return bool(self.tiene_permiso_fn(perm))
        return False

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 1. Barra de acciones y filtros
        top_frame = QFrame()
        top_frame.setObjectName("filterBar")
        top_lay = QHBoxLayout(top_frame)
        top_lay.setContentsMargins(8, 4, 8, 4)
        top_lay.setSpacing(8)

        top_lay.addWidget(QLabel("Dotación:"))
        self.combo_dot = QComboBox()
        self.combo_dot.addItem("Todas")
        for d in self.records_service.listar_dotaciones(incluir_inactivos=False):
            self.combo_dot.addItem(d["nombre"])
        self.combo_dot.currentIndexChanged.connect(self.consultar)
        top_lay.addWidget(self.combo_dot)

        top_lay.addWidget(QLabel("Especialidad:"))
        self.combo_esp = QComboBox()
        self.combo_esp.addItem("Todas")
        self.combo_esp.currentIndexChanged.connect(self.consultar)
        top_lay.addWidget(self.combo_esp)

        top_lay.addWidget(QLabel("Estado:"))
        self.combo_estado = QComboBox()
        self.combo_estado.addItems([
            "Todos",
            "Con vencidos",
            "Por vencer (≤ 30 días)",
            "Por vencer (≤ 60 días)",
            "Solo vigentes",
            "Sin registros",
        ])
        self.combo_estado.currentIndexChanged.connect(self._aplicar_filtros_locales)
        top_lay.addWidget(self.combo_estado)

        top_lay.addWidget(QLabel("Buscar:"))
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Legajo, nombre, turno...")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.setFixedWidth(185)
        self.txt_search.textChanged.connect(self._on_search_changed)
        top_lay.addWidget(self.txt_search)

        self.btn_actualizar = QPushButton("🔍 Actualizar")
        self.btn_actualizar.clicked.connect(self.consultar)
        top_lay.addWidget(self.btn_actualizar)

        top_lay.addStretch()

        self.btn_editar = QPushButton("✏️ Registrar / Editar")
        self.btn_editar.setEnabled(self._can("conocimiento_via.editar"))
        self.btn_editar.clicked.connect(self._editar_seleccion)
        top_lay.addWidget(self.btn_editar)

        self.btn_autocompletar = QPushButton("⚡ Autocompletar por Turno")
        self.btn_autocompletar.setObjectName("btnSecondary")
        self.btn_autocompletar.setEnabled(self._can("conocimiento_via.editar"))
        self.btn_autocompletar.clicked.connect(self._autocompletar_por_turno)
        top_lay.addWidget(self.btn_autocompletar)

        puede_masivo = self._can("conocimiento_via.autocompletar_masivo")
        self.btn_autocompletar_masivo = QPushButton("⚡⚡ Calcular Todo el Personal")
        self.btn_autocompletar_masivo.setObjectName("btnSecondary")
        self.btn_autocompletar_masivo.setEnabled(puede_masivo)
        self.btn_autocompletar_masivo.setToolTip(
            "Calcula y registra el conocimiento de vía para todo el personal de conducción según su turno asignado."
            if puede_masivo
            else "Requiere el permiso 'conocimiento_via.autocompletar_masivo'."
        )
        self.btn_autocompletar_masivo.clicked.connect(self._autocompletar_masivo)
        top_lay.addWidget(self.btn_autocompletar_masivo)

        self.btn_sectores = QPushButton("⚙️ Sectores")
        self.btn_sectores.setObjectName("btnSecondary")
        self.btn_sectores.setEnabled(self._can("conocimiento_via.administrar"))
        self.btn_sectores.clicked.connect(self._administrar_sectores)
        top_lay.addWidget(self.btn_sectores)

        self.btn_excel = QPushButton("📊 Exportar Excel")
        self.btn_excel.setObjectName("btnSecondary")
        self.btn_excel.clicked.connect(self._exportar_excel)
        top_lay.addWidget(self.btn_excel)

        layout.addWidget(top_frame)

        # 2. Tabla matricial
        self.table_model = ConocimientoViaTableModel(self)
        self.proxy_model = MultiFilterProxyModel(self)
        self.proxy_model.setSourceModel(self.table_model)

        self.table = QTableView()
        self.table.setModel(self.proxy_model)
        self.table.setSelectionBehavior(QTableView.SelectItems)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.table, 1)

        # 3. Pie con leyenda y resumen
        foot = QHBoxLayout()
        self.lbl_resumen = QLabel("")
        self.lbl_resumen.setStyleSheet("font-weight: 600; font-size: 9pt;")
        foot.addWidget(self.lbl_resumen)
        foot.addStretch()
        lbl_leyenda = QLabel(
            "▲ ASC (alejándose de PC km 0) | ▼ DESC (hacia PC)   •   "
            "🟢 Vigente   🟡 ≤ 60d   🟠 ≤ 30d   🔴 Vencido (> 2 años)   •   "
            "Doble clic en una celda para editar"
        )
        lbl_leyenda.setStyleSheet("color: #6B7280; font-size: 8.5pt;")
        foot.addWidget(lbl_leyenda)
        layout.addLayout(foot)

    def _poblar_especialidades(self) -> None:
        from src.domain.conocimiento_via import es_personal_conduccion

        cur = self.combo_esp.currentText()
        esps = [e for e in self.records_service.listar_especialidades_empleados() if es_personal_conduccion(e)]
        self.combo_esp.blockSignals(True)
        self.combo_esp.clear()
        self.combo_esp.addItem("Todas")
        for e in esps:
            self.combo_esp.addItem(e)
        idx = self.combo_esp.findText(cur)
        if idx >= 0:
            self.combo_esp.setCurrentIndex(idx)
        self.combo_esp.blockSignals(False)

    def consultar(self) -> None:
        self._poblar_especialidades()
        dot = self.combo_dot.currentText()
        esp = self.combo_esp.currentText()
        try:
            datos = self.records_service.obtener_matriz_conocimiento_via(
                dotacion=None if dot == "Todas" else dot,
                especialidad=None if esp == "Todas" else esp,
            )
            self._sectores = datos.get("sectores", [])
            self._filas_raw = datos.get("filas", [])
            self._aplicar_filtros_locales()
            res = datos.get("resumen", {})
            self.lbl_resumen.setText(
                f"Personal: {res.get('personal', 0)}  |  "
                f"Vigentes: {res.get('vigentes', 0)}  |  "
                f"Alerta ≤60d: {res.get('alerta_60', 0)}  |  "
                f"Alerta ≤30d: {res.get('alerta_30', 0)}  |  "
                f"Vencidos: {res.get('vencidos', 0)}"
            )
        except Exception as e:
            logger.error("Error al consultar Conocimiento de Vía: %s", e)

    def _aplicar_filtros_locales(self) -> None:
        filtro_est = self.combo_estado.currentText()
        filtradas: List[Dict[str, Any]] = []
        for f in self._filas_raw:
            if filtro_est == "Con vencidos" and not f.get("tiene_vencido"):
                continue
            elif filtro_est == "Por vencer (≤ 30 días)" and not f.get("tiene_alerta_30"):
                continue
            elif filtro_est == "Por vencer (≤ 60 días)" and not (f.get("tiene_alerta_60") or f.get("tiene_alerta_30")):
                continue
            elif filtro_est == "Solo vigentes" and (
                not f.get("tiene_vigente") or f.get("tiene_vencido")
            ):
                continue
            elif filtro_est == "Sin registros":
                tiene_alguno = any(
                    (ev.get("fecha_iso"))
                    for s_map in (f.get("celdas") or {}).values()
                    for ev in s_map.values()
                )
                if tiene_alguno:
                    continue
            filtradas.append(f)

        self.table_model.set_matriz(self._sectores, filtradas)
        self._ajustar_anchos()

    def _ajustar_anchos(self) -> None:
        self.table.setColumnWidth(0, 75)   # LEGAJO
        self.table.setColumnWidth(1, 210)  # APELLIDOS Y NOMBRES
        self.table.setColumnWidth(2, 155)  # ESPECIALIDAD
        self.table.setColumnWidth(3, 80)   # DOTACIÓN
        self.table.setColumnWidth(4, 90)   # TURNO
        for idx in range(len(_COLS_FIJAS), self.table_model.columnCount()):
            self.table.setColumnWidth(idx, 118)

    def _on_search_changed(self, text: str) -> None:
        self.proxy_model.set_global_search(text)

    def _obtener_celda_seleccionada(self):
        idx = self.table.currentIndex()
        if not idx.isValid():
            return None, None
        src_idx = self.proxy_model.mapToSource(idx)
        fila = self.table_model.get_row(src_idx.row())
        sector = self.table_model.get_sector_for_column(src_idx.column())
        return fila, sector

    def _on_double_click(self, proxy_index: QModelIndex) -> None:
        if not self._can("conocimiento_via.editar"):
            return
        src_idx = self.proxy_model.mapToSource(proxy_index)
        fila = self.table_model.get_row(src_idx.row())
        sector = self.table_model.get_sector_for_column(src_idx.column())
        if fila and sector:
            self._abrir_edicion(fila, sector)

    def _editar_seleccion(self) -> None:
        if not self._can("conocimiento_via.editar"):
            return
        fila, sector = self._obtener_celda_seleccionada()
        if not fila:
            QMessageBox.information(self, "Selección", "Seleccione una fila/celda de personal de conducción en la tabla.")
            return
        if not sector:
            if not self._sectores:
                QMessageBox.information(self, "Sectores", "No hay sectores de vía activos configurados.")
                return
            sector = self._sectores[0]
        self._abrir_edicion(fila, sector)

    def _abrir_edicion(self, fila: Dict[str, Any], sector: Dict[str, Any]) -> None:
        sid = int(sector["id"])
        info_actual = (fila.get("celdas") or {}).get(sid, {})
        dlg = EditarConocimientoDialog(
            records_service=self.records_service,
            empleado=fila,
            sector=sector,
            info_actual=info_actual,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        if dlg.exec():
            self.consultar()

    def _autocompletar_por_turno(self) -> None:
        if not self._can("conocimiento_via.editar"):
            return
        fila, _ = self._obtener_celda_seleccionada()
        if not fila:
            QMessageBox.information(
                self,
                "Autocompletar por Turno",
                "Seleccione un agente en la tabla para autocompletar sus sectores según su turno asignado.",
            )
            return
        dlg = AutocompletarTurnoViaDialog(
            records_service=self.records_service,
            empleado=fila,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        if dlg.exec():
            self.consultar()

    def _autocompletar_masivo(self) -> None:
        if not self._can("conocimiento_via.autocompletar_masivo"):
            return
        dot = self.combo_dot.currentText()
        esp = self.combo_esp.currentText()
        dlg = AutocompletarMasivoViaDialog(
            records_service=self.records_service,
            usuario_actual=self.usuario_actual,
            dotacion_actual=None if dot == "Todas" else dot,
            especialidad_actual=None if esp == "Todas" else esp,
            parent=self,
        )
        if dlg.exec():
            self.consultar()

    def _administrar_sectores(self) -> None:
        if not self._can("conocimiento_via.administrar"):
            return
        dlg = AdminSectoresViaDialog(
            records_service=self.records_service,
            usuario_actual=self.usuario_actual,
            parent=self,
        )
        dlg.exec()
        self.consultar()

    def _exportar_excel(self) -> None:
        if not self._filas_raw:
            QMessageBox.information(self, "Exportar", "No hay registros para exportar.")
            return
        fecha_tag = datetime.now().strftime("%Y%m%d")
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar Conocimiento de Vía a Excel",
            f"Conocimiento_Via_{fecha_tag}.xlsx",
            "Excel Files (*.xlsx)",
        )
        if not file_path:
            return
        try:
            filas_visibles = [
                self.table_model.get_row(self.proxy_model.mapToSource(self.proxy_model.index(r, 0)).row())
                for r in range(self.proxy_model.rowCount())
            ]
            filas_visibles = [f for f in filas_visibles if f]
            out = self.excel_service.export_conocimiento_via(file_path, self._sectores, filas_visibles)
            QMessageBox.information(self, "Exportación Exitosa", f"Archivo exportado correctamente a:\n{out}")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n{e}")
