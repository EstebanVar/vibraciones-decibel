# compat.py
"""
Capa de compatibilidad QGIS 3.28 … 4.x  (Qt5/PyQt5  y  Qt6/PyQt6).

Reglas que sigue todo el plugin:
  * Enums de Qt SIEMPRE con nombre completo (Qt.ItemFlag.ItemIsEditable,
    Qt.ItemDataRole.UserRole, …): funciona en PyQt5 5.15 y en PyQt6.
  * Enums de QGIS que cambiaron de lugar (QgsWkbTypes -> Qgis.GeometryType,
    QgsUnitTypes -> Qgis.RenderUnit, QgsPalLayerSettings.* -> Qgis.Label*,
    QgsMapLayerProxyModel.* -> Qgis.LayerFilter, …) se resuelven aquí,
    probando primero la ruta nueva y luego la antigua.
  * Los campos de capas en memoria se declaran en la URI
    ("…&field=nombre:string(50)"), así no se usa QVariant/QMetaType.
  * dialog.exec() (no exec_()).
"""

from qgis.core import (Qgis, QgsWkbTypes, QgsUnitTypes, QgsPalLayerSettings,
                       QgsMapLayerProxyModel, QgsVectorFileWriter)

try:  # Qt6: QAction vive en QtGui
    from qgis.PyQt.QtGui import QAction  # noqa: F401
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401


def resolver(*candidatos):
    """
    Devuelve el primer atributo que exista. Cada candidato es (objeto,
    'Ruta.Con.Puntos'). Ej.: resolver((Qgis, 'RenderUnit.Millimeters'),
    (QgsUnitTypes, 'RenderMillimeters')).
    """
    for base, ruta in candidatos:
        obj = base
        try:
            for parte in ruta.split('.'):
                obj = getattr(obj, parte)
            return obj
        except AttributeError:
            continue
    raise AttributeError("Ningún candidato disponible: "
                         + ", ".join(r for _, r in candidatos))


def version_qgis():
    v = getattr(Qgis, 'QGIS_VERSION_INT', None)
    if v is None and hasattr(Qgis, 'versionInt'):
        v = Qgis.versionInt()
    return int(v or 0)


def valor_enum(e):
    """int de un enum en PyQt5 (int) o PyQt6 (Enum con .value)."""
    return getattr(e, 'value', e)


# --- Geometría ----------------------------------------------------------
GEOM_PUNTO = resolver((Qgis, 'GeometryType.Point'),
                      (QgsWkbTypes, 'PointGeometry'))
GEOM_POLIGONO = resolver((Qgis, 'GeometryType.Polygon'),
                         (QgsWkbTypes, 'PolygonGeometry'))


def es_poligono(layer):
    return layer is not None and layer.geometryType() == GEOM_POLIGONO


# --- Filtros de QgsMapLayerComboBox -------------------------------------
FILTRO_PUNTOS = resolver((Qgis, 'LayerFilter.PointLayer'),
                         (QgsMapLayerProxyModel, 'PointLayer'))
FILTRO_POLIGONOS = resolver((Qgis, 'LayerFilter.PolygonLayer'),
                            (QgsMapLayerProxyModel, 'PolygonLayer'))

# --- Unidades de render -------------------------------------------------
MM = resolver((Qgis, 'RenderUnit.Millimeters'),
              (QgsUnitTypes, 'RenderMillimeters'))
PUNTOS = resolver((Qgis, 'RenderUnit.Points'),
                  (QgsUnitTypes, 'RenderPoints'))

# --- Etiquetado ---------------------------------------------------------
ETQ_SOBRE_PUNTO = resolver((Qgis, 'LabelPlacement.OverPoint'),
                           (QgsPalLayerSettings, 'OverPoint'))
ETQ_LINEA = resolver((Qgis, 'LabelPlacement.Line'),
                     (QgsPalLayerSettings, 'Line'))
ETQ_CURVA = resolver((Qgis, 'LabelPlacement.Curved'),
                     (QgsPalLayerSettings, 'Curved'))
CUADRANTE_ARRIBA_IZQ = resolver((Qgis, 'LabelQuadrantPosition.AboveLeft'),
                                (QgsPalLayerSettings, 'QuadrantAboveLeft'))
CUADRANTE_DERECHA = resolver((Qgis, 'LabelQuadrantPosition.Right'),
                             (QgsPalLayerSettings, 'QuadrantRight'))

# --- Escritura de archivos ---------------------------------------------
SOBRESCRIBIR_ARCHIVO = resolver(
    (QgsVectorFileWriter, 'ActionOnExistingFile.CreateOrOverwriteFile'),
    (QgsVectorFileWriter, 'CreateOrOverwriteFile'))
SOBRESCRIBIR_CAPA = resolver(
    (QgsVectorFileWriter, 'ActionOnExistingFile.CreateOrOverwriteLayer'),
    (QgsVectorFileWriter, 'CreateOrOverwriteLayer'))
SIN_ERROR = resolver((QgsVectorFileWriter, 'WriterError.NoError'),
                     (QgsVectorFileWriter, 'NoError'))


def escribir_capa(capa, ruta, driver, nombre_capa=None, sobrescribir_archivo=True,
                  contexto=None):
    """
    Escribe una capa con QgsVectorFileWriter (V3 en 3.20+; V2 de respaldo).
    Devuelve (ok, mensaje).
    """
    from qgis.core import QgsProject
    op = QgsVectorFileWriter.SaveVectorOptions()
    op.driverName = driver
    op.fileEncoding = "UTF-8"
    if nombre_capa:
        op.layerName = nombre_capa
    op.actionOnExistingFile = (SOBRESCRIBIR_ARCHIVO if sobrescribir_archivo
                               else SOBRESCRIBIR_CAPA)
    ctx = contexto or QgsProject.instance().transformContext()
    if hasattr(QgsVectorFileWriter, 'writeAsVectorFormatV3'):
        res = QgsVectorFileWriter.writeAsVectorFormatV3(capa, ruta, ctx, op)
    else:  # pragma: no cover
        res = QgsVectorFileWriter.writeAsVectorFormatV2(capa, ruta, ctx, op)
    codigo = res[0] if isinstance(res, (tuple, list)) else res
    ok = codigo == SIN_ERROR or valor_enum(codigo) == 0
    msg = res[1] if isinstance(res, (tuple, list)) and len(res) > 1 else ""
    return ok, str(msg)


def guardar_estilo_en_bd(capa, nombre, descripcion=""):
    """
    Guarda el estilo de `capa` como predeterminado dentro de su fuente
    (GeoPackage). Al volver a cargar la capa en QGIS, el estilo se aplica
    solo. Devuelve (ok, mensaje).
    """
    try:
        if hasattr(capa, 'saveStyleToDatabaseV2'):
            res = capa.saveStyleToDatabaseV2(nombre, descripcion, True, "")
            msg = res[1] if isinstance(res, (tuple, list)) else ""
            return (not msg), str(msg)
        msg = capa.saveStyleToDatabase(nombre, descripcion, True, "")
        if isinstance(msg, (tuple, list)):
            msg = msg[-1]
        return (not msg), str(msg or "")
    except Exception as e:  # pragma: no cover
        return False, str(e)
