"""Conservative extraction of named competitors from explicit disclosure lists.

This baseline does not infer peers from every company mention. Each candidate
keeps the original statement, its business context and offsets in the saved text.
"""

from __future__ import annotations

import re
from collections import defaultdict


HEADINGS = {"competition", "competitors", "competitive environment", "competitive landscape"}
EXPLICIT = re.compile(
    r"\b(?:our\s+(?:(?:main|principal|primary|major|key)\s+)?competitors\s+"
    r"(?:(?:in|for)\s+[^.;\n]{1,100}?\s+)?(?:include|are|comprise)|"
    r"we\s+compete\s+(?:directly\s+)?(?:with|against)|"
    r"we\s+(?:also\s+)?(?:face|experience)\s+(?:(?:intense|significant|strong)\s+)?competition\b"
    r"[^.;\n]{0,200}?\bfrom)\b", re.I)
LIST_MARKER = re.compile(r"\b(?:such as|namely|including|include|comprise|consist of)\b", re.I)
COMPETITION_FRAME = re.compile(r"\b(?:sources?\s+of\s+competition|our\s+(?:(?:main|principal|primary)\s+)?competitors)\b", re.I)
BUSINESS_LIST = re.compile(r"^[•*\-–]?\s*(?:companies|providers|manufacturers|firms)\s+(?:providing|offering|that\b)", re.I)
GENERIC_LIST = re.compile(
    r"^\s*(?:[\w'&-]+\s+){0,20}"
    r"(?:companies|providers|manufacturers|firms|businesses|competitors)\b"
    r"[^.!?;\n]{0,100}?\b(?:such as|including|namely)\b", re.I)
ROLE_CHANGE = re.compile(
    r"(?:[.;,]\s*|\b(?:while|but|and)\s+)?\b(?:our|the|these)\s+"
    r"(?:(?:main|major|key|principal)\s+)?"
    r"(?:customers?|suppliers?|partners?|clients?|employees?|shareholders?|investors?)\b", re.I)
LEGAL = re.compile(r"\b(?:incorporated|inc|corporation|corp|company|co|limited|ltd|llc|plc|se|sa|nv)\b", re.I)


def company_key(name):
    """Normalize legal spelling only; do not equate brands with parent issuers."""
    name = re.sub(r"\s*/[A-Z]{2,3}/\s*$", "", name, flags=re.I)
    name = name.replace("’", "'").replace("&", " and ").lower()
    name = LEGAL.sub(" ", name)
    return "".join(re.findall(r"[a-z0-9]+", name))


def _without_parentheses(text):
    depth, result = 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif not depth:
            result.append(char)
    return "".join(result)


def _names(text, directory_keys):
    # Parenthetical owners of a joint venture are not separately named rivals.
    text = _without_parentheses(text)
    text = re.split(r"\b(?:and\s+)?(?:other|established|various|numerous)\s+", text, maxsplit=1, flags=re.I)[0]
    # Keep a legal suffix attached in names such as "Example, Inc.".
    text = re.sub(r",(?=\s*(?:Inc\.?|Corp\.?|Corporation|Ltd\.?|LLC|Co\.?)\b)", "\x00", text)
    # Only the first sentence belongs to the explicit enumeration.
    for stop in re.finditer(r"\.(?=\s+[A-Z])", text):
        if not re.search(r"\b(?:Inc|Corp|Co|Ltd)\.$", text[:stop.end()], re.I):
            text = text[:stop.start()]
            break
    names = []
    for item in re.split(r"[,;]", text):
        item = re.sub(r"^\s*(?:and|or)\s+", "", item).strip(" .;:\t\n")
        # An exact directory spelling can contain "and" within a company name.
        parts = [item] if company_key(item) in directory_keys else re.split(r"\s+and\s+(?=[A-Z0-9])", item)
        for name in parts:
            name = re.sub(r"\s+", " ", name).replace("\x00", ",").strip(" .;:\t\n")
            registered_name = re.search(r"\b(?:Inc|Corporation|Corp|Ltd|LLC|plc)$", name, re.I)
            if ((not re.match(r"[A-Z0-9]", name) and not registered_name) or not 2 <= len(name) <= 100
                    or len(name.split()) > 12 or not re.search(r"[A-Za-z]", name)):
                continue
            # Generic descriptions are not individual company identities.
            if re.search(r"\b(?:providers|companies|markets|customers|suppliers|manufacturers|competitors|agencies)\b", name, re.I):
                continue
            if name not in names:
                names.append(name)
    return names


def _context(prefix):
    prefix = prefix.strip(" •\t\n:;,-")
    market = re.search(r"competition\s+in\s+(?:the\s+)?(.+?)\s+markets?\s+from\b", prefix, re.I)
    if market:
        return market[1][0].upper() + market[1][1:]
    category = re.search(r"competition\s+from\s+(.+?)\s+(?:such as|including|namely)$", prefix, re.I)
    if category:
        return category[1][0].upper() + category[1][1:]
    # These labels are shortened from source phrases, not an industry taxonomy.
    phrase = re.match(r"companies\s+(?:providing|offering)\s+(.+)", prefix, re.I)
    if phrase:
        prefix = phrase[1]
        prefix = re.split(r"\s+(?:to deliver|in the|for use|that |which )", prefix, maxsplit=1, flags=re.I)[0]
    if not prefix or len(prefix) > 105 or re.search(r"\b(?:our|we)\b", prefix, re.I):
        return "Named competition"
    return prefix[0].upper() + prefix[1:]


