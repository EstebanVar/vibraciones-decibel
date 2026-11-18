# exportar.py
"""
Exportación de resultados con su estética incluida.

  <base>.gpkg            4 capas; cada una con su estilo guardado como
                         PREDETERMINADO dentro del GeoPackage (tabla
                         layer_styles): al arrastrarlo a QGIS se ve igual.
  <base>_estilos/*.qml   Estilos sueltos (Propiedades > Simbología > Estilo >
                         Cargar estilo…), por si se usan en otra capa.
  <base>_shp/*.shp       Shapefiles + .qml con el MISMO nombre: QGIS lo
                         carga automáticamente al abrir el .shp.
  <base>.kmz             Google Earth, con leyenda (valores enteros).
  <base>_memoria.csv     Memoria de cálculo por receptor y foco.
  <base>_receptores.csv  Resumen y evaluación por receptor.
  <base>_LEEME.txt       Método, sistema de referencia y capas.
"""

import csv
import datetime
import os
import shutil
import zipfile
from xml.sax.saxutils import escape as xesc

from qgis.core import (QgsVectorLayer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsProject, QgsGeometry)

from . import compat
from .estilos import COLOR_ESTADO, NOMBRES_CAPA, guardar_qml
from .leyenda_kmz import (TITULO_LEYENDA, entradas_leyenda,
                          generar_png_leyenda, subtitulo_leyenda)

ORDEN = ['areas_dispersion', 'isolineas', 'fuentes', 'receptores']


def exportar_todo(capas, res, ruta_base, opciones, epsg, meta):
    """
    capas: dict clave -> QgsVectorLayer (memoria, ya estilizadas).
    ruta_base: ruta sin extensión (carpeta/nombre).
    opciones: set con 'gpkg', 'shp', 'kmz', 'csv'.
    meta: {'proyecto','escenario','ctx_leyenda','modo','n_exp','acotar'}.
          'proyecto' = nombre del proyecto QGIS abierto.
    Devuelve lista de (ok, texto).
    """
    out = []
    carpeta = os.path.dirname(ruta_base)
    base = os.path.basename(ruta_base)
    dir_qml = os.path.join(carpeta, f"{base}_estilos")
    os.makedirs(dir_qml, exist_ok=True)

    # QML de cada capa (fuente única de verdad para GPKG y SHP).
    qmls = {}
    for k in ORDEN:
        ruta = os.path.join(dir_qml, f"{k}.qml")
        ok, msg = guardar_qml(capas[k], ruta)
        if ok:
            qmls[k] = ruta
    out.append((bool(qmls), f"Estilos QML: {dir_qml}"))

    if 'gpkg' in opciones:
        out.append(_gpkg(capas, qmls, ruta_base + ".gpkg"))
    if 'shp' in opciones:
        out.append(_shp(capas, qmls, os.path.join(carpeta, f"{base}_shp")))
    if 'kmz' in opciones:
        out.append(_kmz(res, ruta_base + ".kmz", epsg, meta))
    if 'csv' in opciones:
        out.append(_csv(res, ruta_base))
    out.append(_leeme(ruta_base + "_LEEME.txt", epsg, meta, opciones))
    return out


# --------------------------------------------------------------------------
def _gpkg(capas, qmls, ruta):
    errores = []
    for i, k in enumerate(ORDEN):
        ok, msg = compat.escribir_capa(capas[k], ruta, "GPKG", nombre_capa=k,
                                       sobrescribir_archivo=(i == 0))
        if not ok:
            errores.append(f"{k}: {msg}")
            continue
        if k in qmls:
            lyr = QgsVectorLayer(f"{ruta}|layername={k}", NOMBRES_CAPA[k], "ogr")
            if lyr.isValid():
                lyr.loadNamedStyle(qmls[k])
                ok_e, msg_e = compat.guardar_estilo_en_bd(
                    lyr, k, "Vibraciones Decibel (FTA 2018)")
                if not ok_e:
                    errores.append(f"estilo {k}: {msg_e}")
            del lyr
    if errores:
        return False, f"GeoPackage con avisos ({ruta}): " + "; ".join(errores)
    return True, f"GeoPackage con estilos embebidos: {ruta}"


