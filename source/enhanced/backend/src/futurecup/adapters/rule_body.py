"""Conservative Markdown presentation normalization, never semantic similarity proof."""
from __future__ import annotations

from decimal import Decimal
import hashlib
import html
import re

VERSION = "markdown-rule-v1"
# Use an explicit suffix boundary for ASCII; Chinese labels can continue after a colon.
REFERENCE = re.compile(r"^(?:\d+[.、)\s]*)?(?:参考文献|参考资料|引用|引文|来源|知识片段|检索结果|原文|任务输入|用户输入|提示词|反馈|上一轮产物|错误示例|反例|(?:sources?|references?|citations?|prompt|feedback|task input|previous artifact)(?:\b|$))", re.I)
SOURCE_COLUMN = re.compile(r"原文|引文|引用|来源|source|quote|citation|reference", re.I)
NEGATIVE = re.compile(r"无需|无须|不必|不需要|不得|禁止|不允许|不能|不应|不采用|不遵循|不执行|\b(?:not|never|prohibited|disallowed)\b|\bno need\b", re.I)
MODAL = re.compile(r"必须|应当|不得|禁止|不允许|允许|可以|不能|无需|无须|不必|不需要|不应|须|\b(?:must|shall|required|should|allowed?|may|not|never|prohibited)\b", re.I)
# Chinese units do not have an ASCII word boundary.
QUANTITY = re.compile(r"(\d+(?:\.\d+)?)\s*(milliseconds?\b|seconds?\b|minutes?\b|hours?\b|days?\b|毫秒|秒钟|分钟|小时|秒|天)", re.I)
RELATION = re.compile(r"不得超过|不超过|不少于|至少|至多|最多|最少|小于|大于|等于|>=|<=|≥|≤|>|<|=")
CLAUSE = re.compile(r"(?:\.(?!\d)|[。！？!?；;，,\n])")


def canonical(text: str) -> str:
    """Collapse presentation whitespace, but never concatenate separated ASCII words/digits."""
    def join(match):
        left = text[match.start()-1] if match.start() else ""
        right = text[match.end()] if match.end() < len(text) else ""
        return " " if left.isascii() and left.isalnum() and right.isascii() and right.isalnum() else ""
    return re.sub(r"\s+", join, text).strip()


def inline(raw: str) -> tuple[str, list[str]]:
    steps = []
    text = raw
    # Images/links are not permission to fetch anything. Only visible link labels survive.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    if text != raw:
        steps.append("visible_link_label_only")
    for pattern in (r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1",
                    r"(?<![\w*])\*(?!\*)(?=\S)(.+?)(?<=\S)\*(?![\w*])",
                    r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)"):
        before = text
        text = re.sub(pattern, lambda m: m.group(2) if m.lastindex == 2 else m.group(1), text)
        if text != before:
            steps.append("paired_emphasis")
    # Inline code remains visible; fenced/indented code is excluded at block level.
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    return html.unescape(text), steps


