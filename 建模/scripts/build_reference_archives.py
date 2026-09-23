"""Create clearly labelled text-only PDF archives for references originally published as HTML."""
from datetime import date
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "references"
OUT = REF / "web_pdf"
ITEMS = [
    ("01_xinhua", "[1] 记者手记：抵近广西横州镇龙乡", "连日来，", "【纠错】"),
    ("02_xinhua_daily", "[2] 三进“孤岛乡”", "台风“美莎克”", "【纠错】"),
    ("03_cop_dem", "[3] Copernicus DEM 产品网页", "The Copernicus DEM is a Digital Surface Model", "\nRelated news\n"),
    ("09_dji_specs", "[9] Matrice 350 RTK 技术参数网页", "飞行器\n尺寸", None),
    ("10_doodle_sense", "[10] Doodle Labs Sense 产品公告", "Doodle Labs releases Sense, new interference-avoidance features for Mesh Rider Radio\nBy", "\nDefense\nPublic Safety\nCommercial\nCapabilities"),
]


class BlockText(HTMLParser):
    """Keep inline emphasis/links inside their original text block."""
    blocks = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "div", "section", "br"}

    def __init__(self):
        super().__init__()
        self.lines, self.current, self.skip = [], [], 0

    def flush(self):
        text = " ".join(" ".join(self.current).split())
        if text:
            self.lines.append(text)
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.blocks:
            self.flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag in self.blocks:
            self.flush()

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.current.append(data.strip())


def build():
    OUT.mkdir(exist_ok=True)
    pdfmetrics.registerFont(TTFont("SourceCN", str(ROOT.parent / "写作/fonts/SimSun.ttf")))
    styles = {
        "title": ParagraphStyle("Title", fontName="SourceCN", fontSize=18, leading=25, textColor=colors.HexColor("#163956"), spaceAfter=14),
        "note": ParagraphStyle("Note", fontName="SourceCN", fontSize=9, leading=14, textColor=colors.HexColor("#405669"), wordWrap="CJK", spaceAfter=10),
        "body": ParagraphStyle("Body", fontName="SourceCN", fontSize=10.5, leading=17, wordWrap="CJK", spaceAfter=7, alignment=TA_LEFT),
        "heading": ParagraphStyle("Heading", fontName="SourceCN", fontSize=11.5, leading=18, spaceBefore=9, spaceAfter=6, keepWithNext=True, textColor=colors.HexColor("#163956")),
    }
    manifest = []
    for key, title, start, end in ITEMS:
        metadata = json.loads((REF / "metadata" / (key + ".json")).read_text(encoding="utf-8"))
        full = (REF / "text" / (key + ".txt")).read_text(encoding="utf-8")
        if key in ("03_cop_dem", "10_doodle_sense"):
            blocks = BlockText()
            blocks.feed((REF / "web" / (key + ".html")).read_text(encoding="utf-8"))
            blocks.flush()
            full = "\n".join(blocks.lines)
            if key == "10_doodle_sense":
                start = "Doodle Labs is pleased to announce"
                end = "\nDefense Public Safety Commercial"
        begin = full.index(start)
        finish = full.index(end, begin) if end else len(full)
        body = full[begin:finish].strip()
        paragraphs = body.splitlines()
        path = OUT / (key + "_网页正文存档.pdf")
        story = [Paragraph(escape(title), styles["title"]),
                 Paragraph("网页正文存档 · 非网站发布的原版PDF", styles["note"]),
                 Paragraph("来源：" + escape(metadata["final_url"]), styles["note"]),
                 Paragraph("获取日期：2026-09-23。由已获取的官方网页正文重新排版，保留文本和图注，未包含原网页图片、交互组件及导航。原始HTML、响应哈希和访问记录另行保存；此处页码仅属于本存档。", styles["note"]),
                 Spacer(1, 8)]
        published = {"01_xinhua":"2026-07-09 16:54:55", "02_xinhua_daily":"2026-07-12 09:47:29", "10_doodle_sense":"2023-10-16"}.get(key)
        if published:
            story.append(Paragraph("原文发布日期：" + published, styles["note"]))
        for line in paragraphs:
            if line.strip():
                is_heading = key == "10_doodle_sense" and line in {"How Sense works", "Commercial Applications", "Defense Applications: Battle-tested reliability", "About Mesh Rider"}
                story.append(Paragraph(escape(line), styles["heading" if is_heading else "body"]))
        def footer(canvas, doc):
            canvas.setFont("SourceCN", 8)
            canvas.setFillColor(colors.HexColor("#617185"))
            canvas.drawString(42, 25, "网页正文存档 | 原网页信息以保存的HTML及当前网站为准")
            canvas.drawRightString(A4[0]-42, 25, str(doc.page))
        doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=44, rightMargin=44, topMargin=42, bottomMargin=45,
                                title=title + "（网页正文存档）", author="文献整理：网页正文存档")
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        reader = PdfReader(path)
        manifest.append({"id":key, "path":str(path.relative_to(REF)), "kind":"generated_text_archive_not_publisher_pdf",
                         "source_url":metadata["final_url"], "source_sha256":metadata["sha256"],
                         "sha256":sha256(path.read_bytes()).hexdigest(), "pages":len(reader.pages), "body_characters":len(body)})
    (REF / "metadata/web_pdf_archives.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
