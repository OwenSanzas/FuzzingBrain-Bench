// PocRunner is a tiny Java main() that calls a Jazzer-style fuzz harness
// against a single PoC blob. Lets the original exception bubble up so the
// JVM prints the natural stack trace.
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Paths;

public class PocRunner {
    public static void main(String[] args) throws Throwable {
        if (args.length < 1) {
            System.err.println("Usage: PocRunner <poc.bin>");
            System.exit(2);
        }
        byte[] data = Files.readAllBytes(Paths.get(args[0]));
        String targetClass = System.getProperty("targetClass");
        if (targetClass == null || targetClass.isEmpty()) {
            System.err.println("error: -DtargetClass=... required");
            System.exit(2);
        }
        Class<?> cls = Class.forName(targetClass);
        Method m = cls.getMethod("fuzzerTestOneInput", byte[].class);
        try {
            m.invoke(null, (Object) data);
        } catch (InvocationTargetException ite) {
            // Re-throw the underlying cause so the JVM prints the original
            // stack trace (otherwise reflection wraps it in InvocationTargetException).
            throw ite.getCause();
        }
    }
}
