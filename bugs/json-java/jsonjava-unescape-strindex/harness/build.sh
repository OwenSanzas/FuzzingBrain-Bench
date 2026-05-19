#!/bin/bash
set -euo pipefail
cmd="${1:?usage: build.sh build-libs | harness <config>}"

# JSON-java is pure Java. We build once (Maven), reuse jar across configs.

PROJ_JAR=/src/json-java/target/json-java-1.0-SNAPSHOT.jar
HARNESS_DIR=/src/harness

if [ "${cmd}" = "build-libs" ]; then
    cd /src/json-java
    mvn -B -q -DskipTests package
    # Maven names the jar based on artifactId+version in pom.xml. Discover.
    JAR=$(find /src/json-java/target -maxdepth 1 -name '*.jar' \
        -not -name '*sources*' -not -name '*javadoc*' \
        | head -1)
    test -n "${JAR}"
    ln -sf "${JAR}" /src/json-java/project.jar
    echo "built ${JAR}"
    exit 0
fi

if [ "${cmd}" = "harness" ]; then
    CONFIG="${2:?harness needs <config>}"
    OUT=/out/${CONFIG}
    LIB=/out/lib
    mkdir -p "${OUT}" "${LIB}/classes"

    # Compile harness + PocRunner into shared lib/classes (idempotent across configs).
    if [ ! -f "${LIB}/classes/PocRunner.class" ]; then
        cp /src/json-java/project.jar "${LIB}/json-java.jar"
        javac -d "${LIB}/classes" \
            -cp "${LIB}/json-java.jar" \
            "${HARNESS_DIR}/XmlToJsonFuzzer.java" \
            "${HARNESS_DIR}/PocRunner.java"
    fi

    # Per-config wrapper script. JVM flags differ across configs:
    #   debug/debug-asan/release-asan -> same JVM args (-Xmx512m)
    #   coverage -> add jacoco agent for line coverage emission
    case "${CONFIG}" in
        debug|debug-asan|release-asan)
            cat > "${OUT}/harness" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="${DIR}/../lib"
exec java -Xmx512m \
    -cp "${LIB}/classes:${LIB}/json-java.jar" \
    -DtargetClass=XmlToJsonFuzzer \
    PocRunner "$@"
EOF
            ;;
        coverage)
            # Coverage build — JaCoCo agent emits exec data on exit.
            cat > "${OUT}/harness" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
LIB="${DIR}/../lib"
RUNDIR="$(pwd)"
exec java -Xmx512m \
    -javaagent:"${LIB}/jacocoagent.jar"=destfile="${RUNDIR}/jacoco.exec" \
    -cp "${LIB}/classes:${LIB}/json-java.jar" \
    -DtargetClass=XmlToJsonFuzzer \
    PocRunner "$@"
EOF
            ;;
        *) echo "unknown config: ${CONFIG}" >&2; exit 2 ;;
    esac

    chmod +x "${OUT}/harness"
    echo "built ${OUT}/harness"
    exit 0
fi
exit 2