def _shp(capas, qmls, carpeta):
    os.makedirs(carpeta, exist_ok=True)
    errores = []
    for k in ORDEN:
        ruta = os.path.join(carpeta, f"{k}.shp")
        ok, msg = compat.escribir_capa(capas[k], ruta, "ESRI Shapefile")
        if not ok:
            errores.append(f"{k}: {msg}")
            continue
        if k in qmls:   # .qml homónimo -> QGIS lo aplica al abrir el .shp
            shutil.copyfile(qmls[k], os.path.join(carpeta, f"{k}.qml"))
    if errores:
        return False, "Shapefiles con avisos: " + "; ".join(errores)
    return True, f"Shapefiles + QML: {carpeta}"


# --------------------------------------------------------------------------
def _csv(res, ruta_base):
    try:
        cols = ['Receptor', 'Foco', 'Distancia (m)', 'Distancia (ft)',
                'Lv (VdB)', 'PPV (in/s)', 'PPV (mm/s)']
        with open(ruta_base + "_memoria.csv", "w", newline="",
                  encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter=";")
            w.writeheader()
            for fila in res['memoria']:
                w.writerow({c: _num(fila.get(c)) for c in cols})
        cols_r = [('nombre', 'Receptor'), ('este', 'Este (m)'),
                  ('norte', 'Norte (m)'), ('dist_m', 'Distancia (m)'),
                  ('fte_cerc', 'Fuente más cercana'), ('lv_vdb', 'Lv (VdB)'),
                  ('lim_vdb', 'Límite Lv (VdB)'), ('eval_lv', 'Molestia'),
                  ('ppv_ins', 'PPV (in/s)'), ('ppv_mms', 'PPV (mm/s)'),
                  ('lim_ppv', 'Límite PPV (in/s)'), ('eval_ppv', 'Daño')]
        with open(ruta_base + "_receptores.csv", "w", newline="",
                  encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow([t for _, t in cols_r])
            for r in res['receptores']:
                fila = dict(r, este=round(r['x'], 2), norte=round(r['y'], 2))
                w.writerow([_num(fila.get(c)) for c, _ in cols_r])
        return True, f"CSV: {ruta_base}_memoria.csv y _receptores.csv"
    except Exception as e:
        return False, f"CSV: {e}"


def _num(v):
    """Decimal con coma para Excel en español."""
    if isinstance(v, float):
        return f"{v}".replace(".", ",")
    return "" if v is None else v


# --------------------------------------------------------------------------
def _kmz(res, ruta, epsg, meta):
    from shapely import wkt as swkt

    tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem(f"EPSG:{epsg}"),
                                QgsCoordinateReferenceSystem("EPSG:4326"),
                                QgsProject.instance())

    def a_wgs(wkt):
        g = QgsGeometry.fromWkt(wkt)
        g.transform(tr)
        return swkt.loads(g.asWkt())

    def coords(seq):
        return " ".join(f"{x:.7f},{y:.7f},0" for x, y in
                        [(c[0], c[1]) for c in seq])

    def poligonos_kml(g):
        polys = g.geoms if hasattr(g, 'geoms') else [g]
        s = []
        for p in polys:
            if p.geom_type != 'Polygon' or p.is_empty:
                continue
            s.append("<Polygon><tessellate>1</tessellate><outerBoundaryIs><LinearRing>"
                     f"<coordinates>{coords(p.exterior.coords)}</coordinates>"
                     "</LinearRing></outerBoundaryIs>")
            for h in p.interiors:
                s.append("<innerBoundaryIs><LinearRing>"
                         f"<coordinates>{coords(h.coords)}</coordinates>"
                         "</LinearRing></innerBoundaryIs>")
            s.append("</Polygon>")
        return "<MultiGeometry>" + "".join(s) + "</MultiGeometry>"

    def lineas_kml(g):
        lns = g.geoms if hasattr(g, 'geoms') else [g]
        return "<MultiGeometry>" + "".join(
            f"<LineString><tessellate>1</tessellate><coordinates>{coords(l.coords)}"
            "</coordinates></LineString>" for l in lns) + "</MultiGeometry>"

    def kcol(hex_color, alpha):
        h = hex_color.lstrip('#')
        return f"{alpha:02x}{h[4:6]}{h[2:4]}{h[0:2]}"

    def f1(v, d=1):
        return "—" if v is None else f"{v:.{d}f}".replace(".", ",")

    try:
        sub = subtitulo_leyenda(meta.get('ctx_leyenda'))
        png = generar_png_leyenda(entradas_leyenda(res['bandas']),
                                  titulo=TITULO_LEYENDA, subtitulo=sub)
    except Exception as e:
        return False, f"KMZ: no se pudo generar la leyenda ({e})"

    try:
        nombre_doc = " · ".join(x for x in (meta.get('proyecto'),
                                            meta.get('escenario')) if x)
        k = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
             f"<name>{xesc(nombre_doc or 'Proyección de vibraciones')}</name>",
             "<description>Proyección de vibraciones, FTA Report No. 0123 (2018). "
             "Lv = Lv,ref − 30·log10(D/25 ft).</description>"]
        for b in res['bandas']:
            # Borde negro, igual que en QGIS.
            k.append(f'<Style id="b{b["orden"]}"><LineStyle><color>ff000000'
                     f'</color><width>1</width></LineStyle><PolyStyle><color>'
                     f'{kcol(b["color"], 140)}</color></PolyStyle></Style>')
        for i in res['isolineas']:
            k.append(f'<Style id="l{i["orden"]}"><LineStyle><color>ff000000'
                     '</color><width>1.5</width></LineStyle></Style>')
        k.append('<Style id="src"><IconStyle><color>ff2e57e4</color><scale>1.1</scale>'
                 '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_square.png'
                 '</href></Icon></IconStyle><PolyStyle><color>552e57e4</color></PolyStyle>'
                 '<LineStyle><color>ff2e57e4</color><width>2</width></LineStyle></Style>')
        for est, col in COLOR_ESTADO.items():
            k.append(f'<Style id="r_{est}"><IconStyle><color>{kcol(col, 255)}</color>'
                     '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png'
                     '</href></Icon></IconStyle></Style>')

        k.append("<Folder><name>Áreas de dispersión</name>")
        for b in sorted(res['bandas'], key=lambda b: -b['lv_min']):
            k.append(f"<Placemark><name>{xesc(b['rango'])}</name><styleUrl>#b{b['orden']}"
                     f"</styleUrl>{poligonos_kml(a_wgs(b['wkt']))}</Placemark>")
        k.append("</Folder><Folder><name>Isolíneas</name>")
        for i in res['isolineas']:
            desc = (f"<description>Radio: {f1(i['radio_m'])} m</description>"
                    if i['radio_m'] else "")
            k.append(f"<Placemark><name>{i['lv_vdb']} VdB</name><styleUrl>#l{i['orden']}"
                     f"</styleUrl>{desc}{lineas_kml(a_wgs(i['wkt']))}</Placemark>")
        k.append("</Folder><Folder><name>Fuentes</name>")
        for f in res['fuentes']:
            g = a_wgs(f['wkt'])
            geom = (f"<Point><coordinates>{g.x:.7f},{g.y:.7f},0</coordinates></Point>"
                    if g.geom_type == 'Point' else poligonos_kml(g))
            k.append(f"<Placemark><name>{xesc(f['nombre'])}</name><styleUrl>#src</styleUrl>"
                     f"<description>{xesc(f['equipo'] or '')}. Lv a 25 ft: {f1(f['lv_25ft'])} VdB. "
                     f"PPV a 25 ft: {f1(f['ppv_25ft'], 3)} in/s.</description>{geom}</Placemark>")
        k.append("</Folder><Folder><name>Receptores</name>")
        for r in res['receptores']:
            g = a_wgs(f"POINT({r['x']} {r['y']})")
            lim = (f" (límite {r['lim_vdb']:g} VdB: {r['eval_lv']})"
                   if r['lim_vdb'] is not None else "")
            limp = (f" (límite {r['lim_ppv']:g} in/s: {r['eval_ppv']})"
                    if r['lim_ppv'] is not None else "")
            k.append(f"<Placemark><name>{xesc(r['nombre'])}</name><styleUrl>#r_{r['estado']}"
                     f"</styleUrl><description>Distancia: {f1(r['dist_m'])} m. "
                     f"Lv: {f1(r['lv_vdb'])} VdB{xesc(lim)}. PPV: {f1(r['ppv_ins'], 4)} in/s"
                     f"{xesc(limp)}.</description><Point><coordinates>{g.x:.7f},{g.y:.7f},0"
                     "</coordinates></Point></Placemark>")
        k.append("</Folder>")
        # Leyenda abajo a la izquierda, tamaño nativo (sin deformar).
        k.append('<ScreenOverlay><name>Leyenda</name><Icon><href>leyenda.png</href></Icon>'
                 '<overlayXY x="0" y="0" xunits="fraction" yunits="fraction"/>'
                 '<screenXY x="10" y="40" xunits="pixels" yunits="pixels"/>'
                 '<rotationXY x="0" y="0" xunits="fraction" yunits="fraction"/>'
                 '<size x="-1" y="-1" xunits="fraction" yunits="fraction"/></ScreenOverlay>')
        k.append("</Document></kml>")
        with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doc.kml", "\n".join(k))
            z.writestr("leyenda.png", png)
        return True, f"KMZ: {ruta}"
    except Exception as e:
        return False, f"KMZ: {e}"


