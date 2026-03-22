import base64
import io


def _error(message: str) -> dict:
    return {"status": "error", "error": message, "data": None}


def run(params: dict) -> dict:
    params = params or {}
    text = params.get("text")
    if text is None:
        return _error("Missing required parameter: text")

    # Minimal PDF generation without external dependencies.
    content_lines = []
    y = 750
    for line in str(text).splitlines():
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content_lines.append(f"1 0 0 1 72 {y} Tm ({safe[:120]}) Tj")
        y -= 14
        if y < 72:
            break
    stream = "BT /F1 12 Tf " + " ".join(content_lines) + " ET"
    stream_bytes = stream.encode("latin1")

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
                   b"/Resources << /Font << /F1 5 0 R >> >> >>")
    objects.append(b"<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n" +
                   stream_bytes + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    xref = []
    pdf = io.BytesIO()
    pdf.write(b"%PDF-1.4\n")
    for idx, obj in enumerate(objects, start=1):
        xref.append(pdf.tell())
        pdf.write(f"{idx} 0 obj\n".encode("ascii"))
        pdf.write(obj)
        pdf.write(b"\nendobj\n")
    xref_offset = pdf.tell()
    pdf.write(b"xref\n0 " + str(len(objects) + 1).encode("ascii") + b"\n")
    pdf.write(b"0000000000 65535 f \n")
    for offset in xref:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(b"trailer\n<< /Size " + str(len(objects) + 1).encode("ascii") +
              b" /Root 1 0 R >>\nstartxref\n" +
              str(xref_offset).encode("ascii") + b"\n%%EOF")
    pdf_bytes = pdf.getvalue()

    data = {"pdf_base64": base64.b64encode(pdf_bytes).decode("utf-8")}
    return {"status": "ok", "error": None, "data": data}
