import java.io.IOException;

import org.apache.fontbox.cmap.CMapParser;
import org.apache.pdfbox.io.RandomAccessReadBuffer;

public class CMapFuzzer {
    public static void fuzzerTestOneInput(byte[] data) throws Exception {
        // Jazzer-style harness: IOException is CMapParser's DECLARED "malformed CMap"
        // rejection, so it is a clean parse failure, not a finding.
        try {
            CMapParser parser = new CMapParser();
            parser.parse(new RandomAccessReadBuffer(data));
        } catch (IOException expected) {
            // malformed input rejected cleanly
        }
    }
}
