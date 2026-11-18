# leyenda_kmz.py
"""
Leyenda PNG para el ScreenOverlay del KMZ (Google Earth).

Sin dependencias de QGIS: solo matplotlib. La leyenda se arma como un único
bloque (título + subtítulo + filas de color) dentro de un AnchoredOffsetbox
con marco, y se guarda con bbox_inches='tight', de modo que el lienzo se
ajusta al contenido real y ningún texto queda cortado, sin importar cuántas
bandas haya ni lo largas que sean las etiquetas.
"""

import io

TITULO_LEYENDA = "Nivel de Vibración VdB"


def _entero(v):
    """La leyenda usa SIEMPRE valores enteros, sin decimales."""
    return f"{int(round(float(v)))}"


def subtitulo_leyenda(ctx):
    """
    ctx = {'sumado': bool, 'lv_ref': float|None, 'foco': str|None,
           'n_focos': int, 'modo': 'suma'|'lineal'}
    Varios focos, suma   -> 'Suma Sinérgica (N focos)'.
    Varios focos, lineal -> 'Envolvente sin suma (N focos)'.
    Foco único           -> 'Ref: 94 VdB a 25 ft · <foco>'.
    """
    if not ctx:
        return None
    if ctx.get('sumado'):
        n = ctx.get('n_focos')
        base = ("Envolvente sin suma" if ctx.get('modo') == 'lineal'
                else "Suma Sinérgica")
        return f"{base} ({n} focos)" if n else base
    lv = ctx.get('lv_ref')
    if lv is None:
        return None
    sub = f"Ref: {_entero(lv)} VdB a 25 ft"
    if ctx.get('foco'):
        sub += f" · {ctx['foco']}"
    return sub


def entradas_leyenda(bandas):
    """
    Una fila por banda (texto entero ya calculado en 'rango'), de mayor a
    menor nivel: lo más crítico arriba, como se lee en el mapa.
    """
    filas = sorted(bandas, key=lambda b: b['lv_min'], reverse=True)
    return [(b['rango'], b['color']) for b in filas]


def generar_png_leyenda(entradas, titulo=TITULO_LEYENDA, subtitulo=None,
                        dpi=150):
    """
    entradas: lista [(texto, '#rrggbb'), ...] en el orden a mostrar.
    Devuelve los bytes PNG. El tamaño del lienzo lo decide el contenido
    (bbox_inches='tight'), no un figsize fijo.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.offsetbox import (AnchoredOffsetbox, DrawingArea,
                                      HPacker, TextArea, VPacker)
    from matplotlib.patches import Rectangle

    cabecera = [TextArea(titulo, textprops=dict(
        fontsize=10, fontweight="bold", color="#111111"))]
    if subtitulo:
        cabecera.append(TextArea(subtitulo, textprops=dict(
            fontsize=8, color="#444444", style="italic")))

    filas = []
    for texto, color in entradas:
        muestra = DrawingArea(18, 11, 0, 0)
        muestra.add_artist(Rectangle((0, 0), 18, 11, facecolor=color,
                                     edgecolor="#333333", linewidth=0.5))
        etq = TextArea(texto, textprops=dict(fontsize=8, color="#111111"))
        filas.append(HPacker(children=[muestra, etq], align="center",
                             pad=0, sep=6))

    bloque = VPacker(
        children=[VPacker(children=cabecera, align="left", pad=0, sep=2),
                  VPacker(children=filas, align="left", pad=0, sep=3)],
        align="left", pad=0, sep=8)

    # Figura sin ejes: el tamaño inicial es irrelevante porque el guardado
    # con bbox_inches='tight' recorta/expande al contenido real.
    fig = plt.figure(figsize=(1, 1), dpi=dpi)
    caja = AnchoredOffsetbox(loc="upper left", child=bloque, pad=0.6,
                             borderpad=0, frameon=True,
                             bbox_to_anchor=(0, 1),
                             bbox_transform=fig.transFigure)
    caja.patch.set_facecolor("white")
    caja.patch.set_edgecolor("#222222")
    caja.patch.set_linewidth(0.8)
    caja.patch.set_boxstyle("round,pad=0,rounding_size=0.15")
    fig.add_artist(caja)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight",
                pad_inches=0.04, transparent=True)
    plt.close(fig)
    return buf.getvalue()
