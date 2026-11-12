"""Point d'entrée appelé par QGIS au chargement du plugin."""


def classFactory(iface):
    from .plugin import ValetudoPlugin
    return ValetudoPlugin(iface)
