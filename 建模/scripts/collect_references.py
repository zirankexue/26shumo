"""Download public references and preserve retrieval evidence; no model changes."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / "references"


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.links, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        data = dict(attrs)
        if tag == "a" and data.get("href"):
            self.links.append(data["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, text):
        if not self.skip and text.strip():
            self.parts.append(text.strip())


def fetch(label, url):
    for folder in ("pdf", "web", "metadata", "text", "previews"):
        (ROOT / folder).mkdir(parents=True, exist_ok=True)
    record = {"id": label, "url": url, "retrieved_utc": datetime.now(timezone.utc).isoformat()}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; AcademicReferenceAudit/1.0)", "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                raw = response.read()
                record.update(status=response.status, final_url=response.url, content_type=response.headers.get("Content-Type", ""))
        except urllib.error.URLError as first_error:
            # Windows Schannel uses the system trust store; never disable TLS verification.
            record["urllib_error"] = str(first_error)
            temp = ROOT / "web" / (label + ".download")
            result = subprocess.run(["curl.exe", "-sS", "-L", "--max-time", "35", "-o", str(temp), "-w", "%{json}", url], capture_output=True, timeout=40)
            info = json.loads(result.stdout.decode("utf-8"))
            record.update(status=info.get("http_code"), final_url=info.get("url_effective"), content_type=info.get("content_type", ""), transport="curl Schannel; TLS verification enabled")
            if result.returncode or record["status"] >= 400:
                raise RuntimeError(f'curl exit={result.returncode}, HTTP={record["status"]}: {result.stderr.decode("utf-8", "replace")}')
            raw = temp.read_bytes()
            temp.unlink()
        record.update(bytes=len(raw), sha256=sha256(raw).hexdigest())
        if raw.startswith(b"%PDF-"):
            path = ROOT / "pdf" / (label + ".pdf")
            path.write_bytes(raw)
            from pypdf import PdfReader
            reader = PdfReader(path)
            pages = [{"page": i+1, "text": p.extract_text() or ""} for i,p in enumerate(reader.pages)]
            (ROOT / "text" / (label + ".json")).write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")
            (ROOT / "text" / (label + ".txt")).write_text("\n\n".join(f'=== PDF PAGE {p["page"]} ===\n{p["text"]}' for p in pages), encoding="utf-8")
            record.update(kind="original_pdf", pages=len(reader.pages), pdf_metadata={str(k):str(v) for k,v in (reader.metadata or {}).items()})
        else:
            decoded = raw.decode("utf-8", "replace")
            if "json" in record['content_type'] or decoded.lstrip().startswith(("{", "[")):
                path = ROOT / "metadata" / (label + "_response.json")
                path.write_bytes(raw)
                record["kind"] = "json"
            else:
                path = ROOT / "web" / (label + ".html")
                path.write_bytes(raw)
                page = Page(); page.feed(decoded)
                title = re.search(r"<title[^>]*>(.*?)</title>", decoded, re.I | re.S)
                record.update(kind="html", title=title.group(1).strip() if title else None, links=page.links)
                (ROOT / "text" / (label + ".txt")).write_text("\n".join(page.parts), encoding="utf-8")
        record["path"] = str(path.relative_to(ROOT))
    except Exception as exc:
        record.update(status=getattr(exc, "code", "failed"), error=str(exc))
    (ROOT / "metadata" / (label + ".json")).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k:v for k,v in record.items() if k not in ("links", "pdf_metadata")}


INITIAL = [
    ("01_xinhua", "https://www.xinhuanet.com/politics/20260709/acf8e4b353304bb78007d8224f1cd2ef/c.html"),
    ("02_xinhua_daily", "https://www.news.cn/local/20260712/8bd1f64af3124569b5cf4415fc013acd/c.html"),
    ("03_cop_dem", "https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM"),
    ("04_crossref", "https://api.crossref.org/works/10.1109/TSMC.2016.2582745"),
    ("05_crossref", "https://api.crossref.org/works/10.1016/j.trd.2020.102668"),
    ("06_itu", "https://www.itu.int/rec/R-REC-P.525-5-202411-I/en"),
    ("07_ti_slaa287b", "https://www.ti.com/lit/an/slaa287b/slaa287b.pdf"),
    ("08_crossref", "https://api.crossref.org/works/10.1109/MCOM.2016.7470933"),
    ("09_dji_specs", "https://enterprise.dji.com/matrice-350-rtk/specs"),
    ("10_doodle_sense", "https://doodlelabs.com/news/sense-interference-avoidance-release/"),
]

SUPPLEMENTS = [
    ("03_cop_dem_handbook", "https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf"),
    ("04_dorling_arxiv", "https://arxiv.org/pdf/1608.02305"),
    ("06_itu_p525_en", "https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.525-5-202411-I!!PDF-E.pdf"),
    ("06_itu_p525_zh", "https://www.itu.int/dms_pubrec/itu-r/rec/p/R-REC-P.525-5-202411-I!!PDF-C.pdf"),
    ("08_zeng_arxiv", "https://arxiv.org/pdf/1602.03602"),
    ("09_dji_manual_cn", "https://dl.djicdn.com/downloads/matrice_350_rtk/20240814/Matrice_350_RTK_User_Manual_v1.2_cn.pdf"),
]


def collect(item, refresh=False):
    label, url = item
    record_path = ROOT / "metadata" / (label + ".json")
    if not refresh and record_path.exists():
        record = json.loads(record_path.read_text(encoding="utf-8"))
        path = ROOT / record.get("path", "nonexistent")
        if record.get("status") == 200 and path.is_file() and sha256(path.read_bytes()).hexdigest() == record.get("sha256"):
            return {"id": label, "status": "cached_and_hash_verified", "path": str(path)}
    return fetch(label, url)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Refresh even hash-verified downloads.")
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=6) as executor:
        for result in executor.map(lambda item: collect(item, args.refresh), INITIAL + SUPPLEMENTS):
            print(json.dumps(result, ensure_ascii=False), flush=True)
