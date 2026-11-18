"""
================================================================================
  calculos_core.py  —  NÚCLEO DE CÁLCULO (destinado a compilarse con Cython)
================================================================================
  Autor base: Esteban / Decibel

  Lógica matemática y espacial del plugin. Sin dependencias de QGIS/Qt:
  numpy + shapely (+ scipy y contourpy si están; hay fallbacks).

  Metodología: FTA Report No. 0123 (2018), sección 7.2.
      Lv(D)  = Lv,ref − 30·log10(D / 25 ft)            [VdB re 1 µin/s]  Ec. 7-3
      PPV(D) = PPV,ref · (25 ft / D) ** n              [in/s]            Ec. 7-2
      25 ft = 7.62 m. n = 1.5 en el manual (configurable).

  Modos de proyección (un único selector):
      'iterativo': solo el foco elegido.
      'suma'     : suma logarítmica (energética) del nivel de cada foco.
                   Lv = 10·log10 Σ 10^(Lv_i/10)      PPV = √Σ PPV_i²
      'lineal'   : SIN sumar niveles. La región de cada nivel es la unión
                   (disolución) de las regiones de cada foco por separado:
                   las isolíneas que se tocan se funden en una sola.
                   Receptor: nivel y PPV de la fuente MÁS CERCANA.

  Distancia mínima:
      - Foco puntual: 0.3 m (como la app web) o 7.62 m si acotar_dref=True.
      - Foco de área: distancia al polígono, d = 0 en su interior, acotada a
        7.62 m (el equipo se mueve dentro del área; Lv nunca supera Lv,ref).

  Isolíneas en los NIVELES que define el usuario (enteros):
      - Un único foco activo: geometría EXACTA (círculo de radio
        R = 7.62·10^((Lv,ref − T)/30) o polígono ampliado en R).
      - Varios focos: grilla + contourpy, con extent automático que contiene
        siempre la isolínea más baja (no hay cortes en el borde).
      - Bandas = región(T_i) − región(T_i+1); la última es "≥ T_max".

  Contrato: una única función pública, calcular_todo(...). Salida solo con
  tipos nativos + WKT.
================================================================================
"""

import math

import numpy as np
from shapely.geometry import (Point, Polygon, MultiPolygon, LineString,
                              MultiLineString)
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

try:
    from contourpy import contour_generator
    _TIENE_CONTOURPY = True
except Exception:  # pragma: no cover
    _TIENE_CONTOURPY = False

try:
    from scipy.spatial import cKDTree
    _TIENE_KDTREE = True
except Exception:  # pragma: no cover
    _TIENE_KDTREE = False


# ==========================================================================
# 0. CONSTANTES
# ==========================================================================

M2FT = 3.2808399             # mismo factor que la planilla / app web
D_REF_FT = 25.0
D_REF_METROS = D_REF_FT / M2FT   # 7.62 m
D_MIN_PUNTO = 0.3            # m, evita la singularidad sobre la fuente
MM_POR_PULGADA = 25.4
MAX_CELDAS_DEF = 4_000_000

MODO_ITERATIVO = "iterativo"
MODO_SUMA = "suma"
MODO_LINEAL = "lineal"

# Paleta por defecto (amarillo -> morado). La UI puede pasar otra.
PALETA = [(255, 240, 168), (253, 198, 90), (247, 145, 53), (232, 85, 47),
          (195, 42, 75), (138, 26, 98), (78, 16, 102)]


def _a_rgb(c):
    if isinstance(c, str):
        h = c.lstrip('#')
        return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4))
    return tuple(int(v) for v in c[:3])


