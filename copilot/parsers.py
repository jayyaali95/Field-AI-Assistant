"""Turn each corpus file into citable chunks: (location, text).

The location is what a technician would cite: a page, a section heading, a case number, an email.
"""
import csv
import email
import io
import json
import re
from dataclasses import dataclass, field
from email import policy
from pathlib import Path

import docx
import pymupdf
import pytesseract
from bs4 import BeautifulSoup
from PIL import Image, ImageFilter

from . import config

MAX_CHUNK_CHARS = 6000


@dataclass
class Chunk:
    location: str
    text: str


@dataclass
class Parsed:
    chunks: list[Chunk]
    note: str = ""  # shown next to the title, e.g. "last modified Mar 21, 2024"
    warnings: list[str] = field(default_factory=list)


def parse_file(path: Path) -> Parsed:
    suffix = path.suffix.lower()
    parser = {
        ".pdf": parse_pdf,
        ".docx": parse_docx,
        ".html": parse_html,
        ".htm": parse_html,
        ".eml": parse_eml,
        ".json": parse_json,
        ".csv": parse_csv,
    }.get(suffix)
    if parser is None:
        return Parsed([], warnings=[f"no parser for {suffix}"])
    parsed = parser(path)
    parsed.chunks = [c for chunk in parsed.chunks for c in _split(chunk) if c.text.strip()]
    return parsed


def _split(chunk: Chunk) -> list[Chunk]:
    """Split oversized chunks on paragraph boundaries, keeping the same location."""
    if len(chunk.text) <= MAX_CHUNK_CHARS:
        return [chunk]
    parts, current = [], ""
    for para in chunk.text.split("\n"):
        if current and len(current) + len(para) > MAX_CHUNK_CHARS:
            parts.append(current)
            current = ""
        current += para + "\n"
    if current.strip():
        parts.append(current)
    return [Chunk(f"{chunk.location} (part {i})", p) for i, p in enumerate(parts, 1)]


# ---------- PDF ----------

def parse_pdf(path: Path) -> Parsed:
    parsed = Parsed([])
    with pymupdf.open(path) as pdf:
        for number, page in enumerate(pdf, 1):
            try:
                text = page.get_text().strip()
            except Exception as error:  # broken page in an otherwise readable file
                parsed.warnings.append(f"p. {number} unreadable ({error}), skipped")
                continue
            if len(text) < 30:  # no text layer, probably a scan
                text = _ocr(page)
            if text:
                parsed.chunks.append(Chunk(f"p. {number}", text))
            else:
                parsed.warnings.append(f"p. {number} has no readable text")
    return parsed


def _ocr(page) -> str:
    image = Image.open(io.BytesIO(page.get_pixmap(dpi=300).tobytes("png"))).convert("L")
    image = image.filter(ImageFilter.MedianFilter(3))  # removes scanner speckle
    # psm 6 reads the page as one block, which keeps table rows (name | value) on one line.
    return pytesseract.image_to_string(image, lang=config.OCR_LANG, config="--psm 6").strip()


# ---------- DOCX ----------

# "5. Werkwijze / Method" is a top-level section, "5.1 Eerste beoordeling" is not.
NUMBERED_SECTION = re.compile(r"^\d+\.?\s+\S.{0,80}$")


def parse_docx(path: Path) -> Parsed:
    """One chunk per top-level section. Subsections (6.1, 6.2...) stay inside it with their headings,
    so a whole procedure ("6. Procedure", steps 1-18 with their tables) comes back as one unit."""
    document = docx.Document(str(path))
    chunks: list[Chunk] = []
    location, lines = "Header", []

    def flush():
        if lines:
            chunks.append(Chunk(location, "\n".join(lines)))

    # Walk the body in order so tables stay inside the section they belong to.
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = docx.text.paragraph.Paragraph(block, document)
            text = para.text.strip()
            if not text:
                continue
            style = para.style.name if para.style is not None else ""
            if _is_section(text, style):
                flush()
                location, lines = text, []
            lines.append(text)
        elif tag == "tbl":
            table = docx.table.Table(block, document)
            for row in table.rows:
                cells = []
                for cell in row.cells:  # merged cells repeat, keep one
                    value = cell.text.strip()
                    if not cells or cells[-1] != value:
                        cells.append(value)
                if any(cells):
                    lines.append(" | ".join(cells))
    flush()
    return Parsed(chunks)


def _is_section(text: str, style: str) -> bool:
    if style in ("Heading 1", "Title"):
        return True
    # Legacy Kyltek instructions use Normal style for headings, e.g. "5. Werkwijze / Method".
    return style == "Normal" and "\t" not in text and bool(NUMBERED_SECTION.match(text)) and not text.endswith(".")


# ---------- Confluence HTML ----------