# --------------------------------------------------------------------------
def _leeme(ruta, epsg, meta, opciones):
    try:
        suma = {'iterativo': 'un solo foco (iterativo)',
                'suma': 'suma logarítmica (energética) de todas las fuentes',
                'lineal': 'lineal: sin suma, isolíneas de cada fuente disueltas; '
                          'receptor = fuente más cercana'}.get(meta.get('modo'), '')
        txt = [
            f"Proyección de vibraciones · {meta.get('proyecto', '')} · {meta.get('escenario', '')}",
            f"Generado: {datetime.datetime.now():%Y-%m-%d %H:%M}", "",
            f"Sistema de referencia: EPSG:{epsg}. Codificación: UTF-8.", "",
            "Método: FTA Report No. 0123 (2018), sección 7.2.",
            "  Lv(D)  = Lv,ref − 30·log10(D / 25 ft)        [VdB re 1 µin/s]",
            f"  PPV(D) = PPV,ref · (25 ft / D)^{meta.get('n_exp', 1.5):g}            [in/s]",
            f"  Modo de proyección: {suma}.",
            "  Distancia mínima: "
            + ("7,62 m (25 ft)" if meta.get('acotar') else "0,3 m para focos puntuales")
            + "; focos de área: distancia al polígono, acotada a 7,62 m.", "",
            "Capas (mismos campos en GPKG y SHP; estilos en *_estilos/*.qml):",
            "  areas_dispersion  Polígonos entre niveles consecutivos (lv_min, lv_max en VdB enteros).",
            "  isolineas         Líneas de igual nivel (lv_vdb); radio_m solo con un foco puntual.",
            "  fuentes           Focos (lv_25ft en VdB, ppv_25ft en in/s).",
            "  receptores        Nivel proyectado, límite y evaluación (molestia y daño).", "",
            "Para recuperar la estética: abrir el .gpkg (estilo predeterminado embebido) o el .shp",
            "(el .qml homónimo se aplica solo). En otra capa: Propiedades > Simbología > Estilo > Cargar estilo.",
        ]
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write("\r\n".join(txt))
        return True, f"LEEME: {ruta}"
    except Exception as e:
        return False, f"LEEME: {e}"
