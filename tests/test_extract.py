import io, os, zipfile, pytest
import extract as ex

S = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
def read(n): return open(os.path.join(S, n), "rb").read()

@pytest.mark.parametrize("name,needle", [
    ("newton_laws.txt", "F = m * a"), ("photosynthesis.docx", "6CO2 + 6H2O"),
    ("algorithms.pptx", "O(n log n)"), ("ohms_law.pdf", "V = I * R")])
def test_samples_extract(name, needle):
    d = ex.extract_document(name, read(name))
    assert needle in d.text and d.file_type == name.rsplit(".", 1)[1]

def test_structure_preserved():
    t = ex.extract_document("photosynthesis.docx", read("photosynthesis.docx")).text
    assert t.startswith("# Photosynthesis") and "## Stages" in t and "- Calvin cycle" in t and "| Organelle | Role |" in t
    p = ex.extract_document("algorithms.pptx", read("algorithms.pptx")).text
    assert p.count("Introduction to Algorithms") == 1  # title not duplicated
    assert ex.extract_document("ohms_law.pdf", read("ohms_law.pdf")).text.startswith("# Ohm")

@pytest.mark.parametrize("name,raw,status", [
    ("a.exe", b"MZ", 400), ("a.pdf", b"nope", 400), ("a.docx", b"PKjunk", 400), ("a.txt", b"", 400),
    ("a.doc", b"x", 400), ("a.txt", b"\x00\x01" * 100, 400), ("noext", b"x", 400)])
def test_rejections(name, raw, status):
    with pytest.raises(ex.ExtractionError) as e: ex.extract_document(name, raw)
    assert e.value.status == status

def test_oversize(monkeypatch):
    monkeypatch.setattr(ex, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(ex.ExtractionError) as e: ex.extract_document("a.txt", b"x" * 101)
    assert e.value.status == 413

def test_zip_that_is_not_office():
    b = io.BytesIO(); z = zipfile.ZipFile(b, "w"); z.writestr("x.txt", "hi"); z.close()
    with pytest.raises(ex.ExtractionError): ex.extract_document("a.docx", b.getvalue())

def test_utf8_devanagari_txt():
    assert "नमस्ते" in ex.extract_document("a.txt", "नमस्ते दुनिया".encode("utf-8")).text

def test_pdf_without_text_is_clear_error():
    import pymupdf
    d = pymupdf.open(); d.new_page(); raw = d.tobytes()
    with pytest.raises(ex.ExtractionError) as e: ex.extract_document("blank.pdf", raw)
    assert "scanned" in e.value.message.lower()
