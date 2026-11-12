#!/usr/bin/env sh
# Compile valetudo_qgis/i18n/*.ts into .qm files loaded by the plugin.
# Needs Qt's lrelease (Debian/Ubuntu: qttools5-dev-tools or qt6-l10n-tools;
# Windows/OSGeo4W: shipped with the Qt tools).
set -eu
cd "$(dirname "$0")/.."

LRELEASE=""
for candidate in lrelease lrelease-qt5 lrelease-qt6 \
                 /usr/lib/qt5/bin/lrelease /usr/lib/qt6/bin/lrelease; do
    if command -v "$candidate" >/dev/null 2>&1; then
        LRELEASE="$candidate"
        break
    fi
done
if [ -z "$LRELEASE" ]; then
    echo "lrelease not found: install the Qt linguist tools." >&2
    exit 1
fi

for ts in valetudo_qgis/i18n/*.ts; do
    "$LRELEASE" "$ts" -qm "${ts%.ts}.qm"
done
