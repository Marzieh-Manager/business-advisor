"""Credibility audit for a generated weekly report.

The report is candid about named employees and the owner acts on it, so every
specific it contains has to trace back to something he actually said or to
`business_context/business.md`. This runs against the raw model output in
`data/responses/` before a report is sent.

    python tools/audit_report.py data/responses/<stamp>-<week>.json

Exit code is non-zero when anything needs a human to clear it, so this can gate
delivery later.

What it cannot do: judge whether the advice is *good*. It checks that the
report is not making things up. Read the report yourself as well.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from pomeai.config import Config
from pomeai.context import load_business_context
from pomeai.notes import load_week
from pomeai.weeks import week_containing

from datetime import datetime, time, timezone

# Below this, an `evidence` quote is not close enough to his actual words to be
# presented as one. Transcripts contain disfluencies the model tends to tidy up,
# so this is deliberately not 1.0.
EVIDENCE_FLOOR = 0.85

# Phrasings that dress a claim as research. The prompt forbids these outright;
# any hit is a prompt failure, not a style question.
# Phrasings that dress a claim up as research. The prompt forbids these
# outright, so any hit is a prompt failure rather than a style question.
# These are patterns, not substrings: bare "study" matches "case study", a
# marketing deliverable, and a false positive that common teaches the reader
# to ignore the audit.
CITATION_PATTERNS = (
    r"\b\d+(?:\.\d+)?\s?(?:%|per ?cent)",
    r"\b(?:research|studies|data|evidence|surveys?|numbers)\s+"
    r"(?:show|shows|shows that|suggest|suggests|found|find|finds|indicate|indicates)\b",
    r"\baccording to\b",
    r"\bmeta[- ]analys[ie]s\b",
    r"\bresearchers?\b",
    r"\b(?:a|one|the|another)\s+(?:\d{4}\s+)?study\b",
    r"\bstudy\s+(?:found|showed|shows|of|by)\b",
    r"\bstudies\b(?!\s+of\s+your)",
    r"\b(?:harvard|mckinsey|gartner|deloitte|bain|forrester)\b",
    # "on average" is left out on purpose: applied to his own order volumes
    # it is legitimate, and a FAIL that fires on true statements gets ignored.
    r"\bstatistically\b",
)

# Words that start a sentence or head a list, so a capital tells you nothing
# about whether they name a real entity.
STOPWORDS = {
    "the", "this", "that", "these", "those", "you", "your", "he", "she", "it",
    "and", "but", "or", "if", "when", "what", "why", "how", "who", "which",
    "a", "an", "in", "on", "at", "to", "for", "of", "with", "from", "by",
    "do", "does", "did", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "not", "no", "yes", "one", "two", "three", "four",
    "give", "get", "go", "put", "run", "send", "book", "make", "take", "stop",
    "start", "keep", "hire", "ask", "tell", "say", "use", "week", "next",
    "now", "then", "there", "here", "so", "as", "into", "out", "up", "down",
    "why", "because", "same", "best", "more", "most", "less", "own", "each",
    "every", "any", "all", "both", "either", "neither", "first", "second",
    "park", "keep", "harbourline", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
}

MONTHS = ("january february march april may june july august september "
          "october november december").split()


def normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace - for fuzzy matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", text.lower())).strip()


def walk_strings(node, path=""):
    """Yield (path, string) for every string in the report tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk_strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def best_match(fragment: str, corpus: str) -> float:
    """Highest similarity of `fragment` against any window of `corpus`."""
    if not fragment:
        return 1.0
    step = max(1, len(fragment) // 8)
    return max(
        (
            difflib.SequenceMatcher(None, fragment, corpus[i:i + len(fragment)]).ratio()
            for i in range(0, max(1, len(corpus) - len(fragment)), step)
        ),
        default=0.0,
    )


class Audit:
    def __init__(self) -> None:
        self.findings: list[tuple[str, str, str]] = []  # (severity, where, detail)

    def flag(self, severity: str, where: str, detail: str) -> None:
        self.findings.append((severity, where, detail))

    def report(self) -> int:
        if not self.findings:
            print("\n[ OK ] nothing to clear - every specific traces back to a source.")
            return 0
        order = {"FAIL": 0, "CHECK": 1}
        self.findings.sort(key=lambda f: order.get(f[0], 2))
        fails = sum(1 for f in self.findings if f[0] == "FAIL")
        checks = len(self.findings) - fails
        print(f"\n{fails} failure(s), {checks} item(s) to clear by hand\n")
        for severity, where, detail in self.findings:
            print(f"  [{severity:5}] {where}")
            print(f"           {detail}")
        return 1


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2

    response_path = Path(argv[1])
    if not response_path.exists():
        print(f"no such response file: {response_path}")
        return 2

    config = Config.load()
    sections = json.loads(response_path.read_text(encoding="utf-8"))

    # The filename carries the week's Monday, e.g. 20260821T150617Z-2026-08-17.json
    match = re.search(r"(\d{4}-\d{2}-\d{2})\.json$", response_path.name)
    if not match:
        print(f"cannot tell which week {response_path.name} covers")
        return 2
    monday = datetime.strptime(match.group(1), "%Y-%m-%d").date()
    week = week_containing(
        config, datetime.combine(monday, time(12, 0), tzinfo=timezone.utc)
    )
    notes = load_week(config, week)

    transcripts = " ".join((n.transcript or "") for n in notes)
    context = load_business_context(config)
    haystack = normalise(transcripts + " " + context)
    spoken = normalise(transcripts)

    print(f"auditing {response_path.name}")
    print(f"  week:    {week.label}")
    print(f"  notes:   {len(notes)}")
    print(f"  sources: {len(transcripts):,} chars of transcript, "
          f"{len(context):,} chars of business context")

    # Every date the sources actually contain, normalised to "14 august" and
    # "august 14" so a report date can be matched as a unit rather than by its
    # parts. "22nd of October" and "October 22" both reduce to the same pair.
    source_dates = set()
    for day, month in re.findall(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + "|".join(MONTHS) + r")\b",
        haystack,
    ):
        source_dates.add(f"{int(day)} {month}")
    for month, day in re.findall(
        r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", haystack
    ):
        source_dates.add(f"{month} {int(day)}")

    audit = Audit()

    # --- 1. Evidence must be close to his actual words ---------------------
    print("\n1. evidence fidelity")
    evidence = sections.get("biggest_issue", {}).get("evidence", []) or []
    for i, quote in enumerate(evidence):
        # Quotes elide with "..."; each side has to match on its own.
        fragments = [f for f in quote.split("...") if len(f.strip()) > 12] or [quote]
        score = min(best_match(normalise(f), spoken) for f in fragments)
        verdict = "ok" if score >= EVIDENCE_FLOOR else "TOO LOOSE"
        print(f"   {score:.2f}  {verdict:9}  {quote[:60]}...")
        if score < EVIDENCE_FLOOR:
            audit.flag(
                "FAIL", f"biggest_issue.evidence[{i}]",
                f"only {score:.0%} similar to anything he said: {quote[:90]}",
            )

    # --- 2. No invented research ------------------------------------------
    print("\n2. fabricated-evidence markers")
    hits = 0
    for path, text in walk_strings(sections):
        low = text.lower()
        for pattern in CITATION_PATTERNS:
            found = re.search(pattern, low)
            if not found:
                continue
            hits += 1
            at = max(0, found.start() - 45)
            audit.flag(
                "FAIL", path,
                f"reads as a citation ({found.group()!r}): ...{text[at:at + 130]}...",
            )
    print(f"   {hits} hit(s)" + ("" if hits else "  ok - no statistic or study cited"))

    # --- 3. Specifics that trace to no source ------------------------------
    print("\n3. unsourced specifics")
    seen: set[str] = set()
    for path, text in walk_strings(sections):
        # Dates first: this is where the previous report slipped.
        for date in re.findall(
            r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:" + "|".join(MONTHS) + r")\b"
            r"|\b(?:" + "|".join(MONTHS) + r")\s+\d{1,2}\b",
            text, re.IGNORECASE,
        ):
            key = f"date::{normalise(date)}"
            if key in seen:
                continue
            seen.add(key)
            # Checking the day and the month independently is useless - "14" is
            # in "since 2014" and "august" is everywhere. The date has to appear
            # as a unit, in either order, allowing for "22nd of October".
            day = str(int(re.search(r"\d{1,2}", date).group()))
            month = next(m for m in MONTHS if m in date.lower())
            if not (f"{day} {month}" in source_dates or f"{month} {day}" in source_dates):
                audit.flag(
                    "CHECK", path,
                    f"date {date!r} appears in neither his notes nor the business "
                    "context - if you derived it, the report must say so",
                )

        # Proper nouns: people, companies, products he would have to have named.
        for word in re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zA-Z]{2,})\b", text):
            low = word.lower()
            if low in STOPWORDS or low in MONTHS or low in seen:
                continue
            seen.add(low)
            if low not in haystack:
                audit.flag(
                    "CHECK", path,
                    f"proper noun {word!r} traces to no source - confirm he said it, "
                    "or that it is a method name rather than a claim about his business",
                )
    unsourced = sum(1 for f in audit.findings if f[0] == "CHECK")
    print(f"   {unsourced} item(s) to clear")

    # --- 4. One named method per section at most ---------------------------
    # A heuristic, and a weak one - it counts phrases that look like method
    # names, so it will both miss real ones and catch ordinary wording. It is
    # here to notice a report drifting towards a lecture, not to certify one.
    # Whether the naming actually helps is a judgement made by reading it.
    print("\n4. named-method density (heuristic - read the report too)")
    for section in ("biggest_issue", "biggest_opportunity", "leadership_lesson"):
        body = " ".join(t for p, t in walk_strings(sections.get(section, {})))
        # A named method is normally introduced in italics or quoted in the text.
        named = re.findall(r"\*([a-z][a-z \-]{4,40})\*|\b([a-z]+ (?:test|method|rule|principle|analysis))\b", body)
        terms = {n[0] or n[1] for n in named}
        print(f"   {section:22} {len(terms)} named  {sorted(terms) if terms else ''}")
        if len(terms) > 1:
            audit.flag(
                "CHECK", section,
                f"{len(terms)} named methods in one section ({sorted(terms)}) - "
                "the cap is one, or it reads like a textbook",
            )

    # --- 5. The section that has failed before -----------------------------
    print("\n5. section completeness")
    avoiding = sections.get("avoiding", {})
    pattern = [p for p in (avoiding.get("pattern") or []) if p.strip()]
    chips = [avoiding.get(k, "") for k in ("avoided", "symptom_one", "symptom_two")]
    chips = [c for c in chips if c and c.strip()] or [
        c for c in (avoiding.get("connections") or []) if c and c.strip()
    ]
    print(f"   avoiding.pattern  {len(pattern)} paragraph(s)")
    print(f"   avoiding chips    {len(chips)}")
    if not pattern:
        audit.flag("FAIL", "avoiding.pattern", "empty - the section renders as a bare heading")
    if len(chips) != 3:
        audit.flag("FAIL", "avoiding", f"{len(chips)} chips, expected 3")

    return audit.report()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
