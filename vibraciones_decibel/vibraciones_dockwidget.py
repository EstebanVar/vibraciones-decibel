# vibraciones_dockwidget.py
"""
Dock principal del plugin (QGIS 3.28 – 4.x).

Pestañas, equivalentes a la app web "Proyección de vibraciones · FTA":
  Proyecto    escenario, capas de focos (puntos o áreas) y receptores, huso,
              modo de proyección (Iterativo / Suma / Lineal)
  Focos       equipo FTA (Tabla 7-4) o nivel medido, activo, nombre editable
  Receptores  criterio de molestia (Tabla 6-3) y daño (Tabla 7-5) por receptor
  Isolíneas   escala: nivel mínimo, paso y n.º de colores; paleta; opacidad
  Resultados  evaluación por receptor, memoria de cálculo y alcance por nivel
"""

import os
import re

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QDoubleSpinBox, QSpinBox, QPushButton, QComboBox, QCheckBox, QLineEdit,
    QTableWidget, QTableWidgetItem, QGroupBox, QMessageBox, QHeaderView,
    QFileDialog, QTabWidget, QPlainTextEdit, QDialog, QDialogButtonBox,
    QAbstractItemView,
)
from qgis.gui import QgsMapLayerComboBox, QgsColorButton
from qgis.core import (QgsProject, QgsGeometry, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform)

from . import compat
from . import fta_datos as FTA
from .calculos_core import calcular_todo
from .estilos import construir_capas, COLOR_ESTADO

# Selector único de modo: (texto, clave para el núcleo, ayuda)
MODOS = [
    ("Iterativo", "iterativo",
     "Solo el foco elegido en la pestaña Focos."),
    ("Suma", "suma",
     "Suma logarítmica (energética) del nivel que llega de cada foco."),
    ("Lineal", "lineal",
     "Sin sumar niveles: las isolíneas de cada foco que se tocan se disuelven.\n"
     "Cada receptor toma el nivel de la fuente más cercana."),
]

GRIS = QColor('#f1f1ec')
ROL = Qt.ItemDataRole.UserRole
EDITABLE = Qt.ItemFlag.ItemIsEditable

# Columnas de las tablas
F_ACT, F_NOM, F_EQ, F_LV, F_PPV = range(5)
R_NOM, R_CLV, R_LLV, R_CPPV, R_LPPV = range(5)


def _es_nulo(v):
    """None, NULL de QGIS (QVariant en PyQt5) o texto vacío."""
    if v is None:
        return True
    if hasattr(v, 'isNull'):
        try:
            return bool(v.isNull())
        except TypeError:
            pass
    return str(v).strip() in ("", "NULL")