def color_banda(i, n, paleta=None):
    """Color interpolado sobre las paradas de `paleta` para la banda i de n."""
    pal = [_a_rgb(c) for c in (paleta or PALETA)]
    if len(pal) == 1:
        return '#%02x%02x%02x' % pal[0]
    t = 0.5 if n <= 1 else i / (n - 1)
    p = t * (len(pal) - 1)
    a = int(math.floor(p))
    b = min(len(pal) - 1, a + 1)
    f = p - a
    rgb = [round(pal[a][k] + (pal[b][k] - pal[a][k]) * f) for k in range(3)]
    return '#%02x%02x%02x' % tuple(rgb)


def _txt_nivel(v):
    return f"{int(round(float(v)))}"


def rango_txt(t_min, t_max):
    """Sin unidad: la leyenda ya dice 'Nivel de Vibración VdB' en el título."""
    if t_max is None:
        return f"≥ {_txt_nivel(t_min)}"
    return f"{_txt_nivel(t_min)} – {_txt_nivel(t_max)}"


# ==========================================================================
# 1. FÓRMULAS FTA
# ==========================================================================

def _lv(lv_ref, d, d_min):
    """Ec. 7-3 con distancia acotada inferiormente a d_min (m)."""
    return lv_ref - 30.0 * np.log10(np.maximum(d, d_min) / D_REF_METROS)


def _ppv(ppv_ref_in_s, d, d_min, n_exp):
    """Ec. 7-2. PPV,ref y resultado en in/s."""
    return ppv_ref_in_s * (D_REF_METROS / np.maximum(d, d_min)) ** n_exp


def radio_nivel(lv_ref, t):
    """Distancia (m) a la que un foco de Lv,ref alcanza el nivel T."""
    return D_REF_METROS * 10 ** ((lv_ref - t) / 30.0)


def combinar_lv(valores, suma):
    v = [x for x in valores if x is not None and np.isfinite(x)]
    if not v:
        return None
    k = 10.0 if suma == "en" else 20.0
    return float(k * np.log10(sum(10 ** (x / k) for x in v)))


def combinar_ppv(valores, suma):
    v = [x for x in valores if x is not None and np.isfinite(x)]
    if not v:
        return None
    if suma == "en":
        return float(math.sqrt(sum(x * x for x in v)))
    return float(sum(v))


# ==========================================================================
# 2. NORMALIZACIÓN DE ENTRADAS
# ==========================================================================

def _asegurar_utm(puntos, epsg):
    """x/y UTM por elemento; reproyecta lon/lat con pyproj solo si falta."""
    def listo(p):
        return ('x' in p and 'y' in p) or p.get('geom') is not None

    if all(listo(p) for p in puntos):
        return [dict(p) for p in puntos]
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    out = []
    for p in puntos:
        if listo(p):
            out.append(dict(p))
        else:
            x, y = tr.transform(p['lon'], p['lat'])
            out.append({**p, 'x': x, 'y': y})
    return out


def _geometria(f):
    g = f.get('geom')
    if g is None:
        return Point(float(f['x']), float(f['y']))
    if isinstance(g, str):
        from shapely import wkt as _wkt
        g = _wkt.loads(g)
    if not isinstance(g, BaseGeometry):
        raise TypeError(f"Foco '{f.get('nombre')}': 'geom' debe ser Shapely o WKT.")
    if g.is_empty:
        raise ValueError(f"Foco '{f.get('nombre')}': geometría vacía.")
    if not g.is_valid:
        g = g.buffer(0)
    return g


def _normalizar_modo(modo):
    """'iterativo' | 'suma' | 'lineal' (acepta los textos del selector)."""
    m = (modo or "").strip().lower()
    if m.startswith("lin"):
        return MODO_LINEAL
    if m.startswith(("sum", "acumul", "área", "area")):
        return MODO_SUMA
    return MODO_ITERATIVO


def _a_in_s(v, unidad):
    if v is None:
        return None
    u = (unidad or "in/s").strip().lower().replace(" ", "")
    if u in ("in/s", "ips", "pulg/s"):
        return float(v)
    if u in ("mm/s", "mms"):
        return float(v) / MM_POR_PULGADA       # in/s = mm/s / 25.4
    raise ValueError(f"Unidad PPV no soportada: '{unidad}'.")


