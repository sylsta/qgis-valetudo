"""Internationalisation helpers.

Source strings are written in English and wrapped in ``tr()``. Translations
live in ``i18n/valetudo_qgis_<lang>.ts`` and are compiled to ``.qm`` files
(see ``scripts/compile_translations.sh``).
"""

import os

from qgis.PyQt.QtCore import QCoreApplication, QLocale, QTranslator
from qgis.core import QgsSettings

CONTEXT = "ValetudoQgis"
I18N_DIR = os.path.join(os.path.dirname(__file__), "i18n")


def tr(text):
    """Translate a user-facing string in the plugin's single context."""
    return QCoreApplication.translate(CONTEXT, text)


def ui_language():
    """Two-letter code of the QGIS interface language (e.g. 'fr')."""
    settings = QgsSettings()
    locale = ""
    if settings.value("locale/overrideFlag", False, type=bool):
        locale = settings.value("locale/userLocale", "", type=str)
    return (locale or QLocale().name())[:2]


def install_translator():
    """Load and install the translation matching the QGIS UI language.

    Returns the QTranslator (keep a reference to it), or None if no
    translation is available, in which case the English source is shown.
    """
    path = os.path.join(I18N_DIR, f"valetudo_qgis_{ui_language()}.qm")
    if not os.path.exists(path):
        return None
    translator = QTranslator()
    if not translator.load(path):
        return None
    QCoreApplication.installTranslator(translator)
    return translator


def remove_translator(translator):
    if translator is not None:
        QCoreApplication.removeTranslator(translator)
