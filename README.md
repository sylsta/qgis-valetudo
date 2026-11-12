# Valetudo pour QGIS

Plugin d'apprentissage : affiche et pilote un robot aspirateur Valetudo dans QGIS.

## Installation (développement)

1. Dans QGIS : *Préférences > Profils utilisateurs > Ouvrir le dossier du profil actif*.
2. Aller dans `python/plugins/` (le créer s'il n'existe pas).
3. Y copier le dossier `valetudo_qgis` de ce dépôt (ou y faire un lien symbolique
   vers lui, pour que chaque `git pull` soit pris en compte directement).
4. Redémarrer QGIS, puis activer « Valetudo » dans *Extensions > Installer/Gérer les extensions > Installées*.

Conseil : installer aussi l'extension **Plugin Reloader** pour recharger le plugin
après chaque modification du code, sans redémarrer QGIS.

## Utilisation

1. *Extensions > Valetudo > Paramètres…* : saisir l'adresse du robot (ex. `http://192.168.1.23`).
2. **Afficher la carte** : crée le groupe « Valetudo » et passe le projet en « Pas de SCR ».
3. **Suivi en direct** : rafraîchit la position du robot et sa trajectoire.
4. Sélectionner des pièces dans « Valetudo - carte », puis **Nettoyer les pièces sélectionnées**.

## Organisation du code

| Fichier              | Rôle                                                        |
|----------------------|-------------------------------------------------------------|
| `__init__.py`        | Point d'entrée appelé par QGIS (`classFactory`)             |
| `metadata.txt`       | Nom, version, compatibilité QGIS                            |
| `plugin.py`          | Barre d'outils, menu, liaison entre client et couches       |
| `client.py`          | Appels HTTP asynchrones à l'API Valetudo                    |
| `map_layers.py`      | JSON de carte -> couches QGIS, mises à jour incrémentales    |
| `coords.py`          | Conversion de repère (seul endroit qui inverse l'axe Y)     |
| `settings_dialog.py` | Paramètres (URL, authentification, intervalle)              |

## Pistes pour la suite

- Outil de carte (`QgsMapTool`) pour dessiner une zone de nettoyage.
- Outil « aller ici » (clic sur la carte -> `GoToLocationCapability`).
- Flèche orientée selon le champ `angle` du robot.
- Remplacer le polling par le flux SSE de Valetudo.
- Afficher batterie et état dans un dock (`/robot/state/attributes`).

## Licence

GPL v2 ou ultérieure — voir [LICENSE](LICENSE).
