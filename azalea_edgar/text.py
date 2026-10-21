"""Conservative HTML/iXBRL to text conversion; preserve visible numbers and tables."""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

from .client import SecError


CLEANER_VERSION = "htmlparser-v1"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "param", "source", "track", "wbr"}
DROP = {"script", "style", "head", "noscript", "svg", "ix:header", "ix:hidden"}
BLOCK = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
         "li", "ul", "ol", "table", "tr", "blockquote", "pre", "hr", "br"}


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def hidden(self):
        return bool(self.stack and self.stack[-1][1])

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        style = re.sub(r"\s+", "", attributes.get("style") or "").lower()
        suppressed = (self.hidden() or tag in DROP or "hidden" in attributes
                      or (attributes.get("aria-hidden") or "").lower() == "true"
                      or "display:none" in style or "visibility:hidden" in style)
        if not suppressed:
            if tag in BLOCK:
                self.parts.append("\n")
            elif tag in {"td", "th"}:
                self.parts.append("\t")
        if tag not in VOID:
            self.stack.append((tag, suppressed))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        was_hidden = self.hidden()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if not was_hidden and tag in BLOCK:
            self.parts.append("\n")
        elif not was_hidden and tag in {"td", "th"}:
            self.parts.append("\t")

    def handle_data(self, data):
        if not self.hidden():
            self.parts.append(data)


def clean_document(body: bytes, content_type: str, filename: str) -> tuple[str, dict]:
    if filename.lower().endswith((".pdf", ".xml", ".zip")) or body.startswith(b"%PDF"):
        raise SecError("Unsupported primary document format")
    charset = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    encodings = [charset.group(1)] if charset else []
    encodings.extend(["utf-8-sig", "cp1252"])
    encoding = None
    for candidate in dict.fromkeys(encodings):
        try:
            decoded = body.decode(candidate, errors="strict")
            encoding = candidate
            break
        except (UnicodeError, LookupError):
            continue
    if encoding is None:
        raise SecError("Unable to decode filing without replacement characters")
    is_html = filename.lower().endswith((".htm", ".html", ".xhtml")) or "html" in content_type
    if is_html:
        parser = VisibleText()
        parser.feed(decoded)
        parser.close()
        decoded = "".join(parser.parts)
    decoded = unicodedata.normalize("NFKC", decoded).replace("\x00", "")
    decoded = decoded.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in decoded.split("\n"):
        cells = [re.sub(r"[^\S\n\t]+", " ", cell).strip() for cell in line.split("\t")]
        line = "\t".join(cell for cell in cells if cell)
        if line or (lines and lines[-1]):
            lines.append(line)
    text = "\n".join(lines).strip() + "\n"
    warnings = ["cp1252 decoding fallback; review encoding"] if encoding == "cp1252" else []
    return text, {"encoding": encoding, "characters": len(text),
                  "words": len(text.split()), "cleaner_version": CLEANER_VERSION,
                  "warnings": warnings}


def text_issues(text: str, form: str, min_chars: int = 1000) -> list[str]:
    issues = []
    if len(text.strip()) < min_chars:
        issues.append(f"cleaned text shorter than {min_chars} characters")
    if "\ufffd" in text or "\x00" in text:
        issues.append("replacement or NUL character present")
    if re.search(r"<(?:script|style|ix:hidden)\b", text, re.I):
        issues.append("unremoved markup")
    expected = form.replace("/A", "")
    if not re.search(r"\b" + re.escape(expected).replace(r"\-", r"[-\s]?") + r"\b", text, re.I):
        issues.append(f"missing form marker {expected}")
    if not re.search(r"\b(?:item\s+\d|financial statements|balance sheets)\b", text, re.I):
        issues.append("missing basic filing content marker")
    return issues
