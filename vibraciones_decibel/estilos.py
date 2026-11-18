# estilos.py
"""
Construcción de las capas de resultado (memoria) y su simbología.

Las cuatro capas usan nombres de campo de <= 10 caracteres, idénticos en
GeoPackage y Shapefile, de modo que el mismo QML sirve para ambos formatos.

    areas_dispersion  MultiPolygon  lv_min, lv_max, rango, area_ha, escenario
    isolineas         MultiLine     lv_vdb, radio_m, area_ha, escenario
    fuentes           Punto/Área    nombre, equipo, tipo, activa, lv_25ft, ppv_25ft, este, norte, escenario
    receptores        Punto         nombre, este, norte, dist_m, fte_cerc, lv_vdb, lim_vdb, eval_lv,
                                    ppv_ins, ppv_mms, lim_ppv, eval_ppv, estado, escenario
"""

from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSingleSymbolRenderer,
    QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol, QgsSimpleLineSymbolLayer,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
    QgsTextBufferSettings,
)

from . import compat

COLOR_ESTADO = {'ok': '#1b8a4c', 'bad': '#c9303e', 'na': '#6f7a85'}
TEXTO_ESTADO = {'ok': 'Cumple', 'bad': 'No cumple', 'na': 'Sin evaluar'}
COLOR_FUENTE = '#e4572e'

NOMBRES_CAPA = {
    'areas_dispersion': 'Áreas de dispersión',
    'isolineas': 'Isolíneas',
    'fuentes': 'Fuentes',
    'receptores': 'Receptores',
}

CAMPOS = {
    'areas_dispersion': [('lv_min', 'integer'), ('lv_max', 'integer'),
                         ('rango', 'string(24)'), ('area_ha', 'double(14,4)'),
                         ('escenario', 'string(60)')],
    'isolineas': [('lv_vdb', 'integer'), ('radio_m', 'double(12,2)'),
                  ('area_ha', 'double(14,4)'), ('escenario', 'string(60)')],
    'fuentes': [('nombre', 'string(60)'), ('equipo', 'string(80)'),
                ('tipo', 'string(10)'), ('activa', 'integer'),
                ('lv_25ft', 'double(8,1)'), ('ppv_25ft', 'double(10,4)'),
                ('este', 'double(12,2)'), ('norte', 'double(12,2)'),
                ('escenario', 'string(60)')],
    'receptores': [('nombre', 'string(60)'), ('este', 'double(12,2)'),
                   ('norte', 'double(12,2)'), ('dist_m', 'double(10,1)'),
                   ('fte_cerc', 'string(60)'), ('lv_vdb', 'double(8,1)'),
                   ('lim_vdb', 'double(8,1)'), ('eval_lv', 'string(12)'),
                   ('ppv_ins', 'double(12,6)'), ('ppv_mms', 'double(12,5)'),
                   ('lim_ppv', 'double(8,3)'), ('eval_ppv', 'string(12)'),
                   ('estado', 'string(4)'), ('escenario', 'string(60)')],
}

ALIAS = {
    'lv_min': 'Lv mín (VdB)', 'lv_max': 'Lv máx (VdB)', 'area_ha': 'Área (ha)',
    'lv_vdb': 'Lv (VdB)', 'radio_m': 'Radio (m)', 'lv_25ft': 'Lv a 25 ft (VdB)',
    'ppv_25ft': 'PPV a 25 ft (in/s)', 'dist_m': 'Distancia (m)',
    'fte_cerc': 'Fuente más cercana', 'lim_vdb': 'Límite Lv (VdB)',
    'eval_lv': 'Molestia', 'ppv_ins': 'PPV (in/s)', 'ppv_mms': 'PPV (mm/s)',
    'lim_ppv': 'Límite PPV (in/s)', 'eval_ppv': 'Daño estructural',
}


# ==========================================================================
# Capas en memoria
# ==========================================================================

def _capa_memoria(tipo_geom, epsg, clave, titulo):
    campos = "&".join(f"field={n}:{t}" for n, t in CAMPOS[clave])
    capa = QgsVectorLayer(f"{tipo_geom}?crs=EPSG:{epsg}&{campos}", titulo, "memory")
    for i, (n, _) in enumerate(CAMPOS[clave]):
        if n in ALIAS:
            capa.setFieldAlias(i, ALIAS[n])
    return capa


