# __init__.py
def classFactory(iface):
    from .plugin_main import VibracionesDecibelPlugin
    return VibracionesDecibelPlugin(iface)
