// Adapted from upstream PR #412 Reproduce.java: takes a byte[] directly
// so PocRunner can drive it.
import org.apache.fontbox.pfb.PfbParser;

public class PfbFuzzer {
    public static void fuzzerTestOneInput(byte[] data) throws Exception {
        new PfbParser(data);
    }
}
