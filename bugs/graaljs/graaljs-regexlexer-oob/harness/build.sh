#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

HARNESS_DIR=/src/harness

if [ "${cmd}" = "build-libs" ]; then
    # Use the pom.xml at /src/harness to resolve graaljs from Maven Central.
    cd "${HARNESS_DIR}"
    mkdir -p /src/deps
    mvn -B -q dependency:copy-dependencies \
        -DoutputDirectory=/src/deps -DincludeScope=runtime -Dmdep.useBaseVersion=true
    echo "graaljs deps resolved"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    LIB=/out/lib
    mkdir -p "${OUT}" "${LIB}/classes" "${LIB}/deps"

    if [ ! -f "${LIB}/classes/PocRunner.class" ]; then
        cp /src/deps/*.jar "${LIB}/deps/" 2>/dev/null || true
        CP="${LIB}/deps/*"
        javac -d "${LIB}/classes" -encoding utf-8 \
            -cp "${CP}" \
            "${HARNESS_DIR}/RegExpFuzzer.java" \
            "${HARNESS_DIR}/PocRunner.java"
    fi

    cat > "${OUT}/harness" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="${DIR}/../lib"
exec java -Xmx512m \
    -cp "${LIB}/classes:${LIB}/deps/*" \
    -DtargetClass=RegExpFuzzer \
    PocRunner "$@"
EOF
    chmod +x "${OUT}/harness"
    echo "built ${OUT}/harness"
    exit 0
fi
exit 2