def _sentence_end(text):
    for stop in re.finditer(r"[.!?](?=\s+[A-Z]|\s*$)", text):
        after = text[stop.end():].lstrip()
        abbreviation = re.search(r"\b(?:Inc|Corp|Co|Ltd)\.$", text[:stop.end()], re.I)
        if abbreviation and after and not re.match(r"(?:Our|We|The|These|This|In|However|For|Moreover)\b", after):
            continue
        return stop.end()
    return len(text)


def extract_competitors(text, directory=(), issuer_name=""):
    identities = defaultdict(dict)
    for entry in directory:
        key = company_key(entry["name"])
        if not key:
            continue
        issuer = identities[key].setdefault(entry["cik"], {
            "cik": entry["cik"], "name": entry["name"], "tickers": []})
        if entry["ticker"] not in issuer["tickers"]:
            issuer["tickers"].append(entry["ticker"])

    candidates, by_name, groups, seen_statements = [], {}, [], set()
    active_section = None
    competition_frame = None
    section_count = 0
    for block in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        statement = block[0].strip()
        heading = statement.rstrip(":").lower()
        if heading in HEADINGS:
            active_section = statement.rstrip(":")
            competition_frame = None
            section_count += 1
            continue
        if (len(statement) < 100 and "\n" not in statement and
                not statement.startswith("•") and (statement.istitle() or statement.isupper())
                and statement not in {"Table of Contents"}):
            active_section = None
            competition_frame = None
        # A section heading alone does not establish the role of a company list.
        if active_section and statement.endswith(":") and COMPETITION_FRAME.search(statement):
            competition_frame = statement
            continue
        explicit = EXPLICIT.search(statement)
        if not active_section and not explicit:
            continue
        # Do not promote compensation or shareholder-return benchmarking peers.
        if re.search(r"\b(?:peer group|shareholder return|compensation|remuneration)\b", statement, re.I):
            continue
        if explicit and re.search(r"\b(?:not|never|no longer)\s+(?:directly\s+)?compete\b", statement, re.I):
            continue
        if re.search(r"\b(?:do not|does not|no longer)\s+(?:include|compete)\b", statement, re.I):
            continue
        if explicit:
            names = []
            # Try each direct predicate, within its own sentence. A vague earlier
            # assertion must not hide a later explicit list in the same paragraph.
            for explicit in EXPLICIT.finditer(statement):
                clause_end = explicit.start() + _sentence_end(statement[explicit.start():])
                start = explicit.end()
                generic = GENERIC_LIST.match(statement[start:clause_end])
                if generic:
                    start += generic.end()
                role_change = ROLE_CHANGE.search(statement, start, clause_end)
                end = role_change.start() if role_change else clause_end
                names = [name for name in _names(statement[start:end], identities)
                         if company_key(name) != company_key(issuer_name)]
                if names:
                    break
            if not names:
                continue
            relation_basis, relation_text = "direct_statement", explicit[0]
            evidence_start, evidence_end = explicit.start(), end
        else:
            if not competition_frame or not BUSINESS_LIST.match(statement):
                if statement not in {"Table of Contents"} and not statement.isdigit() and not statement.startswith("•"):
                    competition_frame = None
                continue
            marker = LIST_MARKER.search(statement)
            if not marker:
                continue
            start = marker.end()
            relation_basis, relation_text = "framed_competition_list", competition_frame
            role_change = ROLE_CHANGE.search(statement, start)
            end = role_change.start() if role_change else len(statement)
            names = [name for name in _names(statement[start:end], identities)
                     if company_key(name) != company_key(issuer_name)]
            evidence_start, evidence_end = 0, len(statement)
        if not names:
            continue
        if statement in seen_statements:
            continue
        seen_statements.add(statement)
        group_id = f"g{len(groups) + 1}"
        paragraph_start = block.start() + len(block[0]) - len(block[0].lstrip())
        quote_start = paragraph_start + evidence_start
        quote_end = paragraph_start + evidence_end
        group = {"id": group_id, "label": _context(statement[evidence_start:start]),
                 "section": active_section or "Explicit competition statement",
                 "relation_basis": relation_basis, "relation_text": relation_text,
                 "quote": text[quote_start:quote_end], "start": quote_start, "end": quote_end,
                 "start_utf16": len(text[:quote_start].encode("utf-16-le")) // 2,
                 "end_utf16": len(text[:quote_end].encode("utf-16-le")) // 2,
                 "candidate_ids": []}
        # Remove the enumeration marker from the business-context label.
        if not explicit:
            group["label"] = _context(statement[:marker.start()])
        for name in names:
            key = company_key(name)
            if key not in by_name:
                matches = list(identities.get(key, {}).values())
                candidate = {"id": f"c{len(candidates) + 1}", "name": name, "group_ids": [],
                             "identity": {"status": "directory_match" if len(matches) == 1 else
                                          "ambiguous" if matches else "unresolved", "matches": matches}}
                candidates.append(candidate)
                by_name[key] = candidate
            candidate = by_name[key]
            if group_id not in candidate["group_ids"]:
                candidate["group_ids"].append(group_id)
                group["candidate_ids"].append(candidate["id"])
        groups.append(group)
    return {"method": "Explicit competition lists · Rules v2",
            "status": "found" if candidates else "no_named_competitors" if section_count else "not_found",
            "candidates": candidates, "groups": groups,
            "summary": {"candidate_count": len(candidates), "context_count": len(groups),
                        "matched_count": sum(c["identity"]["status"] == "directory_match" for c in candidates)},
            "note": "Candidates named in this filing, not a complete or ranked peer set. Company names and business relationships reflect the filing date. Directory matches use name spelling; unmatched names need identity review."}