def _geom(wkt, multi=False):
    g = QgsGeometry.fromWkt(wkt)
    if multi and not g.isMultipart():
        g.convertToMultiType()
    return g


def _agregar(capa, filas):
    """filas: [(QgsGeometry, [atributos])]."""
    feats = []
    for g, attrs in filas:
        f = QgsFeature(capa.fields())
        f.setGeometry(g)
        f.setAttributes(attrs)
        feats.append(f)
    capa.dataProvider().addFeatures(feats)
    capa.updateExtents()
    return capa


def construir_capas(res, epsg, escenario, opacidad=0.5):
    """Crea y estiliza las 4 capas. Devuelve dict clave -> QgsVectorLayer."""
    capas = {}
    esc = escenario or ""

    # --- Áreas de dispersión -------------------------------------------
    c = _capa_memoria("MultiPolygon", epsg, 'areas_dispersion',
                      NOMBRES_CAPA['areas_dispersion'])
    _agregar(c, [(_geom(b['wkt'], True),
                  [b['lv_min'], b['lv_max'], b['rango'], b['area_ha'], esc])
                 for b in res['bandas']])
    estilo_areas(c, res['bandas'], opacidad)
    capas['areas_dispersion'] = c

    # --- Isolíneas -----------------------------------------------------
    c = _capa_memoria("MultiLineString", epsg, 'isolineas',
                      NOMBRES_CAPA['isolineas'])
    _agregar(c, [(_geom(i['wkt'], True),
                  [i['lv_vdb'], i['radio_m'], i['area_ha'], esc])
                 for i in res['isolineas']])
    estilo_isolineas(c, res['isolineas'])
    capas['isolineas'] = c

    # --- Fuentes (puntos o áreas, según la capa de focos) --------------
    es_area = any(f['tipo'] == 'Área' for f in res['fuentes'])
    c = _capa_memoria("MultiPolygon" if es_area else "Point", epsg, 'fuentes',
                      NOMBRES_CAPA['fuentes'])
    _agregar(c, [(_geom(f['wkt'], es_area),
                  [f['nombre'], f['equipo'], f['tipo'], int(bool(f['activa'])),
                   f['lv_25ft'], f['ppv_25ft'], f['este'], f['norte'], esc])
                 for f in res['fuentes']])
    estilo_fuentes(c, es_area)
    capas['fuentes'] = c

    # --- Receptores ----------------------------------------------------
    c = _capa_memoria("Point", epsg, 'receptores', NOMBRES_CAPA['receptores'])
    _agregar(c, [(QgsGeometry.fromPointXY(QgsPointXY(r['x'], r['y'])),
                  [r['nombre'], round(r['x'], 2), round(r['y'], 2), r['dist_m'],
                   r['fte_cerc'], r['lv_vdb'], r['lim_vdb'], r['eval_lv'],
                   r['ppv_ins'], r['ppv_mms'], r['lim_ppv'], r['eval_ppv'],
                   r['estado'], esc])
                 for r in res['receptores']])
    estilo_receptores(c)
    capas['receptores'] = c
    return capas


# ==========================================================================
# Etiquetas
# ==========================================================================

def _formato_texto(tamano=9, color='#101820', negrita=True, halo=1.0):
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(halo)
    buf.setSizeUnit(compat.MM)
    buf.setColor(QColor(255, 255, 255))
    buf.setOpacity(0.95)
    fuente = QFont()
    fuente.setBold(negrita)
    fmt = QgsTextFormat()
    fmt.setFont(fuente)
    fmt.setSize(tamano)
    fmt.setSizeUnit(compat.PUNTOS)
    fmt.setColor(QColor(color))
    fmt.setBuffer(buf)
    return fmt


def _etiquetar(capa, expresion, colocacion, tamano=9, cuadrante=None,
               dx=0.0, dy=0.0, color='#101820'):
    pal = QgsPalLayerSettings()
    pal.fieldName = expresion
    pal.isExpression = True
    pal.enabled = True
    pal.setFormat(_formato_texto(tamano, color))
    pal.placement = colocacion
    if cuadrante is not None:
        pal.quadOffset = cuadrante
    if dx or dy:
        pal.xOffset = dx
        pal.yOffset = dy
        pal.offsetUnits = compat.MM
    capa.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    capa.setLabelsEnabled(True)


# ==========================================================================
# Simbologías
# ==========================================================================

def _oscurecer(hex_color, f=0.72):
    h = hex_color.lstrip('#')
    return '#%02x%02x%02x' % tuple(round(int(h[k:k + 2], 16) * f)
                                   for k in (0, 2, 4))


