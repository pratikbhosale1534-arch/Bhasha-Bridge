"""Generates the sample study documents (run once: python samples/build_samples.py)."""
import os, io
HERE = os.path.dirname(__file__)

TXT = """# Newton's Laws of Motion

Newton's laws describe the relationship between a body and the forces acting on it.

## First Law (Inertia)
- An object at rest stays at rest unless acted on by a net force.
- The SI unit of force is the newton (N).

## Second Law
The net force equals mass times acceleration: F = m * a. For a 2 kg mass accelerating at 3 m/s^2, F = 6 N.

## Python example
```python
def force(mass, acceleration):
    return mass * acceleration
```
"""

def main():
    with open(os.path.join(HERE, "newton_laws.txt"), "w", encoding="utf-8") as f:
        f.write(TXT)

    from docx import Document
    d = Document()
    d.add_heading("Photosynthesis", 0)
    d.add_paragraph("Photosynthesis converts light energy into chemical energy stored in glucose.")
    d.add_heading("Overall equation", 1)
    d.add_paragraph("6CO2 + 6H2O -> C6H12O6 + 6O2")
    d.add_heading("Stages", 1)
    d.add_paragraph("Light-dependent reactions", style="List Bullet")
    d.add_paragraph("Calvin cycle", style="List Bullet")
    t = d.add_table(rows=2, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Organelle", "Role"
    t.rows[1].cells[0].text, t.rows[1].cells[1].text = "Chloroplast", "Site of photosynthesis"
    d.save(os.path.join(HERE, "photosynthesis.docx"))

    from pptx import Presentation
    p = Presentation()
    s = p.slides.add_slide(p.slide_layouts[1])
    s.shapes.title.text = "Introduction to Algorithms"
    s.placeholders[1].text = "An algorithm is a finite sequence of well-defined steps.\nTime complexity: O(n log n) for merge sort."
    p.save(os.path.join(HERE, "algorithms.pptx"))

    import fitz
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 80), "Ohm's Law", fontsize=22)
    pg.insert_text((72, 120), "Voltage V equals current I times resistance R: V = I * R.", fontsize=11)
    pg.insert_text((72, 140), "For I = 2 A and R = 5 ohm, V = 10 V.", fontsize=11)
    doc.save(os.path.join(HERE, "ohms_law.pdf"))

if __name__ == "__main__":
    main()