def extract(value: object) -> dict:
    """A bounded, explicit Markdown subset. Unsupported/raw HTML is conservatively excluded."""
    if not isinstance(value, dict):
        value = {}
    if isinstance(value.get("markdown"), str) and value["markdown"].strip():
        fields = [("markdown", value["markdown"])]
    else:
        # Legacy concise workflows deliver their body through items, never arbitrary metadata.
        fields = [(f"items.{i}", s) for i, s in enumerate(value.get("items", [])) if isinstance(s, str)]
    fragments, excluded = [], []
    for field, raw in fields:
        lines = raw.splitlines(keepends=True)
        offsets, cursor = [], 0
        for line in lines:
            offsets.append(cursor)
            cursor += len(line)
        fence, comment, html_block, reference_level = None, False, [], None
        reference_paragraph = False
        pending = []
        section = ""

        def add(start, end, text, kind="paragraph", extra=None):
            rendered, steps = inline(text)
            if not rendered.strip():
                return
            source = raw[start:end]
            fragments.append({"field": field, "start": start, "end": end,
                "line_start": raw.count("\n", 0, start)+1,
                "line_end": raw.count("\n", 0, max(start, end-1))+1,
                "raw": source, "rendered": rendered, "normalized": canonical(rendered),
                "kind": kind, "steps": steps + (extra or []), "section": section})

        def flush():
            if pending:
                start, end = pending[0][0], pending[-1][1]
                add(start, end, "\n".join(s for _, _, s in pending))
                pending.clear()

        i = 0
        while i < len(lines):
            line = lines[i].rstrip("\r\n")
            start, end = offsets[i], offsets[i]+len(lines[i])
            stripped = line.strip()
            heading = re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            reason = None
            if fence:
                if re.fullmatch(r" {0,3}"+re.escape(fence[0])+r"{"+str(fence[1])+r",}\s*", line):
                    fence = None
                reason = "fenced_code"
            elif marker:
                fence = (marker.group(1)[0], len(marker.group(1)))
                reason = "fenced_code"
            elif comment or "<!--" in line:
                # Multiple comment delimiters on one line must not expose a following hidden block.
                for token in re.findall(r"<!--|-->",line):
                    comment = token == "<!--"
                reason = "html_comment"
            elif html_block or re.search(r"<[A-Za-z!/][^>]*>", html.unescape(line)):
                for token in re.findall(r"</?[A-Za-z][^>]*>",line):
                    tag_match=re.match(r"</?([A-Za-z][\w:-]*)",token)
                    tag=tag_match.group(1).lower()
                    if token.startswith("</"):
                        if html_block and html_block[-1]==tag:html_block.pop()
                    elif tag not in {"br","hr","img","input","meta","link"} and not token.endswith("/>"):
                        html_block.append(tag)
                reason = "raw_html"
            elif any(MODAL.search(q) for q in re.findall(r'“([^”]+)”|‘([^’]+)’|"([^"\n]+)"',line) for q in q if q):
                reason = "quoted_normative_source_not_body_assertion"
            elif re.match(r"^(?: {4}|\t)", line):
                reason = "indented_code_or_unsupported_nesting"
            elif stripped.startswith(">") or re.match(r"^\s*\[[^\]]+\]:", line):
                reason = "quotation_or_reference_definition"
            elif "~~" in line or re.search(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f]", line):
                reason = "struck_or_hidden_or_ambiguous_text"
            if heading:
                reference_paragraph = False
                flush()
                level = len(heading.group(1))
                if reference_level is not None and level <= reference_level:
                    reference_level = None
                title, _ = inline(heading.group(2))
                section = title
                if REFERENCE.match(title):
                    reference_level = level
                reason = reason or "heading"
            if reference_level is not None:
                reason = reason or "reference_section"
            if stripped.startswith(">"):
                reference_paragraph = True
            visible, _ = inline(stripped)
            if not stripped:
                reference_paragraph = False
            if not heading and REFERENCE.match(visible.strip("[]")):
                reference_paragraph = True
            if reference_paragraph:
                reason = reason or "reference_or_input_paragraph"
            if reason:
                flush()
                excluded.append({"field": field, "start": start, "end": end, "reason": reason})
                i += 1
                continue
            if not stripped:
                flush()
                i += 1
                continue
            # A header + separator identifies a real table; never join arbitrary pipes.
            if "|" in line and i+1 < len(lines) and re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*", lines[i+1].strip()):
                flush()
                headers = [s.strip() for s in line.strip().strip("|").split("|")]
                quoted = {j for j, h in enumerate(headers) if SOURCE_COLUMN.search(h)}
                i += 2
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    row = lines[i].rstrip("\r\n")
                    cells = [s.strip() for s in row.strip().strip("|").split("|")]
                    a, b = offsets[i], offsets[i]+len(lines[i])
                    label, _ = inline(cells[0]) if cells else ("", [])
                    for j, cell in enumerate(cells):
                        unsafe = re.search(r"<[^>]*>|~~|[\u200b-\u200f\u202a-\u202e\u2060-\u206f]",html.unescape(cell))
                        if j in quoted or unsafe:
                            excluded.append({"field":field,"start":a,"end":b,"column":j,"reason":"source_quote_column"})
                            continue
                        add(a, b, cell, "table_cell")
                        if j and 0 not in quoted and 1 <= len(label) <= 48 and not re.search(r"<[^>]*>|~~|[\u200b-\u200f\u202a-\u202e\u2060-\u206f]",html.unescape(label)):
                            add(a, b, label+"："+cell, "table_subject", ["explicit_table_subject_association"])
                    i += 1
                continue
            if "|" in line:
                flush()
                excluded.append({"field":field,"start":start,"end":end,"reason":"unsupported_table_or_pipe"})
                i += 1
                continue
            bullet = re.match(r"^ {0,3}(?:[-+*]|\d+[.)])\s+(.+)$", line)
            if bullet:
                flush()
                pending.append((start, end, bullet.group(1)))
            else:
                pending.append((start, end, line))
            i += 1
        flush()
    return {"version": VERSION, "fragments": fragments, "excluded": excluded,
            "body_fields": [f for f, _ in fields], "body_contract": "markdown_else_legacy_items; no summary/citations/input/feedback"}


def quantities(text: str) -> list[tuple[Decimal, str, str]]:
    result = []
    for match in QUANTITY.finditer(text):
        unit = match.group(2).lower().rstrip("s")
        unit = {"分钟":"minute","秒":"second","秒钟":"second","毫秒":"millisecond","小时":"hour","天":"day"}.get(unit, unit)
        prefix = text[max(0, match.start()-12):match.start()]
        relations = RELATION.findall(prefix)
        relation = relations[-1] if relations else "="
        relation = {"至少":">=","不少于":">=","最少":">=","≥":">=","至多":"<=","最多":"<=",
                    "不超过":"<=","不得超过":"<=","≤":"<=","大于":">","小于":"<","等于":"="}.get(relation, relation)
        result.append((Decimal(match.group(1)), unit, relation))
    return result