def _preparar_fuentes(pts_f, unidad_ppv, acotar_dref):
    fuentes = []
    for i, f in enumerate(pts_f):
        g = _geometria(f)
        es_area = g.geom_type in ("Polygon", "MultiPolygon")
        c = g.centroid
        if f.get('lv') is None or not np.isfinite(float(f['lv'])):
            raise ValueError(f"Foco '{f.get('nombre')}': falta Lv a 25 ft.")
        fuentes.append({
            **f,
            'idx': i,
            '_geom': g,
            'es_area': es_area,
            'lv': float(f['lv']),
            'ppv': _a_in_s(f.get('ppv'), unidad_ppv),
            'x': float(f.get('x', c.x)),
            'y': float(f.get('y', c.y)),
            'd_min': D_REF_METROS if (es_area or acotar_dref) else D_MIN_PUNTO,
            'activa': bool(f.get('activa', True)),
        })
    return fuentes


# ==========================================================================
# 3. DISTANCIAS SOBRE GRILLA (eficiente, sin iterar celdas)
# ==========================================================================

def _lineas_de(geom):
    if geom.is_empty:
        return []
    if geom.geom_type in ("LineString", "LinearRing"):
        return [geom]
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(_lineas_de(g))
        return out
    if geom.geom_type == "Polygon":
        return [geom.exterior] + list(geom.interiors)
    return []


def _muestrear_lineas(geom_lineal, paso):
    trozos = []
    for ln in _lineas_de(geom_lineal):
        c = np.asarray(ln.coords, dtype=float)[:, :2]
        if len(c) == 0:
            continue
        a, b = c[:-1], c[1:]
        largos = np.hypot(*(b - a).T) if len(a) else []
        for p0, p1, L in zip(a, b, largos):
            n = max(1, int(np.ceil(L / paso)))
            t = np.arange(n, dtype=float)[:, None] / n
            trozos.append(p0 + t * (p1 - p0))
        trozos.append(c[-1:])
    return np.vstack(trozos) if trozos else np.empty((0, 2))


def _mascara_interior(geom, xx, yy):
    try:
        import shapely
        if hasattr(shapely, "contains_xy"):
            return shapely.contains_xy(geom, xx, yy)
    except Exception:
        pass
    try:
        from shapely import vectorized
        return vectorized.contains(geom, xx, yy)
    except Exception:
        pass
    from matplotlib.path import Path
    from shapely.geometry.polygon import orient
    polys = geom.geoms if hasattr(geom, "geoms") else [geom]
    mascara = np.zeros(xx.shape, dtype=bool)
    q = np.column_stack([xx.ravel(), yy.ravel()])
    for p in polys:
        p = orient(p, sign=1.0)
        verts, codes = [], []
        for anillo in [p.exterior] + list(p.interiors):
            c = np.asarray(anillo.coords)[:, :2]
            verts.append(c)
            codes += [Path.MOVETO] + [Path.LINETO] * (len(c) - 2) + [Path.CLOSEPOLY]
        mascara |= Path(np.vstack(verts), codes).contains_points(q).reshape(xx.shape)
    return mascara


