# fta_datos.py
"""
Tablas y conversiones del manual FTA Report No. 0123 (2018).

Datos públicos del manual (no es IP): catálogo de equipos (Tabla 7-4),
criterios de molestia (Tabla 6-3) y de daño estructural (Tabla 7-5),
paleta de colores y conversiones PPV <-> Lv. Sin dependencias de QGIS.
"""

import math

M2FT = 3.2808399            # pies por metro
D_REF_FT = 25.0             # distancia de referencia FTA
CRESTA = 4.0                # factor de cresta FTA (PPV -> rms)
V_REF = 1e-6                # 1 µin/s
MM_POR_PULGADA = 25.4

EQUIPO_PERSONALIZADO = "custom"

# ---- Tabla 7-4: niveles de referencia a 25 ft --------------------------
EQUIPOS = [
    {'id': 'pile_imp_max', 'nombre': 'Hincadora de pilotes (impacto) · rango superior', 'ppv': 1.518, 'lv': 112},
    {'id': 'pile_imp_typ', 'nombre': 'Hincadora de pilotes (impacto) · típico', 'ppv': 0.644, 'lv': 104},
    {'id': 'pile_son_max', 'nombre': 'Hincadora de pilotes (sónica) · rango superior', 'ppv': 0.734, 'lv': 105},
    {'id': 'pile_son_typ', 'nombre': 'Hincadora de pilotes (sónica) · típico', 'ppv': 0.170, 'lv': 93},
    {'id': 'clam', 'nombre': 'Cuchara bivalva, caída (muro pantalla)', 'ppv': 0.202, 'lv': 94},
    {'id': 'hydromill_soil', 'nombre': 'Hidrofresa (muro pantalla) · en suelo', 'ppv': 0.008, 'lv': 66},
    {'id': 'hydromill_rock', 'nombre': 'Hidrofresa (muro pantalla) · en roca', 'ppv': 0.017, 'lv': 75},
    {'id': 'roller', 'nombre': 'Rodillo vibratorio', 'ppv': 0.210, 'lv': 94},
    {'id': 'hoe_ram', 'nombre': 'Martillo hidráulico (hoe ram)', 'ppv': 0.089, 'lv': 87},
    {'id': 'bulldozer_l', 'nombre': 'Bulldozer grande', 'ppv': 0.089, 'lv': 87},
    {'id': 'caisson', 'nombre': 'Perforación de pilas (caisson drilling)', 'ppv': 0.089, 'lv': 87},
    {'id': 'trucks', 'nombre': 'Camiones cargados', 'ppv': 0.076, 'lv': 86},
    {'id': 'jackhammer', 'nombre': 'Martillo neumático (jackhammer)', 'ppv': 0.035, 'lv': 79},
    {'id': 'bulldozer_s', 'nombre': 'Bulldozer pequeño', 'ppv': 0.003, 'lv': 58},
]
EQUIPO_DEF = 'roller'

# ---- Tabla 6-3: molestia (Lv, VdB) --------------------------------------
CRIT_LV = [
    {'id': 'none', 'nombre': 'Sin evaluar', 'lim': None},
    {'id': 'c1', 'nombre': 'Cat. 1 · Alta sensibilidad', 'lim': 65},
    {'id': 'c2f', 'nombre': 'Cat. 2 · Residencial, eventos frecuentes', 'lim': 72},
    {'id': 'c2o', 'nombre': 'Cat. 2 · Residencial, eventos ocasionales', 'lim': 75},
    {'id': 'c2i', 'nombre': 'Cat. 2 · Residencial, eventos infrecuentes', 'lim': 80},
    {'id': 'c3f', 'nombre': 'Cat. 3 · Institucional, eventos frecuentes', 'lim': 75},
    {'id': 'c3o', 'nombre': 'Cat. 3 · Institucional, eventos ocasionales', 'lim': 78},
    {'id': 'c3i', 'nombre': 'Cat. 3 · Institucional, eventos infrecuentes', 'lim': 83},
    {'id': 'custom', 'nombre': 'Límite personalizado', 'lim': None},
]
CRIT_LV_DEF = 'c2f'

# ---- Tabla 7-5: daño estructural (PPV, in/s) ----------------------------
CRIT_PPV = [
    {'id': 'none', 'nombre': 'Sin evaluar', 'lim': None},
    {'id': 'I', 'nombre': 'I · Hormigón armado, acero o madera (sin estuco)', 'lim': 0.5},
    {'id': 'II', 'nombre': 'II · Hormigón y albañilería de ingeniería (sin estuco)', 'lim': 0.3},
    {'id': 'III', 'nombre': 'III · Madera y albañilería sin ingeniería', 'lim': 0.2},
    {'id': 'IV', 'nombre': 'IV · Extremadamente susceptible a daño', 'lim': 0.12},
    {'id': 'custom', 'nombre': 'Límite personalizado', 'lim': None},
]
CRIT_PPV_DEF = 'III'