def _num(txt):
    if _es_nulo(txt):
        return None
    try:
        return float(str(txt).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _f(v, d=1):
    return "—" if v is None else f"{v:.{d}f}".replace(".", ",")


def _slug(s):
    s = re.sub(r"[^\w\-]+", "_", s.strip(), flags=re.UNICODE)
    return s.strip("_") or "vibraciones"


class VibracionesDockWidget(QDockWidget):

    def __init__(self, iface, plugin_dir=None, parent=None):
        super().__init__("Proyección de Vibraciones · FTA 2018", parent)
        self.iface = iface
        self.plugin_dir = plugin_dir
        self.resultado = None
        self._meta = {}
        self._capa_focos_id = None
        self._capa_rec_id = None

        self._build_ui()
        self._connect_signals()
        self._on_capa_focos_cambia(self.cmb_focos.currentLayer())

    # ==================================================================
    # UI
    # ==================================================================
    def _build_ui(self):
        raiz = QWidget()
        lay = QVBoxLayout(raiz)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)
        self.tabs.addTab(self._tab_proyecto(), "Proyecto")
        self.tabs.addTab(self._tab_focos(), "Focos")
        self.tabs.addTab(self._tab_receptores(), "Receptores")
        self.tabs.addTab(self._tab_isolineas(), "Isolíneas")
        self.tabs.addTab(self._tab_resultados(), "Resultados")

        fila = QHBoxLayout()
        self.btn_calcular = QPushButton("Calcular y dibujar")
        self.btn_calcular.setStyleSheet("font-weight: bold;")
        self.btn_exportar = QPushButton("Exportar…")
        self.btn_exportar.setEnabled(False)
        fila.addWidget(self.btn_calcular, 2)
        fila.addWidget(self.btn_exportar, 1)
        lay.addLayout(fila)
        self.lbl_estado = QLabel("")
        self.lbl_estado.setWordWrap(True)
        self.lbl_estado.setStyleSheet("color:#5a6570;font-size:11px;")
        lay.addWidget(self.lbl_estado)
        self.setWidget(raiz)

    def _tab_proyecto(self):
        w = QWidget()
        v = QVBoxLayout(w)

        # El nombre del proyecto es el del proyecto QGIS abierto (no se pide).
        gb = QGroupBox("Escenario")
        f = QFormLayout(gb)
        self.txt_escenario = QLineEdit("Situación 1")
        self.txt_escenario.setToolTip("Cada escenario se dibuja en su propio grupo de capas.")
        f.addRow("Nombre:", self.txt_escenario)
        v.addWidget(gb)

        gb = QGroupBox("Capas")
        f = QFormLayout(gb)
        self.cmb_focos = QgsMapLayerComboBox()
        self.cmb_focos.setFilters(compat.FILTRO_PUNTOS | compat.FILTRO_POLIGONOS)
        self.cmb_rec = QgsMapLayerComboBox()
        self.cmb_rec.setFilters(compat.FILTRO_PUNTOS)
        f.addRow("Focos (puntos o áreas):", self.cmb_focos)
        f.addRow("Receptores:", self.cmb_rec)
        v.addWidget(gb)

        gb = QGroupBox("Cálculo")
        f = QFormLayout(gb)
        self.chk_huso_auto = QCheckBox("Detectar huso UTM automáticamente")
        self.chk_huso_auto.setChecked(True)
        self.spin_epsg = QSpinBox()
        self.spin_epsg.setRange(1024, 999999)
        self.spin_epsg.setValue(32719)
        self.spin_epsg.setEnabled(False)
        self.cmb_modo = QComboBox()
        for i, (txt, clave, ayuda) in enumerate(MODOS):
            self.cmb_modo.addItem(txt, clave)
            self.cmb_modo.setItemData(i, ayuda, Qt.ItemDataRole.ToolTipRole)
        self.cmb_modo.setCurrentIndex(1)          # Suma por defecto
        self.lbl_modo = QLabel(MODOS[1][2])
        self.lbl_modo.setWordWrap(True)
        self.lbl_modo.setStyleSheet("color:#5a6570;font-size:11px;")
        self.spin_n = QDoubleSpinBox()
        self.spin_n.setRange(0.5, 3.0)
        self.spin_n.setSingleStep(0.1)
        self.spin_n.setValue(1.5)
        self.spin_res = QDoubleSpinBox()
        self.spin_res.setRange(0.25, 100.0)
        self.spin_res.setSingleStep(0.5)
        self.spin_res.setValue(2.0)
        self.spin_res.setToolTip("Solo con varios focos. Con un foco las isolíneas son exactas.")
        self.chk_acotar = QCheckBox("Acotar distancia mínima a 25 ft (7,62 m)")
        self.chk_acotar.setToolTip(
            "Desactivado (como la app web): focos puntuales hasta 0,3 m.\n"
            "Los focos de área siempre se acotan a 7,62 m.")
        f.addRow(self.chk_huso_auto)
        f.addRow("EPSG (UTM):", self.spin_epsg)
        f.addRow("Modo de proyección:", self.cmb_modo)
        f.addRow(self.lbl_modo)
        f.addRow("Exponente PPV (n):", self.spin_n)
        f.addRow("Resolución grilla (m):", self.spin_res)
        f.addRow(self.chk_acotar)
        v.addWidget(gb)

        nota = QLabel("Lv(D) = Lv,ref − 30·log10(D/25 ft)\n"
                      "PPV(D) = PPV,ref · (25 ft/D)^n   [in/s]\n"
                      "FTA Report No. 0123 (2018), sección 7.2.")
        nota.setStyleSheet("font-family:monospace;color:#5a6570;")
        v.addWidget(nota)
        v.addStretch()
        return w

    def _tab_focos(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.tabla_f = QTableWidget(0, 5)
        self.tabla_f.setHorizontalHeaderLabels(
            ["✓", "Nombre", "Equipo (FTA, Tabla 7-4)", "Lv 25 ft (VdB)", "PPV 25 ft (in/s)"])
        h = self.tabla_f.horizontalHeader()
        h.setSectionResizeMode(F_EQ, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(F_ACT, QHeaderView.ResizeMode.ResizeToContents)
        self.tabla_f.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla_f.horizontalHeaderItem(F_NOM).setToolTip("Doble clic para renombrar.")
        v.addWidget(self.tabla_f)

        fila = QHBoxLayout()
        self.btn_cargar_f = QPushButton("Cargar desde capa")
        self.btn_medicion = QPushButton("Desde medición…")
        self.btn_medicion.setToolTip("Calcula Lv y PPV a 25 ft desde un PPV medido (mm/s) a cierta distancia.")
        fila.addWidget(self.btn_cargar_f)
        fila.addWidget(self.btn_medicion)
        v.addLayout(fila)

        f = QFormLayout()
        self.cmb_foco_activo = QComboBox()
        f.addRow("Foco a graficar (iterativo):", self.cmb_foco_activo)
        v.addLayout(f)
        return w

    def _tab_receptores(self):
        w = QWidget()
        v = QVBoxLayout(w)
        gb = QGroupBox("Criterio por defecto")
        f = QFormLayout(gb)
        self.cmb_def_lv = self._combo_criterio(FTA.CRIT_LV, FTA.CRIT_LV_DEF, "VdB")
        self.cmb_def_ppv = self._combo_criterio(FTA.CRIT_PPV, FTA.CRIT_PPV_DEF, "in/s")
        self.btn_crit_todos = QPushButton("Aplicar a todos los receptores")
        f.addRow("Molestia (Tabla 6-3):", self.cmb_def_lv)
        f.addRow("Daño (Tabla 7-5):", self.cmb_def_ppv)
        f.addRow(self.btn_crit_todos)
        v.addWidget(gb)

        self.tabla_r = QTableWidget(0, 5)
        self.tabla_r.setHorizontalHeaderLabels(
            ["Nombre", "Molestia", "Lím. (VdB)", "Daño", "Lím. (in/s)"])
        h = self.tabla_r.horizontalHeader()
        h.setSectionResizeMode(R_CLV, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(R_CPPV, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tabla_r)
        self.btn_cargar_r = QPushButton("Cargar desde capa")
        v.addWidget(self.btn_cargar_r)
        return w

    def _tab_isolineas(self):
        w = QWidget()
        v = QVBoxLayout(w)
        gb = QGroupBox("Escala de niveles (VdB)")
        f = QFormLayout(gb)
        self.spin_nmin = QSpinBox()
        self.spin_nmin.setRange(1, 199)
        self.spin_nmin.setValue(FTA.ESCALA_DEF['minimo'])
        self.spin_paso = QSpinBox()
        self.spin_paso.setRange(1, 50)
        self.spin_paso.setValue(FTA.ESCALA_DEF['paso'])
        self.spin_ncol = QSpinBox()
        self.spin_ncol.setRange(1, 16)
        self.spin_ncol.setValue(FTA.ESCALA_DEF['n_colores'])
        f.addRow("Nivel mínimo:", self.spin_nmin)
        f.addRow("Cambio de color cada:", self.spin_paso)
        f.addRow("Cantidad de colores:", self.spin_ncol)
        v.addWidget(gb)

        gb = QGroupBox("Colores")
        f = QFormLayout(gb)
        self.cmb_escala = QComboBox()
        for clave, nombre, _ in FTA.ESCALAS_COLOR:
            self.cmb_escala.addItem(nombre, clave)
        self.btn_col_ini = QgsColorButton()
        self.btn_col_ini.setColor(QColor(FTA.COLOR_CUSTOM_DEF[0]))
        self.btn_col_fin = QgsColorButton()
        self.btn_col_fin.setColor(QColor(FTA.COLOR_CUSTOM_DEF[1]))
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Inicial:"))
        fila.addWidget(self.btn_col_ini)
        fila.addWidget(QLabel("Final:"))
        fila.addWidget(self.btn_col_fin)
        self.spin_opac = QDoubleSpinBox()
        self.spin_opac.setRange(0.1, 1.0)
        self.spin_opac.setSingleStep(0.05)
        self.spin_opac.setValue(0.5)
        f.addRow("Escala:", self.cmb_escala)
        f.addRow(fila)
        f.addRow("Opacidad del relleno:", self.spin_opac)
        v.addWidget(gb)

        # Vista previa de la escala resultante (niveles + colores).
        self.lbl_preview = QLabel()
        self.lbl_preview.setWordWrap(True)
        v.addWidget(self.lbl_preview)
        nota = QLabel("Las bandas van de un nivel al siguiente; la última es «≥ nivel máximo».\n"
                      "Leyenda y etiquetas en enteros; el título indica la unidad (VdB).")
        nota.setWordWrap(True)
        nota.setStyleSheet("color:#5a6570;")
        v.addWidget(nota)
        v.addStretch()
        return w

    def _tab_resultados(self):
        w = QWidget()
        v = QVBoxLayout(w)
        self.lbl_resumen = QLabel("Sin cálculo.")
        self.lbl_resumen.setWordWrap(True)
        v.addWidget(self.lbl_resumen)
        self.tabla_res = QTableWidget(0, 10)
        self.tabla_res.setHorizontalHeaderLabels(
            ["Receptor", "Dist. (m)", "Fuente cercana", "Lv (VdB)", "Lím.",
             "Molestia", "PPV (in/s)", "PPV (mm/s)", "Lím.", "Daño"])
        self.tabla_res.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_res.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        v.addWidget(self.tabla_res, 3)
        v.addWidget(QLabel("Memoria de cálculo (receptor seleccionado):"))
        self.txt_memoria = QPlainTextEdit()
        self.txt_memoria.setReadOnly(True)
        self.txt_memoria.setStyleSheet("font-family:monospace;font-size:11px;")
        v.addWidget(self.txt_memoria, 2)
        v.addWidget(QLabel("Alcance por nivel:"))
        self.tabla_alc = QTableWidget(0, 3)
        self.tabla_alc.setHorizontalHeaderLabels(["Nivel (VdB)", "Radio (m)", "Área (ha)"])
        self.tabla_alc.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_alc.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tabla_alc, 2)
        return w

    # ==================================================================
    # Señales
    # ==================================================================
    def _connect_signals(self):
        self.cmb_focos.layerChanged.connect(self._on_capa_focos_cambia)
        self.cmb_rec.layerChanged.connect(self._on_capa_rec_cambia)
        self.cmb_modo.currentIndexChanged.connect(self._on_modo_cambia)
        self.chk_huso_auto.toggled.connect(lambda on: self.spin_epsg.setEnabled(not on))
        self.btn_cargar_f.clicked.connect(self._cargar_focos)
        self.btn_cargar_r.clicked.connect(self._cargar_receptores)
        self.btn_medicion.clicked.connect(self._dialogo_medicion)
        self.btn_crit_todos.clicked.connect(self._criterio_a_todos)
        self.tabla_f.itemChanged.connect(self._on_item_foco)
        self.tabla_res.currentCellChanged.connect(self._on_res_sel)
        self.btn_calcular.clicked.connect(self._calcular)
        self.btn_exportar.clicked.connect(self._exportar)
        # Escala: cualquier cambio refresca la vista previa.
        for sp in (self.spin_nmin, self.spin_paso, self.spin_ncol):
            sp.valueChanged.connect(self._actualizar_preview)
        self.cmb_escala.currentIndexChanged.connect(self._actualizar_preview)
        self.btn_col_ini.colorChanged.connect(self._actualizar_preview)
        self.btn_col_fin.colorChanged.connect(self._actualizar_preview)
        self._actualizar_preview()

    def _on_capa_focos_cambia(self, layer):
        # Los 3 modos sirven para puntos y para áreas: el selector no cambia.
        self._on_modo_cambia()
        if layer is None or layer.id() != self._capa_focos_id:
            self.tabla_f.setRowCount(0)
            self.cmb_foco_activo.clear()
            self._capa_focos_id = None

    def _on_capa_rec_cambia(self, layer):
        if layer is None or layer.id() != self._capa_rec_id:
            self.tabla_r.setRowCount(0)
            self._capa_rec_id = None

    def _on_modo_cambia(self, *_):
        clave = self.cmb_modo.currentData()
        self.cmb_foco_activo.setEnabled(clave == "iterativo")
        self.lbl_modo.setText(next((a for _, c, a in MODOS if c == clave), ""))

    # ---- Escala de colores ---------------------------------------------
    def _paleta(self):
        """Paradas de color de la escala elegida (None = por defecto)."""
        clave = self.cmb_escala.currentData()
        es_custom = clave == 'custom'
        self.btn_col_ini.setEnabled(es_custom)
        self.btn_col_fin.setEnabled(es_custom)
        if es_custom:
            return [self.btn_col_ini.color().name(), self.btn_col_fin.color().name()]
        return next((p for c, _, p in FTA.ESCALAS_COLOR if c == clave), None)

    def _niveles(self):
        return FTA.niveles_escala(self.spin_nmin.value(), self.spin_paso.value(),
                                  self.spin_ncol.value())

    def _actualizar_preview(self, *_):
        from .calculos_core import color_banda, rango_txt
        niv, pal = self._niveles(), self._paleta()
        n = len(niv)
        celdas = []
        for i in range(n - 1, -1, -1):          # más alto arriba, como la leyenda
            rango = rango_txt(niv[i], niv[i + 1] if i + 1 < n else None)
            celdas.append(
                f"<tr><td style='background:{color_banda(i, n, pal)};width:26px;"
                f"border:1px solid #000'>&nbsp;</td><td>&nbsp;{rango}</td></tr>")
        self.lbl_preview.setText(
            "<b>Nivel de Vibración VdB</b><table cellspacing='2'>"
            + "".join(celdas) + "</table>")

    def _on_item_foco(self, item):
        if item.column() == F_NOM and item.row() < self.cmb_foco_activo.count():
            self.cmb_foco_activo.setItemText(item.row(), item.text().strip())

    # ==================================================================
    # Lectura de capas
    # ==================================================================
    @staticmethod
    def _campo(layer, *candidatos):
        nombres = {f.name().lower(): f.name() for f in layer.fields()}
        for c in candidatos:
            if c in nombres:
                return nombres[c]
        return None

    def _epsg(self):
        """EPSG UTM: fijo, o detectado desde el centro de los focos."""
        if not self.chk_huso_auto.isChecked():
            return self.spin_epsg.value()
        capa = self.cmb_focos.currentLayer() or self.cmb_rec.currentLayer()
        if capa is None or capa.extent().isEmpty():
            return self.spin_epsg.value()
        tr = QgsCoordinateTransform(capa.crs(), QgsCoordinateReferenceSystem("EPSG:4326"),
                                    QgsProject.instance())
        c = tr.transform(capa.extent().center())
        zona = int(((c.x() + 180) % 360) // 6) + 1
        epsg = (32700 if c.y() < 0 else 32600) + zona
        self.spin_epsg.setValue(epsg)
        return epsg

    def _leer_capa(self, layer, epsg):
        """[{fid, nombre, x, y, geom?(shapely), atributos}] en UTM `epsg`."""
        from shapely import wkt as swkt
        tr = None
        destino = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
        if layer.crs() != destino:
            tr = QgsCoordinateTransform(layer.crs(), destino, QgsProject.instance())
        campo_nom = self._campo(layer, "name", "nombre", "id")
        area = compat.es_poligono(layer)
        out = []
        for i, feat in enumerate(layer.getFeatures()):
            g = QgsGeometry(feat.geometry())
            if g.isEmpty():
                continue
            if tr is not None:
                g.transform(tr)
            valor = feat[campo_nom] if campo_nom else None
            nombre = (str(valor) if not _es_nulo(valor)
                      else f"{'A' if area else 'P'}{i + 1}")
            d = {'fid': feat.id(), 'nombre': nombre, 'feat': feat}
            if area:
                a = g.constGet().segmentize()
                a.dropZValue()
                a.dropMValue()
                shp = swkt.loads(a.asWkt())
                if not shp.is_valid:
                    shp = shp.buffer(0)
                if shp.is_empty:
                    continue
                d.update(geom=shp, x=shp.centroid.x, y=shp.centroid.y)
            else:
                p = g.asPoint() if not g.isMultipart() else g.centroid().asPoint()
                d.update(x=p.x(), y=p.y())
            out.append(d)
        return out

    # ==================================================================
    # Tabla de focos
    # ==================================================================
    def _item(self, texto, editable=True):
        it = QTableWidgetItem("" if texto is None else str(texto))
        if not editable:
            it.setFlags(it.flags() & ~EDITABLE)
            it.setBackground(GRIS)
        return it

    def _combo_equipo(self, ident):
        cb = QComboBox()
        for e in FTA.EQUIPOS:
            cb.addItem(f"{e['nombre']} — {e['lv']} VdB", e['id'])
        cb.addItem("Personalizado / nivel medido", FTA.EQUIPO_PERSONALIZADO)
        i = cb.findData(ident)
        cb.setCurrentIndex(i if i >= 0 else cb.count() - 1)
        cb.currentIndexChanged.connect(lambda _=0, c=cb: self._on_equipo(c))
        return cb

    def _fila_de_widget(self, tabla, col, widget):
        for r in range(tabla.rowCount()):
            if tabla.cellWidget(r, col) is widget:
                return r
        return -1

    def _on_equipo(self, cb):
        r = self._fila_de_widget(self.tabla_f, F_EQ, cb)
        if r < 0:
            return
        ident = cb.currentData()
        eq = FTA.equipo(ident)
        cu = eq is None
        lv = self.tabla_f.item(r, F_LV).text() if cu else eq['lv']
        ppv = self.tabla_f.item(r, F_PPV).text() if cu else eq['ppv']
        self.tabla_f.setItem(r, F_LV, self._item(lv, cu))
        self.tabla_f.setItem(r, F_PPV, self._item(ppv, cu))

    def _cargar_focos(self):
        layer = self.cmb_focos.currentLayer()
        if layer is None:
            QMessageBox.warning(self, "Sin capa", "Seleccione la capa de focos.")
            return False
        focos = self._leer_capa(layer, self._epsg())
        c_lv = self._campo(layer, "lv_25ft", "lv_ref", "vdb_ref", "lv")
        c_ppv = self._campo(layer, "ppv_25ft", "ppv_ref", "ppv")
        c_eq = self._campo(layer, "equipo")
        c_act = self._campo(layer, "activa")

        self.tabla_f.blockSignals(True)
        self.tabla_f.setRowCount(len(focos))
        for r, f in enumerate(focos):
            feat = f['feat']
            eq_id = FTA.EQUIPO_DEF
            if c_eq and not _es_nulo(feat[c_eq]):
                v = str(feat[c_eq])
                m = next((e for e in FTA.EQUIPOS if v in (e['id'], e['nombre'])), None)
                eq_id = m['id'] if m else FTA.EQUIPO_PERSONALIZADO
            lv = _num(feat[c_lv]) if c_lv else None
            ppv = _num(feat[c_ppv]) if c_ppv else None
            if lv is not None:
                eq_id = next((e['id'] for e in FTA.EQUIPOS
                              if e['lv'] == lv and (ppv is None or e['ppv'] == ppv)),
                             FTA.EQUIPO_PERSONALIZADO)
            eq = FTA.equipo(eq_id)
            if eq is not None:
                lv, ppv = eq['lv'], eq['ppv']
            act = self._item("", True)
            act.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            v_act = feat[c_act] if c_act else None
            activa = _es_nulo(v_act) or str(v_act).strip().lower() not in ("0", "false", "no")
            act.setCheckState(Qt.CheckState.Checked if activa else Qt.CheckState.Unchecked)
            act.setData(ROL, f['fid'])
            self.tabla_f.setItem(r, F_ACT, act)
            self.tabla_f.setItem(r, F_NOM, self._item(f['nombre']))
            self.tabla_f.setCellWidget(r, F_EQ, self._combo_equipo(eq_id))
            cu = eq is None
            self.tabla_f.setItem(r, F_LV, self._item(lv if lv is not None else 80, cu))
            self.tabla_f.setItem(r, F_PPV, self._item(ppv if ppv is not None else 0.1, cu))
        self.tabla_f.blockSignals(False)

        self.cmb_foco_activo.clear()
        self.cmb_foco_activo.addItems([f['nombre'] for f in focos])
        self._capa_focos_id = layer.id()
        return True

    def _dialogo_medicion(self):
        r = self.tabla_f.currentRow()
        if r < 0:
            QMessageBox.information(self, "Medición", "Seleccione primero un foco en la tabla.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Calcular desde una medición de PPV")
        f = QFormLayout(dlg)
        sp_ppv = QDoubleSpinBox()
        sp_ppv.setDecimals(4)
        sp_ppv.setRange(0.0001, 1000)
        sp_ppv.setValue(1.0)
        sp_d = QDoubleSpinBox()
        sp_d.setDecimals(2)
        sp_d.setRange(0.1, 10000)
        sp_d.setValue(10.0)
        f.addRow("PPV medido (mm/s):", sp_ppv)
        f.addRow("Distancia de medición (m):", sp_d)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if not dlg.exec():   # int en PyQt5/PyQt6 (1 = aceptado)
            return
        ppv = FTA.ppv_ref_desde_medicion(sp_ppv.value(), sp_d.value(), self.spin_n.value())
        lv = round(FTA.lv_desde_ppv(ppv), 1)
        cb = self.tabla_f.cellWidget(r, F_EQ)
        cb.blockSignals(True)
        cb.setCurrentIndex(cb.findData(FTA.EQUIPO_PERSONALIZADO))
        cb.blockSignals(False)
        self.tabla_f.setItem(r, F_LV, self._item(lv, True))
        self.tabla_f.setItem(r, F_PPV, self._item(float(f"{ppv:.4g}"), True))
        self.lbl_estado.setText(f"Lv a 25 ft = {_f(lv)} VdB · PPV a 25 ft = {ppv:.4g} in/s")

    # ==================================================================
    # Tabla de receptores
    # ==================================================================
    def _combo_criterio(self, lista, ident, unidad, al_cambiar=None):
        cb = QComboBox()
        for o in lista:
            txt = f"{o['nombre']} — {o['lim']:g} {unidad}" if o['lim'] is not None else o['nombre']
            cb.addItem(txt, o['id'])
        i = cb.findData(ident)
        cb.setCurrentIndex(max(0, i))
        if al_cambiar:
            cb.currentIndexChanged.connect(lambda _=0, c=cb: al_cambiar(c))
        return cb

    def _on_crit(self, cb, col_combo, col_lim, buscar):
        r = self._fila_de_widget(self.tabla_r, col_combo, cb)
        if r < 0:
            return
        ident = cb.currentData()
        o = buscar(ident)
        custom = ident == 'custom'
        prev = self.tabla_r.item(r, col_lim)
        val = (prev.text() if (custom and prev) else
               "" if (o is None or o['lim'] is None) else o['lim'])
        self.tabla_r.setItem(r, col_lim, self._item(val, custom))

    def _poner_criterios(self, r, id_lv, id_ppv, lim_lv=None, lim_ppv=None):
        cb_lv = self._combo_criterio(FTA.CRIT_LV, id_lv, "VdB",
                                     lambda c: self._on_crit(c, R_CLV, R_LLV, FTA.crit_lv))
        cb_ppv = self._combo_criterio(FTA.CRIT_PPV, id_ppv, "in/s",
                                      lambda c: self._on_crit(c, R_CPPV, R_LPPV, FTA.crit_ppv))
        self.tabla_r.setCellWidget(r, R_CLV, cb_lv)
        self.tabla_r.setCellWidget(r, R_CPPV, cb_ppv)
        o = FTA.crit_lv(id_lv)
        v = lim_lv if id_lv == 'custom' else (o['lim'] if o and o['lim'] is not None else "")
        self.tabla_r.setItem(r, R_LLV, self._item(v, id_lv == 'custom'))
        o = FTA.crit_ppv(id_ppv)
        v = lim_ppv if id_ppv == 'custom' else (o['lim'] if o and o['lim'] is not None else "")
        self.tabla_r.setItem(r, R_LPPV, self._item(v, id_ppv == 'custom'))

    def _cargar_receptores(self):
        layer = self.cmb_rec.currentLayer()
        if layer is None:
            QMessageBox.warning(self, "Sin capa", "Seleccione la capa de receptores.")
            return False
        recs = self._leer_capa(layer, self._epsg())
        c_llv = self._campo(layer, "lim_vdb", "lim_lv")
        c_lppv = self._campo(layer, "lim_ppv")
        d_lv, d_ppv = self.cmb_def_lv.currentData(), self.cmb_def_ppv.currentData()
        self.tabla_r.setRowCount(len(recs))
        for r, rec in enumerate(recs):
            it = self._item(rec['nombre'])
            it.setData(ROL, rec['fid'])
            self.tabla_r.setItem(r, R_NOM, it)
            feat = rec['feat']
            llv = _num(feat[c_llv]) if c_llv else None
            lppv = _num(feat[c_lppv]) if c_lppv else None
            id_lv = d_lv if llv is None else next(
                (o['id'] for o in FTA.CRIT_LV if o['lim'] == llv), 'custom')
            id_ppv = d_ppv if lppv is None else next(
                (o['id'] for o in FTA.CRIT_PPV if o['lim'] == lppv), 'custom')
            self._poner_criterios(r, id_lv, id_ppv, llv, lppv)
        self._capa_rec_id = layer.id()
        return True

    def _criterio_a_todos(self):
        if self.tabla_r.rowCount() == 0 and not self._cargar_receptores():
            return
        for r in range(self.tabla_r.rowCount()):
            self._poner_criterios(r, self.cmb_def_lv.currentData(),
                                  self.cmb_def_ppv.currentData())

    # ==================================================================
    # Cálculo
    # ==================================================================
    @staticmethod
    def _nombre_proyecto():
        """Nombre del proyecto QGIS abierto (título, o nombre del .qgz)."""
        p = QgsProject.instance()
        return (p.title() or p.baseName() or "").strip()

    def _datos_focos(self, capa, epsg):
        por_fid = {f['fid']: f for f in self._leer_capa(capa, epsg)}
        pts, nombres = [], set()
        for r in range(self.tabla_f.rowCount()):
            act = self.tabla_f.item(r, F_ACT)
            nombre = (self.tabla_f.item(r, F_NOM).text() or "").strip()
            if not nombre or nombre in nombres:
                raise ValueError(f"Fila {r + 1}: el nombre del foco está vacío o repetido.")
            nombres.add(nombre)
            f = por_fid.get(act.data(ROL))
            if f is None:
                raise ValueError(f"El foco '{nombre}' ya no existe en la capa. "
                                 "Pulse «Cargar desde capa».")
            lv = _num(self.tabla_f.item(r, F_LV).text())
            ppv = _num(self.tabla_f.item(r, F_PPV).text())
            if lv is None:
                raise ValueError(f"Foco '{nombre}': Lv a 25 ft inválido.")
            cb = self.tabla_f.cellWidget(r, F_EQ)
            eq = FTA.equipo(cb.currentData())
            d = {k: v for k, v in f.items() if k != 'feat'}
            d.update(nombre=nombre, lv=lv, ppv=ppv,
                     activa=act.checkState() == Qt.CheckState.Checked,
                     equipo=eq['nombre'] if eq else "Personalizado")
            pts.append(d)
        return pts

    def _datos_receptores(self, capa, epsg):
        por_fid = {f['fid']: f for f in self._leer_capa(capa, epsg)}
        pts = []
        for r in range(self.tabla_r.rowCount()):
            it = self.tabla_r.item(r, R_NOM)
            rec = por_fid.get(it.data(ROL))
            if rec is None:
                raise ValueError("La tabla de receptores no coincide con la capa. "
                                 "Pulse «Cargar desde capa».")
            pts.append({'fid': rec['fid'], 'nombre': it.text().strip() or rec['nombre'],
                        'x': rec['x'], 'y': rec['y'],
                        'lim_lv': _num(self.tabla_r.item(r, R_LLV).text()),
                        'lim_ppv': _num(self.tabla_r.item(r, R_LPPV).text())})
        return pts

    def _calcular(self):
        capa_f, capa_r = self.cmb_focos.currentLayer(), self.cmb_rec.currentLayer()
        if capa_f is None or capa_r is None:
            QMessageBox.warning(self, "Faltan capas", "Seleccione las capas de focos y receptores.")
            self.tabs.setCurrentIndex(0)
            return
        epsg = self._epsg()
        if self.tabla_f.rowCount() == 0 or self._capa_focos_id != capa_f.id():
            if not self._cargar_focos():
                return
        if self.tabla_r.rowCount() == 0 or self._capa_rec_id != capa_r.id():
            if not self._cargar_receptores():
                return
        niveles = self._niveles()
        if not niveles:
            QMessageBox.warning(self, "Niveles", "Defina al menos un nivel de isolínea.")
            self.tabs.setCurrentIndex(3)
            return
        try:
            pts_f = self._datos_focos(capa_f, epsg)
            pts_r = self._datos_receptores(capa_r, epsg)
            modo = self.cmb_modo.currentData()
            res = calcular_todo(
                pts_f=pts_f, pts_r=pts_r, niveles=niveles,
                n_exp=self.spin_n.value(), resolucion=self.spin_res.value(),
                epsg=epsg, modo=modo,
                foco_idx=max(0, self.cmb_foco_activo.currentIndex()),
                paleta=self._paleta(), acotar_dref=self.chk_acotar.isChecked(),
                unidad_ppv="in/s")
        except Exception as e:
            QMessageBox.critical(self, "Error de cálculo", str(e))
            return

        activas = [f for f in res['fuentes'] if f['activa']]
        if len(activas) == 1:
            ctx = {'sumado': False, 'lv_ref': activas[0]['lv_25ft'], 'foco': activas[0]['nombre']}
        else:
            ctx = {'sumado': True, 'n_focos': len(activas), 'modo': res['modo']}
        self.resultado = res
        self._meta = {'proyecto': self._nombre_proyecto(),
                      'escenario': self.txt_escenario.text().strip() or "Situación 1",
                      'ctx_leyenda': ctx, 'modo': res['modo'], 'n_exp': self.spin_n.value(),
                      'acotar': self.chk_acotar.isChecked(), 'epsg': epsg,
                      'opacidad': self.spin_opac.value()}
        self._dibujar(res, epsg)
        self._mostrar_resultados(res)
        self.btn_exportar.setEnabled(True)

    # ==================================================================
    # Dibujo en el proyecto
    # ==================================================================
    def _dibujar(self, res, epsg):
        proyecto = QgsProject.instance()
        nombre = f"Vibraciones · {self._meta['escenario']}"
        raiz = proyecto.layerTreeRoot()
        g = raiz.findGroup(nombre)
        if g is not None:
            for nodo in g.findLayers():
                proyecto.removeMapLayer(nodo.layerId())
            g.parent().removeChildNode(g)
        g = raiz.insertGroup(0, nombre)
        capas = construir_capas(res, epsg, self._meta['escenario'], self._meta['opacidad'])
        for k in ('receptores', 'fuentes', 'isolineas', 'areas_dispersion'):
            proyecto.addMapLayer(capas[k], False)
            g.addLayer(capas[k])
        self.iface.mapCanvas().refresh()

    def _mostrar_resultados(self, res):
        recs = res['receptores']
        n_bad = sum(1 for r in recs if r['estado'] == 'bad')
        n_act = res['n_activas']
        extra = (f" · Malla ajustada a {res['resolucion_usada']:g} m"
                 if res['resolucion_usada'] and res['resolucion_usada'] > self.spin_res.value() + 1e-9
                 else "")
        self.lbl_resumen.setText(
            f"{n_act} foco(s) activo(s) · {len(recs)} receptor(es) · "
            f"{n_bad} no cumple(n) · modo {res['modo']}{extra}")
        self.lbl_estado.setText(f"Dibujado en el grupo «Vibraciones · {self._meta['escenario']}».")

        t = self.tabla_res
        t.setRowCount(len(recs))
        for i, r in enumerate(recs):
            vals = [r['nombre'], _f(r['dist_m']), r['fte_cerc'] or "", _f(r['lv_vdb']),
                    "" if r['lim_vdb'] is None else f"{r['lim_vdb']:g}", r['eval_lv'] or "—",
                    "—" if r['ppv_ins'] is None else f"{r['ppv_ins']:.4g}",
                    "—" if r['ppv_mms'] is None else f"{r['ppv_mms']:.4g}",
                    "" if r['lim_ppv'] is None else f"{r['lim_ppv']:g}", r['eval_ppv'] or "—"]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if j in (5, 9) and v in ('Cumple', 'No cumple'):
                    it.setForeground(QColor(COLOR_ESTADO['ok' if v == 'Cumple' else 'bad']))
                t.setItem(i, j, it)
        t.resizeColumnsToContents()
        if recs:
            t.selectRow(0)
            self.txt_memoria.setPlainText(recs[0]['memoria'])

        a = self.tabla_alc
        a.setRowCount(len(res['alcance']))
        for i, o in enumerate(res['alcance']):
            a.setItem(i, 0, QTableWidgetItem(f"{o['lv_vdb']}"))
            a.setItem(i, 1, QTableWidgetItem(_f(o['radio_m'])))
            a.setItem(i, 2, QTableWidgetItem(_f(o['area_ha'], 2)))
        self.tabs.setCurrentIndex(4)

    def _on_res_sel(self, fila, *_):
        if self.resultado and 0 <= fila < len(self.resultado['receptores']):
            self.txt_memoria.setPlainText(self.resultado['receptores'][fila]['memoria'])

    # ==================================================================
    # Exportación
    # ==================================================================
    def _exportar(self):
        if not self.resultado:
            return
        from .exportar import exportar_todo

        dlg = QDialog(self)
        dlg.setWindowTitle("Exportar resultados")
        v = QVBoxLayout(dlg)
        chks = {}
        for k, txt in (('gpkg', "GeoPackage con estilos embebidos (+ QML)"),
                       ('shp', "Shapefiles con QML homónimo"),
                       ('kmz', "KMZ para Google Earth (con leyenda)"),
                       ('csv', "CSV: memoria de cálculo y resumen por receptor")):
            c = QCheckBox(txt)
            c.setChecked(True)
            v.addWidget(c)
            chks[k] = c
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                              | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if not dlg.exec():   # int en PyQt5/PyQt6 (1 = aceptado)
            return
        opciones = {k for k, c in chks.items() if c.isChecked()}

        sugerido = _slug(f"{self._meta['proyecto']}_{self._meta['escenario']}") + ".gpkg"
        ruta, _ = QFileDialog.getSaveFileName(self, "Nombre base de los archivos",
                                              sugerido, "GeoPackage (*.gpkg)")
        if not ruta:
            return
        ruta_base = os.path.splitext(ruta)[0]
        try:
            # Capas nuevas desde el resultado: no depende de que el usuario
            # conserve (o haya editado) las capas del mapa.
            capas = construir_capas(self.resultado, self._meta['epsg'],
                                    self._meta['escenario'], self._meta['opacidad'])
            msgs = exportar_todo(capas, self.resultado, ruta_base, opciones,
                                 self._meta['epsg'], self._meta)
        except Exception as e:
            QMessageBox.critical(self, "Error al exportar", str(e))
            return
        texto = "\n".join(("✔ " if ok else "⚠ ") + m for ok, m in msgs)
        caja = (QMessageBox.information if all(ok for ok, _ in msgs)
                else QMessageBox.warning)
        caja(self, "Exportación", texto)
