import java.io.IOException;
import java.util.List;

import org.apache.pdfbox.contentstream.operator.Operator;
import org.apache.pdfbox.cos.COSDictionary;
import org.apache.pdfbox.pdfparser.PDFStreamParser;
import org.apache.pdfbox.pdmodel.graphics.image.PDInlineImage;

public class InlineImageFuzzer {
    public static void fuzzerTestOneInput(byte[] data) throws Exception {
        List<Object> tokens;
        try {
            tokens = new PDFStreamParser(data).parse();
        } catch (IOException | RuntimeException e) {
            return;
        }

        for (Object token : tokens) {
            if (!(token instanceof Operator)) {
                continue;
            }
            Operator op = (Operator) token;
            COSDictionary params = op.getImageParameters();
            if (params == null) {
                continue;
            }
            PDInlineImage image;
            try {
                image = new PDInlineImage(params, op.getImageData(), null);
            } catch (IOException e) {
                continue;
            }
            image.getDecode();
            image.getColorSpace();
            image.getWidth();
            image.getHeight();
            image.isStencil();
        }
    }
}