def parse_html(path: Path) -> Parsed:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    for toc in soup.select(".toc-macro, .toc, nav"):
        toc.decompose()
    meta = soup.find(class_="page-metadata")
    note = ""
    if meta:
        found = re.search(r"last modified on (.+)", meta.get_text(" ", strip=True))
        note = f"last modified {found.group(1)}" if found else ""
    main = soup.find(id="main-content") or soup.body or soup

    chunks: list[Chunk] = []
    location, lines = "Intro", []
    for element in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr", "pre"]):
        if element.find_parent(["li", "td", "th"]) and element.name != "tr":
            continue  # already part of the parent's text
        if element.name == "tr":
            text = " | ".join(c.get_text(" ", strip=True) for c in element.find_all(["td", "th"]))
        elif element.name == "pre":
            text = element.get_text().strip()
        else:
            text = element.get_text(" ", strip=True)
        if not text:
            continue
        if element.name in ("h1", "h2", "h3", "h4"):
            if lines:
                chunks.append(Chunk(location, "\n".join(lines)))
            location, lines = text, []
        lines.append(("- " if element.name == "li" else "") + text)
    if lines:
        chunks.append(Chunk(location, "\n".join(lines)))
    return Parsed(chunks, note=note)


# ---------- Email ----------

SEPARATOR = re.compile(r"^_{10,}\s*$", re.M)
HEADER = re.compile(r"^(From|Van|Sent|Verzonden|Date):\s*(.+)$", re.M)


def parse_eml(path: Path) -> Parsed:
    message = email.message_from_binary_file(path.open("rb"), policy=policy.default)
    body = message.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body else ""
    if body is not None and body.get_content_type() == "text/html":
        text = BeautifulSoup(text, "html.parser").get_text("\n")

    chunks = []
    for i, part in enumerate(SEPARATOR.split(text)):
        part = part.strip()
        if not part:
            continue
        if i == 0:
            sender, date = str(message["from"]), str(message["date"])
        else:
            headers = dict(HEADER.findall(part[:600]))
            sender = headers.get("From") or headers.get("Van", "?")
            date = headers.get("Sent") or headers.get("Verzonden") or headers.get("Date", "?")
        chunks.append(Chunk(f"Email from {sender}, {date}", f"Subject: {message['subject']}\n{part}"))
    return Parsed(chunks)


# ---------- JSON: Teams export, Salesforce export, anything else ----------

def parse_json(path: Path) -> Parsed:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("value"), list) and "channel" in data:
        return _teams(data)
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        chunks = []
        for i, record in enumerate(data["records"], 1):
            label = record.get("CaseNumber") and f"Case {record['CaseNumber']}" or f"Record {i}"
            chunks.append(Chunk(label, "\n".join(_flatten(record))))
        return Parsed(chunks)
    return Parsed([Chunk("Document", "\n".join(_flatten(data)))])


def _teams(data: dict) -> Parsed:
    threads: dict[str, list[str]] = {}
    starts: dict[str, str] = {}
    for message in data["value"]:
        if message.get("deletedDateTime") or message.get("messageType") != "message":
            continue
        content = BeautifulSoup((message.get("body") or {}).get("content") or "", "html.parser").get_text(" ", strip=True)
        if not content:
            continue
        user = ((message.get("from") or {}).get("user") or {}).get("displayName") or "unknown"
        when = message["createdDateTime"][:16].replace("T", " ")
        root = message.get("replyToId") or message["id"]
        threads.setdefault(root, []).append(f"{when} {user}: {content}")
        starts.setdefault(root, f"{when} {user}")
    return Parsed([Chunk(f"Thread started {starts[root]}", "\n".join(lines)) for root, lines in threads.items()])


SKIP_KEYS = {"attributes", "Id", "url", "totalSize", "done", "etag"}


def _flatten(value, prefix="") -> list[str]:
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if key in SKIP_KEYS or item in (None, "", [], {}):
                continue
            name = key.removesuffix("__c").removesuffix("__r")
            if isinstance(item, dict) and set(item) - SKIP_KEYS == {"Name"}:
                lines.append(f"{prefix}{name}: {item['Name']}")  # Account: {Name: X} -> Account: X
            elif key == "records":  # Salesforce sub-query wrapper, adds nothing to the name
                lines.extend(_flatten(item, prefix))
            else:
                lines.extend(_flatten(item, f"{prefix}{name}."))
        return lines
    if isinstance(value, list):
        return [line for item in value for line in _flatten(item, prefix)]
    return [f"{prefix.rstrip('.')}: {value}"]


# ---------- CSV ----------

def parse_csv(path: Path) -> Parsed:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t")
    rows = list(csv.DictReader(io.StringIO(raw), dialect=dialect))
    if not rows:
        return Parsed([])
    key = next(iter(rows[0]))
    chunks = [
        Chunk(f"{key} {row[key]}", "; ".join(f"{k}: {v}" for k, v in row.items() if k and v and v.strip()))
        for row in rows
    ]
    return Parsed(chunks)
