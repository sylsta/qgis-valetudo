"""Entry point called by QGIS when the plugin is loaded."""


def classFactory(iface):
    from .plugin import ValetudoPlugin
    return ValetudoPlugin(iface)
