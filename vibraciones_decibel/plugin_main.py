# plugin_main.py
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .compat import QAction


class VibracionesDecibelPlugin:
    """Registra la acción y muestra el dock widget (QGIS 3.28 – 4.x)."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dock = None
        self.menu = "&Vibraciones Decibel"

    def initGui(self):
        icono = QIcon(os.path.join(self.plugin_dir, "icon.png"))
        self.action = QAction(icono, "Proyección de Vibraciones",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu(self.menu, self.action)

    def unload(self):
        self.iface.removePluginMenu(self.menu, self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

    def run(self):
        if self.dock is None:
            # Import diferido: si falta una dependencia, QGIS igual carga el
            # plugin y el error aparece al abrirlo, no al iniciar QGIS.
            from .vibraciones_dockwidget import VibracionesDockWidget
            self.dock = VibracionesDockWidget(self.iface, self.plugin_dir)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,
                                     self.dock)
        self.dock.show()
        self.dock.raise_()