def _min_dist_bruta(q, borde, max_elem=20_000_000):
    out = np.empty(len(q))
    bloque = max(1, int(max_elem // max(1, len(borde))))
    for i in range(0, len(q), bloque):
        sub = q[i:i + bloque]
        d2 = ((sub[:, None, :] - borde[None, :, :]) ** 2).sum(axis=2)
        out[i:i + bloque] = np.sqrt(d2.min(axis=1))
    return out


def _distancia_grilla(geom, xx, yy, resolucion):
    """
    Punto: hypot vectorizado. Polígono: borde densificado cada resolución/2
    + KD-Tree (O(N log M), error <= resolución/4) y 0 en el interior.
    """
    if geom.geom_type == "Point":
        return np.hypot(xx - geom.x, yy - geom.y)
    es_area = geom.geom_type in ("Polygon", "MultiPolygon")
    lineal = geom.boundary if es_area else geom
    borde = _muestrear_lineas(lineal, max(resolucion / 2.0, 0.05))
    if len(borde) == 0:
        c = geom.centroid
        return np.hypot(xx - c.x, yy - c.y)
    q = np.column_stack([xx.ravel(), yy.ravel()])
    if _TIENE_KDTREE:
        d, _ = cKDTree(borde).query(q, k=1)
    else:  # pragma: no cover
        d = _min_dist_bruta(q, borde)
    d = d.reshape(xx.shape)
    if es_area:
        d[_mascara_interior(geom, xx, yy)] = 0.0
    return d


# ==========================================================================
# 4. REGIONES "nivel >= T"
# ==========================================================================

def _a_multipoligono(g):
    if g is None or g.is_empty:
        return None
    if not g.is_valid:
        g = g.buffer(0)
    if g.geom_type == "Polygon":
        return MultiPolygon([g])
    if g.geom_type == "MultiPolygon":
        return g
    polys = [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"
             and not p.is_empty]
    return MultiPolygon(polys) if polys else None


def _a_multilinea(g):
    if g is None or g.is_empty:
        return None
    lineas = [LineString(ln.coords) for ln in _lineas_de(g)
              if len(ln.coords) >= 2]
    return MultiLineString(lineas) if lineas else None


def _region_exacta(f, t):
    """Un único foco: región exacta donde Lv >= T."""
    if t > float(_lv(f['lv'], 0.0, f['d_min'])):
        return None                                   # T inalcanzable
    r = max(radio_nivel(f['lv'], t), f['d_min'] if f['es_area'] else 0.0)
    if r <= 0.0:
        return None
    return f['_geom'].buffer(r, 64)


def _filled_a_poligonos(filled):
    poligonos = []
    try:
        lista_vertices, lista_offsets = filled
    except (ValueError, TypeError):
        return poligonos
    for verts, offsets in zip(lista_vertices, lista_offsets):
        if verts is None or offsets is None or len(offsets) < 2:
            continue
        anillos = [verts[offsets[k]:offsets[k + 1]]
                   for k in range(len(offsets) - 1)
                   if offsets[k + 1] - offsets[k] >= 4]
        if not anillos:
            continue
        try:
            geom = Polygon(anillos[0], holes=anillos[1:])
            if not geom.is_valid:
                geom = geom.buffer(0)
            if not geom.is_empty:
                poligonos.append(geom)
        except Exception:
            continue
    return poligonos


class _Grilla:
    """Campo de niveles de varios focos sobre una malla regular."""

    def __init__(self, fuentes, niveles, suma, resolucion, max_celdas):
        self.suma = suma
        k = 10.0 if suma == "en" else 20.0
        # Cota del alcance: todos los focos superpuestos en un punto.
        lv_tot = combinar_lv([f['lv'] for f in fuentes], suma)
        rb = max(radio_nivel(lv_tot, min(niveles)), 1.0) * 1.05
        geoms = [f['_geom'] for f in fuentes]
        x0, y0, x1, y1 = unary_union(geoms).bounds
        x0, y0, x1, y1 = x0 - rb, y0 - rb, x1 + rb, y1 + rb
        w, h = x1 - x0, y1 - y0
        res = float(resolucion)
        if (w / res) * (h / res) > max_celdas:
            res = math.sqrt(w * h / max_celdas)     # se engruesa la malla
        self.resolucion = res
        ex = np.arange(x0, x1 + res, res)
        ey = np.arange(y0, y1 + res, res)
        self.xx, self.yy = np.meshgrid(ex, ey)
        acum = np.zeros_like(self.xx, dtype=float)
        for f in fuentes:
            d = _distancia_grilla(f['_geom'], self.xx, self.yy, res)
            acum += 10 ** (_lv(f['lv'], d, f['d_min']) / k)
        self.grid = k * np.log10(np.maximum(acum, 1e-30))
        self._cg = None
        if _TIENE_CONTOURPY:
            self._cg = contour_generator(self.xx, self.yy, self.grid,
                                         fill_type="OuterOffset")

    def region(self, t):
        if float(np.nanmax(self.grid)) < t:
            return None
        if self._cg is not None:
            polys = _filled_a_poligonos(self._cg.filled(t, 1e12))
        else:  # pragma: no cover
            polys = self._region_mpl(t)
        if not polys:
            return None
        return unary_union(polys)

    def _region_mpl(self, t):  # pragma: no cover
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        cs = ax.contourf(self.xx, self.yy, self.grid, levels=[t, 1e12])
        plt.close(fig)
        out = []
        for path in cs.get_paths():
            for coords in path.to_polygons():
                if len(coords) > 2:
                    out.append(Polygon(coords).buffer(0))
        return out


# ==========================================================================
# 5. RECEPTORES
# ==========================================================================

def _estado(valor, limite):
    if limite is None or valor is None:
        return 'na'
    return 'ok' if valor <= limite else 'bad'


_TXT_ESTADO = {'ok': 'Cumple', 'bad': 'No cumple', 'na': 'Sin evaluar'}


def _f1(v, d=1):
    return "—" if v is None else f"{v:.{d}f}".replace(".", ",")


def _evaluar_receptores(fuentes, pts_r, modo, n_exp):
    """
    modo 'suma'  : Lv y PPV globales por suma energética de todos los focos.
    modo 'lineal': sin suma; el receptor toma Lv y PPV del foco más cercano.
    """
    receptores, memoria = [], []
    for r in pts_r:
        p = Point(float(r['x']), float(r['y']))
        lvs, ppvs, dists, txt = [], [], [], []
        for f in fuentes:
            d = float(f['_geom'].distance(p))
            lv = float(_lv(f['lv'], d, f['d_min']))
            ppv = (float(_ppv(f['ppv'], d, f['d_min'], n_exp))
                   if f['ppv'] is not None else None)
            lvs.append(lv)
            ppvs.append(ppv)
            dists.append(d)
            ft = max(d, f['d_min']) * M2FT
            memoria.append({
                'Receptor': r['nombre'], 'Foco': f['nombre'],
                'Distancia (m)': round(d, 2), 'Distancia (ft)': round(d * M2FT, 1),
                'Lv (VdB)': round(lv, 1),
                'PPV (in/s)': None if ppv is None else round(ppv, 5),
                'PPV (mm/s)': None if ppv is None else round(ppv * MM_POR_PULGADA, 4),
            })
            txt.append(f"{f['nombre']} · D = {_f1(d)} m = {_f1(d * M2FT)} ft")
            txt.append(f"  Lv  = {_f1(f['lv'])} − 30·log10({_f1(ft)}/25) = {_f1(lv)} VdB")
            if ppv is not None:
                txt.append(f"  PPV = {f['ppv']:.4g} × (25/{_f1(ft)})^{n_exp:g} = "
                           f"{ppv:.4g} in/s = {ppv * MM_POR_PULGADA:.4g} mm/s")

        i_min = int(np.argmin(dists)) if dists else None
        if modo == MODO_LINEAL and i_min is not None:
            lv_g, ppv_g = lvs[i_min], ppvs[i_min]
        else:
            lv_g = combinar_lv(lvs, "en")
            ppv_g = combinar_ppv(ppvs, "en")
        lim_lv = r.get('lim_lv')
        lim_ppv = r.get('lim_ppv')
        e_lv = _estado(lv_g, lim_lv)
        e_ppv = _estado(ppv_g, lim_ppv)
        estado = ('bad' if 'bad' in (e_lv, e_ppv)
                  else 'ok' if 'ok' in (e_lv, e_ppv) else 'na')
        if len(fuentes) > 1:
            etq = (f"lineal, fuente más cercana: {fuentes[i_min]['nombre']}"
                   if modo == MODO_LINEAL else "suma logarítmica")
            txt.append(f"Global ({etq}): Lv = {_f1(lv_g)} VdB"
                       + (f" · PPV = {ppv_g:.4g} in/s" if ppv_g is not None else ""))
        memoria.append({
            'Receptor': r['nombre'], 'Foco': 'TOTAL',
            'Distancia (m)': None if i_min is None else round(dists[i_min], 2),
            'Distancia (ft)': None if i_min is None else round(dists[i_min] * M2FT, 1),
            'Lv (VdB)': None if lv_g is None else round(lv_g, 1),
            'PPV (in/s)': None if ppv_g is None else round(ppv_g, 5),
            'PPV (mm/s)': None if ppv_g is None else round(ppv_g * MM_POR_PULGADA, 4),
        })
        receptores.append({
            'fid': r.get('fid'),
            'nombre': r['nombre'], 'x': float(r['x']), 'y': float(r['y']),
            'dist_m': None if i_min is None else round(dists[i_min], 2),
            'fte_cerc': None if i_min is None else fuentes[i_min]['nombre'],
            'lv_vdb': None if lv_g is None else round(lv_g, 1),
            'lim_vdb': lim_lv, 'eval_lv': _TXT_ESTADO[e_lv] if lim_lv is not None else '',
            'ppv_ins': None if ppv_g is None else round(ppv_g, 6),
            'ppv_mms': None if ppv_g is None else round(ppv_g * MM_POR_PULGADA, 5),
            'lim_ppv': lim_ppv, 'eval_ppv': _TXT_ESTADO[e_ppv] if lim_ppv is not None else '',
            'estado': estado,
            'memoria': "\n".join(txt),
        })
    return receptores, memoria


# ==========================================================================
# 6. FUNCIÓN PÚBLICA
# ==========================================================================

def calcular_todo(pts_f, pts_r, niveles, n_exp=1.5, resolucion=2.0,
                  epsg=32719, modo="Suma", foco_idx=0, paleta=None,
                  acotar_dref=False, unidad_ppv="in/s",
                  max_celdas=MAX_CELDAS_DEF, **_obsoletos):
    """
    pts_f : [{'nombre','x','y' | 'geom', 'lv', 'ppv', 'activa', 'equipo'}]
            lv = Lv a 25 ft (VdB); ppv = PPV a 25 ft en `unidad_ppv`.
    pts_r : [{'nombre','x','y', 'lim_lv' (VdB|None), 'lim_ppv' (in/s|None)}]
    niveles : lista de niveles de isolínea (VdB, se redondean a entero).
    modo : 'Iterativo' (solo pts_f[foco_idx]), 'Suma' (logarítmica) o
           'Lineal' (sin suma: isolíneas de cada foco disueltas). Puntos y
           áreas admiten los tres.
    paleta : paradas de color ['#rrggbb' | (r,g,b), ...] interpoladas a la
             cantidad de niveles. None = amarillo -> morado.

    Retorna dict nativo:
      bandas     [{wkt, lv_min, lv_max|None, rango, color, area_ha}]
      isolineas  [{wkt, lv_vdb, radio_m|None, area_ha, color}]
      alcance    [{lv_vdb, radio_m|None, area_ha}]
      receptores [{nombre,x,y,dist_m,fte_cerc,lv_vdb,lim_vdb,eval_lv,
                   ppv_ins,ppv_mms,lim_ppv,eval_ppv,estado,memoria}]
      memoria    filas por foco + TOTAL (CSV)
      fuentes    [{nombre,equipo,lv_25ft,ppv_25ft,este,norte,tipo}]
      niveles, suma, resolucion_usada, unidad_ppv
    """
    niv = sorted({int(round(float(v))) for v in niveles
                  if v is not None and np.isfinite(float(v))})
    if not niv:
        raise ValueError("Defina al menos un nivel de isolínea.")

    todas = _preparar_fuentes(_asegurar_utm(pts_f, epsg), unidad_ppv,
                              acotar_dref)
    pts_r = _asegurar_utm(pts_r, epsg)
    if not todas:
        raise ValueError("No hay focos para calcular.")

    m = _normalizar_modo(modo)
    if m == MODO_ITERATIVO:
        i = max(0, min(int(foco_idx), len(todas) - 1))
        activas = [todas[i]]
    else:
        activas = [f for f in todas if f['activa']]
    if not activas:
        raise ValueError("No hay focos activos (marque al menos uno).")

    # --- Regiones nivel >= T ---
    res_usada = None
    if len(activas) == 1:
        regiones = {t: _region_exacta(activas[0], t) for t in niv}
    elif m == MODO_LINEAL:
        # Sin suma: cada foco aporta su región exacta y se DISUELVEN.
        regiones = {}
        for t in niv:
            partes = [g for g in (_region_exacta(f, t) for f in activas)
                      if g is not None and not g.is_empty]
            regiones[t] = unary_union(partes) if partes else None
    else:
        grilla = _Grilla(activas, niv, "en", resolucion, max_celdas)
        regiones = {t: grilla.region(t) for t in niv}
        res_usada = round(grilla.resolucion, 3)

    unico_punto = len(activas) == 1 and not activas[0]['es_area']
    n = len(niv)
    bandas, isolineas, alcance = [], [], []
    for i, t in enumerate(niv):
        reg = regiones[t]
        sig = regiones[niv[i + 1]] if i + 1 < n else None
        color = color_banda(i, n, paleta)
        radio = (round(radio_nivel(activas[0]['lv'], t), 2)
                 if unico_punto and reg is not None else None)
        area = 0.0 if reg is None else reg.area
        alcance.append({'lv_vdb': t, 'radio_m': radio,
                        'area_ha': round(area / 1e4, 4)})
        if reg is None:
            continue
        banda = reg.difference(sig) if sig is not None else reg
        banda = _a_multipoligono(banda)
        if banda is not None:
            bandas.append({
                'wkt': banda.wkt, 'lv_min': t,
                'lv_max': niv[i + 1] if i + 1 < n else None,
                'rango': rango_txt(t, niv[i + 1] if i + 1 < n else None),
                'color': color, 'orden': i,
                'area_ha': round(banda.area / 1e4, 4),
            })
        linea = _a_multilinea(reg.boundary)
        if linea is not None:
            isolineas.append({'wkt': linea.wkt, 'lv_vdb': t, 'radio_m': radio,
                              'area_ha': round(area / 1e4, 4), 'color': color,
                              'orden': i})

    receptores, memoria = _evaluar_receptores(activas, pts_r, m, n_exp)

    fuentes = [{
        'fid': f.get('fid'), 'nombre': f['nombre'],
        'equipo': f.get('equipo', ''), 'lv_25ft': round(f['lv'], 1),
        'ppv_25ft': None if f['ppv'] is None else round(f['ppv'], 4),
        'este': round(f['x'], 2), 'norte': round(f['y'], 2),
        'tipo': 'Área' if f['es_area'] else 'Punto',
        'wkt': f['_geom'].wkt,
        'activa': f in activas,
    } for f in todas]

    return {
        'bandas': bandas, 'isolineas': isolineas, 'alcance': alcance,
        'receptores': receptores, 'memoria': memoria, 'fuentes': fuentes,
        'niveles': niv, 'modo': m,
        'resolucion_usada': res_usada, 'unidad_ppv': 'in/s',
        'n_activas': len(activas),
    }
