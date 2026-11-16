"""Clases base unificadas para ventanas modales y diálogos maximizados de RENO."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QWidget

from src.core.theme import configurar_ventana_dialogo


class BaseDialog(QDialog):
    """Diálogo modal estándar con botón nativo de cierre (X), icono corporativo y ciclo de cierre seguro."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        maximizable: bool = False,
        permitir_cierre: bool = True,
        aplicar_icono: bool = True,
    ) -> None:
        super().__init__(parent)
        self._cerrando: bool = False
        self._permitir_cierre_nativo: bool = bool(permitir_cierre)
        configurar_ventana_dialogo(
            self,
            maximizable=maximizable,
            permitir_cierre=permitir_cierre,
            aplicar_icono=aplicar_icono,
        )

    def showEvent(self, event) -> None:
        self._cerrando = False
        super().showEvent(event)

    def done(self, result: int) -> None:
        self._cerrando = True
        super().done(result)

    def closeEvent(self, event) -> None:
        if not self._permitir_cierre_nativo:
            event.ignore()
            return
        self._cerrando = True
        self.reject()
        event.accept()


class BaseMaximizedDialog(BaseDialog):
    """Diálogo modal maximizado con botones nativos de min/max/cierre (X) y sin recursión en showEvent."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        aplicar_icono: bool = True,
    ) -> None:
        super().__init__(
            parent,
            maximizable=True,
            permitir_cierre=True,
            aplicar_icono=aplicar_icono,
        )
        self._maximizado_inicial: bool = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._maximizado_inicial:
            self._maximizado_inicial = True
            self.setWindowState(
                (self.windowState() & ~(Qt.WindowMinimized | Qt.WindowFullScreen))
                | Qt.WindowMaximized
            )

    def done(self, result: int) -> None:
        self._maximizado_inicial = False
        super().done(result)

    def closeEvent(self, event) -> None:
        self._maximizado_inicial = False
        super().closeEvent(event)