def action(clause: str) -> str:
    found = list(MODAL.finditer(clause))
    text = clause[found[-1].end():] if found else clause
    return QUANTITY.sub("", text).strip()


def action_overlap(expected: str, actual: str) -> bool:
    a, b = action(expected), action(actual)
    chinese = re.findall(r"[\u4e00-\u9fff]+", a)
    if any(run[i:i+4] in b for run in chinese for i in range(max(0, len(run)-3))):
        return True
    words = re.findall(r"[a-z]+", a.lower())
    return any(len(word)>=4 and re.search(r"\b"+re.escape(word)+r"\b", b, re.I) for word in words)


def excerpt(fragment: dict) -> dict:
    return {**{k:fragment[k] for k in ("field","start","end","line_start","line_end","kind","steps")},
            "raw": fragment["raw"][:1200], "rendered": fragment["rendered"][:1200],
            "normalized": fragment["normalized"][:1200],
            "raw_sha256": hashlib.sha256(fragment["raw"].encode()).hexdigest(),
            "truncated": len(fragment["raw"])>1200}


def claims(text: str) -> list[str]:
    result=[]
    for part in CLAUSE.split(canonical(text)):
        part=part.strip()
        if not part:continue
        modals=list(MODAL.finditer(part))
        cuts=[0]+[m.start() for m in modals[1:]]+[len(part)]
        result.extend(part[a:b] for a,b in zip(cuts,cuts[1:]) if part[a:b])
    return result


def inspect_rule(rule: str, query: str, body: dict, tokenize) -> dict:
    expected = canonical(rule)
    matches = [f for f in body["fragments"] if expected in f["normalized"]]
    clauses = claims(rule)
    query_tokens = set(tokenize(query))
    conflicts, candidates = [], []
    for fragment in body["fragments"]:
        for actual in claims(fragment["rendered"]):
            if not actual:
                continue
            overlap = len(query_tokens & set(tokenize(actual))) / max(1, len(query_tokens))
            for clause in clauses:
                related_action = action_overlap(clause, actual)
                if not related_action and overlap < .5:
                    continue
                if related_action:
                    candidates.append(fragment)
                required_q, actual_q = quantities(clause), quantities(actual)
                kind = None
                if required_q and actual_q:
                    if any(q not in required_q for q in actual_q):
                        kind = "NUMERIC_UNIT_CONFLICT"
                if related_action and MODAL.search(clause) and MODAL.search(actual):
                    if bool(NEGATIVE.search(clause)) != bool(NEGATIVE.search(actual)):
                        kind = kind or "RULE_CONFLICT"
                if related_action:
                    for condition in re.findall(r"未([\u4e00-\u9fff]{1,6}?)(?:时|前|后)", clause):
                        if "已"+condition in actual:
                            kind = kind or "RULE_CONFLICT"
                if kind:
                    conflicts.append({"type":kind,"expected_clause":clause,"actual_clause":actual[:600],
                                      "location":excerpt(fragment)})
    # Dedupe table cell/subject projections and repeated witnesses without losing source positions.
    unique = {}
    for conflict in conflicts:
        key = (conflict["type"], conflict["actual_clause"], conflict["location"]["field"], conflict["location"]["start"])
        unique.setdefault(key, conflict)
    conflicts = list(unique.values())
    if conflicts:
        status, kind = "POSSIBLE_CONFLICT", conflicts[0]["type"]
    elif matches:
        status, kind = "RULE_REFLECTED", "RULE_REFLECTION"
    elif candidates:
        status, kind = "SEMANTIC_UNCERTAIN", "SEMANTIC_UNCERTAIN"
    else:
        status, kind = "SOURCE_NOT_REFLECTED", "RULE_NOT_REFLECTED"
    return {"status":status,"validation_type":kind,"normalization_version":VERSION,
            "expected_raw":rule,"expected_normalized":expected,
            "body_matches":[excerpt(f) for f in matches[:2]],
            "related_body":[excerpt(f) for f in candidates[:2]] if not matches else [],
            "conflicts":conflicts[:8],
            "excluded_regions":body["excluded"][:24] if not matches else [],
            "difference": "protected value/unit/relation/polarity differs" if conflicts else
                "exact visible-body rule found" if matches else "no exact visible-body rule; paraphrase is not proven",
            "repair_hint": "Repair only this topic in the deliverable body; preserve the complete source rule, numbers, units, conditions and negation. Citations alone are insufficient."}