# ---- Escala de niveles: mínimo, paso y cantidad de colores -------------
ESCALA_DEF = {'minimo': 65, 'paso': 5, 'n_colores': 6}   # 65 70 … 90


def niveles_escala(minimo, paso, n_colores):
    """[mín, mín+paso, …] con n_colores valores (enteros)."""
    n = max(1, int(n_colores))
    return [int(round(minimo + i * paso)) for i in range(n)]


# ---- Escalas de color (paradas que se interpolan a n colores) -----------
PALETA = [(255, 240, 168), (253, 198, 90), (247, 145, 53), (232, 85, 47),
          (195, 42, 75), (138, 26, 98), (78, 16, 102)]
PALETA_VERDE_MORADO = [(0, 120, 0), (102, 174, 0), (204, 228, 0), (255, 255, 0),
                       (237, 170, 0), (218, 85, 0), (200, 0, 0), (100, 0, 60),
                       (50, 0, 90)]
ESCALAS_COLOR = [
    ('amarillo_morado', 'Amarillo → morado', PALETA),
    ('verde_morado', 'Verde → morado', PALETA_VERDE_MORADO),
    ('custom', 'Personalizada (color inicial y final)', None),
]
COLOR_CUSTOM_DEF = ('#2c7bb6', '#d7191c')

COLOR_ESTADO = {'ok': '#1b8a4c', 'bad': '#c9303e', 'na': '#6f7a85'}
TEXTO_ESTADO = {'ok': 'Cumple', 'bad': 'No cumple', 'na': 'Sin evaluar'}


def _buscar(lista, ident):
    return next((o for o in lista if o['id'] == ident), None)


def equipo(ident):
    return _buscar(EQUIPOS, ident)


def crit_lv(ident):
    return _buscar(CRIT_LV, ident)


def crit_ppv(ident):
    return _buscar(CRIT_PPV, ident)


def color_banda_rgb(i, n):
    """Color interpolado de la paleta para la banda i de n."""
    t = 0.5 if n <= 1 else i / (n - 1)
    p = t * (len(PALETA) - 1)
    a = int(math.floor(p))
    b = min(len(PALETA) - 1, a + 1)
    f = p - a
    return tuple(round(PALETA[a][k] + (PALETA[b][k] - PALETA[a][k]) * f)
                 for k in range(3))


def color_banda(i, n):
    return '#%02x%02x%02x' % color_banda_rgb(i, n)


def oscurecer(hex_color, factor=0.72):
    h = hex_color.lstrip('#')
    rgb = [int(h[k:k + 2], 16) for k in (0, 2, 4)]
    return '#%02x%02x%02x' % tuple(round(c * factor) for c in rgb)


def fmt_nivel(v):
    """Niveles de leyenda SIEMPRE enteros: 72.4 -> '72'."""
    return f"{int(round(float(v)))}"


def normalizar_niveles(valores):
    """Enteros, únicos, ordenados y en rango razonable (1–199 VdB)."""
    out = sorted({int(round(float(v))) for v in valores
                  if v is not None and 0 < float(v) < 200})
    return out


def rango_txt(nivel_min, nivel_max):
    if nivel_max is None:
        return f"≥ {fmt_nivel(nivel_min)} VdB"
    return f"{fmt_nivel(nivel_min)} – {fmt_nivel(nivel_max)} VdB"


# ---- Conversiones (FTA 2018) --------------------------------------------
def lv_desde_ppv(ppv_in_s):
    """Lv (VdB) equivalente a un PPV (in/s), factor de cresta 4."""
    return 20.0 * math.log10((ppv_in_s / CRESTA) / V_REF)


def ppv_desde_lv(lv):
    """PPV (in/s) equivalente a un Lv (VdB), factor de cresta 4."""
    return CRESTA * V_REF * 10 ** (lv / 20.0)


def ppv_ref_desde_medicion(ppv_mm_s, dist_m, n_exp=1.5):
    """Lleva un PPV medido (mm/s) a distancia `dist_m` a PPV a 25 ft (in/s)."""
    return (ppv_mm_s / MM_POR_PULGADA) * ((dist_m * M2FT) / D_REF_FT) ** n_exp
