import org.graalvm.polyglot.Context;
import org.graalvm.polyglot.PolyglotException;
import java.nio.charset.StandardCharsets;

public class RegExpFuzzer {
    private static final Context context = Context.newBuilder("js")
        .allowAllAccess(false)
        .option("engine.WarnInterpreterOnly", "false")
        .build();

    public static void fuzzerTestOneInput(byte[] data) {
        String pattern = new String(data, StandardCharsets.UTF_8);
        if (pattern.isEmpty() || pattern.length() > 200) return;
        String esc = escapeForJS(pattern);
        // Try both unicode flags - the v-flag exercises the RegexLexer path.
        String[] codes = {
            "try { new RegExp('" + esc + "', 'v'); } catch(e) {}",
            "try { new RegExp('" + esc + "', 'u'); } catch(e) {}",
            "try { new RegExp('" + esc + "'); } catch(e) {}",
        };
        for (String code : codes) {
            try {
                context.eval("js", code);
            } catch (PolyglotException e) {
                if (e.isInternalError()) {
                    throw new RuntimeException("Internal error from user regex: " + code, e);
                }
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
                    if (c < 32 || c > 126) sb.append(String.format("\\u%04x", (int) c));
                    else sb.append(c);
            }
        }
        return sb.toString();
    }
}
