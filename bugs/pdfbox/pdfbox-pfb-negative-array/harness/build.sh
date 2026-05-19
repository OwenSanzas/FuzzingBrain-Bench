#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

HARNESS_DIR=/src/harness
PDFBOX_ROOT=/src/pdfbox

if [ "${cmd}" = "build-libs" ]; then
    cd "${PDFBOX_ROOT}"
    mvn -B -q -DskipTests -Dcheckstyle.skip -Dspotless.check.skip=true \
        -pl fontbox -am install
    JAR=$(find "${PDFBOX_ROOT}/fontbox/target" -maxdepth 1 -name 'fontbox-*.jar' \
        -not -name '*sources*' -not -name '*javadoc*' -not -name '*tests*' \
        | head -1)
    test -n "${JAR}"
    cp "${JAR}" /src/fontbox.jar
    mkdir -p /src/deps
    mvn -B -q -pl fontbox dependency:copy-dependencies \
        -DoutputDirectory=/src/deps -DincludeScope=runtime -Dmdep.useBaseVersion=true
    echo "built fontbox jar"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    LIB=/out/lib
    mkdir -p "${OUT}" "${LIB}/classes" "${LIB}/deps"

    if [ ! -f "${LIB}/classes/PocRunner.class" ]; then
        cp /src/fontbox.jar "${LIB}/fontbox.jar"
        cp /src/deps/*.jar "${LIB}/deps/" 2>/dev/null || true
        CP="${LIB}/fontbox.jar:${LIB}/deps/*"
        javac -d "${LIB}/classes" \
            -cp "${CP}" \
            "${HARNESS_DIR}/PfbFuzzer.java" \
            "${HARNESS_DIR}/PocRunner.java"
    fi

    cat > "${OUT}/harness" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="${DIR}/../lib"
exec java -Xmx256m \
    -cp "${LIB}/classes:${LIB}/fontbox.jar:${LIB}/deps/*" \
    -DtargetClass=PfbFuzzer \
    PocRunner "$@"
EOF
    chmod +x "${OUT}/harness"
    echo "built ${OUT}/harness"
    exit 0
fi
exit 2
