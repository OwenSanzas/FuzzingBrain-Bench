// Adapted from upstream issue #985 - driven by a raw byte[] so PocRunner
// can invoke it directly without Jazzer.
import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.PolyglotException;
import java.nio.charset.StandardCharsets;

public class IntlFuzzer {
    private static final Context context = Context.newBuilder("js")
        .allowAllAccess(false)
        .option("engine.WarnInterpreterOnly", "false")
        .build();

    public static void fuzzerTestOneInput(byte[] data) {
        String locale = new String(data, StandardCharsets.UTF_8);
        if (locale.isEmpty()) return;
        String jsCode = "new Intl.Locale('" + escapeForJS(locale) + "');";
        try {
            context.eval("js", jsCode);
        } catch (PolyglotException e) {
            if (e.isInternalError()) {
                // BUG: user input must not surface as an "internal error".
                throw new RuntimeException("Internal error from user input", e);
            }
        }
    }

    private static String escapeForJS(String s) {
        StringBuilder sb = new StringBuilder();
        for (char c : s.toCharArray()) {
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '\'': sb.append("\\'"); break;
                case '\n': sb.append("\\n"); break;
                default:
                    if (c >= 32 && c < 127) sb.append(c);
                    else sb.append(String.format("\\u%04x", (int) c));
            }
        }
        return sb.toString();
    }
}