def estilo_areas(capa, bandas, opacidad=0.5):
    """
    Categorizado por 'rango' (enteros, sin 'VdB'), banda más alta arriba.
    Borde negro sólido. La transparencia va SOLO en el relleno (alfa del
    color), así el borde negro queda opaco y nítido.
    """
    alfa = max(0, min(255, round(float(opacidad) * 255)))
    cats = []
    for b in sorted(bandas, key=lambda b: b['lv_min'], reverse=True):
        c = QColor(b['color'])
        sym = QgsFillSymbol.createSimple({
            'color': f"{c.red()},{c.green()},{c.blue()},{alfa}",
            'outline_style': 'solid', 'outline_color': '0,0,0,255',
            'outline_width': '0.26', 'outline_width_unit': 'MM'})
        cats.append(QgsRendererCategory(b['rango'], sym, b['rango']))
    capa.setRenderer(QgsCategorizedSymbolRenderer('rango', cats))
    capa.setOpacity(1.0)


def _simbolo_isolinea(color):
    """
    Línea negra fina: coincide con el borde negro de las áreas (va encima),
    así el contorno de cada color se ve negro y prolijo.
    Para volver a la línea de color: QColor(_oscurecer(color)).
    """
    linea = QgsSimpleLineSymbolLayer(QColor(0, 0, 0), 0.3)
    linea.setWidthUnit(compat.MM)
    sym = QgsLineSymbol()
    sym.deleteSymbolLayer(0)
    sym.appendSymbolLayer(linea)
    return sym


def estilo_isolineas(capa, isolineas):
    cats = []
    for i in sorted(isolineas, key=lambda i: i['lv_vdb'], reverse=True):
        etq = f"{i['lv_vdb']} VdB"
        cats.append(QgsRendererCategory(i['lv_vdb'], _simbolo_isolinea(i['color']), etq))
    capa.setRenderer(QgsCategorizedSymbolRenderer('lv_vdb', cats))
    _etiquetar(capa, "format_number(\"lv_vdb\", 0) || ' VdB'", compat.ETQ_CURVA,
               tamano=8)


def estilo_fuentes(capa, es_area):
    if es_area:
        sym = QgsFillSymbol.createSimple({
            'color': '228,87,46,45', 'outline_color': COLOR_FUENTE,
            'outline_width': '0.6', 'outline_width_unit': 'MM'})
    else:
        sym = QgsMarkerSymbol.createSimple({
            'name': 'diamond', 'color': COLOR_FUENTE, 'outline_color': '255,255,255',
            'outline_width': '0.5', 'size': '4.2'})
    capa.setRenderer(QgsSingleSymbolRenderer(sym))
    expr = ("\"nombre\" || '\\n' || format_number(\"lv_25ft\", 0) || ' VdB a 25 ft'")
    if es_area:
        _etiquetar(capa, expr, compat.ETQ_SOBRE_PUNTO, tamano=8)
    else:
        _etiquetar(capa, expr, compat.ETQ_SOBRE_PUNTO, tamano=8,
                   cuadrante=compat.CUADRANTE_DERECHA, dx=2.0)


def estilo_receptores(capa):
    cats = []
    for clave in ('bad', 'ok', 'na'):
        sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': COLOR_ESTADO[clave],
            'outline_color': '255,255,255', 'outline_width': '0.5', 'size': '3.2'})
        cats.append(QgsRendererCategory(clave, sym, TEXTO_ESTADO[clave]))
    capa.setRenderer(QgsCategorizedSymbolRenderer('estado', cats))
    # Etiqueta arriba a la izquierda: nombre y Lv ENTERO.
    expr = ("\"nombre\" || coalesce('\\n' || format_number(\"lv_vdb\", 0) || ' VdB', '')")
    _etiquetar(capa, expr, compat.ETQ_SOBRE_PUNTO, tamano=9,
               cuadrante=compat.CUADRANTE_ARRIBA_IZQ, dx=-1.0, dy=-1.0)


def guardar_qml(capa, ruta_qml):
    """Escribe el estilo de la capa como .qml. Devuelve (ok, mensaje)."""
    res = capa.saveNamedStyle(ruta_qml)
    if isinstance(res, (tuple, list)):
        msg, ok = res[0], bool(res[-1])
        return ok, str(msg)
    return True, str(res)
