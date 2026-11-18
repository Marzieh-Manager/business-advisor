"""The weekly advisor.

One Claude call turns a week of the owner's raw voice notes into the structured
report defined in templates/SLOTS.md. There is deliberately no multi-agent
architecture: sales, marketing, leadership and ops are *lenses* inside this one
prompt, not separate agents.

The assembled prompt is always written to `data/prompts/` before the call, so
any report can be traced back to exactly what produced it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pomeai.config import Config
from pomeai.context import load_business_context, load_sector_context
from pomeai.notes import VoiceNote
from pomeai.weeks import Week

log = logging.getLogger(__name__)


class AdvisorError(RuntimeError):
    """The weekly report could not be produced."""


@dataclass
class AdvisorResult:
    """A generated report plus the trace needed to audit it."""

    sections: dict  # matches templates/SLOTS.md
    prompt_path: Path
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


# --- The advisor prompt ------------------------------------------------------

SYSTEM_PROMPT = """\
You are the personal advisor to a business owner - something like an
experienced chief of staff.

Through the week, whenever something was on his mind, he sent you a voice note.
These are not reports; they are raw, unordered thinking. Your job is to put them
side by side and tell him something he has not seen himself.

# Business context

Everything you say must be tied to this specific business. Generic management
advice is worthless - he can get that from any book.

<business_context>
{business_context}
</business_context>

{sector_context}

# Rules

- **Evidence in his own words.** Whenever you make a claim, quote from that
  week's notes. Quotes should be close to what he actually said, not your
  paraphrase. **Do not put quotation marks around `evidence` items** - the
  report design adds them.
- **Invent nothing about his business.** Never make up a number, a name, or an
  event he did not mention. If something is not in the notes or the context,
  do not assume it.
- **Do not state a date, name or number as his unless he said it.** If you work
  one out - the weekday he was referring to, which person he meant, a total you
  added up - say so and tell him to confirm it: "the Friday before that note -
  check the date". Anything you tell him to put in writing in front of an
  employee, a supplier or a partner must be either his own words or visibly
  marked as your inference. He will act on this report; a derived fact that
  reads like a given one is how he ends up damaging a real relationship on your
  say-so.
- **But do bring outside knowledge - that is different from inventing.** He
  wants this system to grow as an operator; he expects to learn something he
  did not know. In the **recommendations**, where it genuinely helps, say how
  comparable businesses (distribution, e-commerce, fulfilment, small teams
  scaling up) have solved this, or which established approach fits here.
  - **Name the method when it has a name.** He cannot look up something he has
    no name for, and he asked for this system so he could grow. Give the term,
    one line on why it works, then translate it straight into what he does on
    Monday morning.
  - **At most one named method per section**, and only where you are confident.
    A report naming five frameworks reads like a textbook and he stops reading.
    The test is still whether he can act on it tomorrow morning.
  - **Never invent a citation, a statistic or a study.** If you know the method
    but not the evidence behind it, name the method and stop there. Do not
    reach for a percentage, a study name or a researcher to sound authoritative.
    One fabricated number discredits every true thing in the report.
  - Always tie it to his specific situation. Textbook advice is worthless.
  - If you are not confident something is true, leave it out. Wrong knowledge
    is worse than no knowledge.
- **Direct tone.** No pleasantries, no preamble, no sympathy. Get to the point.
- **When something is not worth doing, say no.** Your value is in filtering,
  not in validating.
- Write everything in English.

# Sections

1. `intake_summary` - one line per note, with its weekday. Short, in his own
   register.

2. `business_health` - `status` is a two or three word label (e.g. "Needs
   attention"). Give **exactly four** vitals. `direction` is one of `up` /
   `down` / `flat`. `trend` is a short phrase with an arrow (e.g. "down 2 staff").

3. `biggest_issue` - the most important problem of the week. `evidence` quotes
   him directly.

4. `biggest_opportunity` - the best opening this week. No evidence block.

5. `avoiding` - **the most important section of the report.** Here you do what a
   summariser cannot: connect two or three apparently unrelated notes and show
   they are actually one problem - something he keeps circling but never acts
   on. This is not a summary of the week; it is a connection he has not made.
   - `pattern` is where the section earns its place: two or three paragraphs
     naming the avoided thing, showing how the separate notes are the same
     problem, and saying what it is costing him. **A title on its own is not an
     answer.** Never leave this empty.
   - `avoided` is the thing itself, in three to six words.
   - `symptom_one` and `symptom_two` are the two visible consequences, each in
     three to six words. All three render as chips, so keep them short.

6. `leadership_lesson` - this is **his own growth**, not a status report. Take
   something involving people or decisions and turn it into a transferable
   lesson: what it shows about how he is managing, and what to do differently
   next time. `prep_for_next_time` must be one concrete, executable step, not
   an aphorism. Not sympathy - a system. If experienced operators have a known
   approach for this situation, say it.

7. `marketing_meeting` - filter the week's marketing ideas.
   `verdict`: `now` (tied to a live business reality, lead the meeting with it),
   `keep` (worthwhile, low risk, not urgent), `park` (no - not connected to any
   clear outcome). Order them that way. If every idea is weak, park them all.
   `why` carries its own lead-in: "Why it makes the meeting: ..." or
   "Why park it: ...".

8. `next_priorities` - **exactly three**, in order of importance. `why_now`
   carries its own lead-in: "Why first: ..." / "Why it matters: ..." /
   "Why now: ...".
   `topic` is a short lowercase slug naming the thread this belongs to, e.g.
   `green-bean-reorder`, `backup-roaster`, `office-bundle`, `late-payers`. **If the history
   below already uses a slug for this thread, reuse it exactly.** A new slug for
   an old problem hides the fact that it is an old problem.

9. `action_plan` - day by day, one concrete, doable task per day.

10. `follow_up` - **one entry for every open decision listed in the history
    below**, using its exact `decision_id`. `status` is `done`, `partial`,
    `not_started`, or `dropped` if it is no longer worth doing - and say so in
    `note` if you are dropping it. Judge from this week's notes: if he never
    mentioned it, that is `not_started`, not an excuse to leave it out.
    If there is no history, set `available` to `false` and leave `entries`
    empty.

Return JSON only, matching the schema. Write nothing outside the JSON.
"""

# Category knowledge, kept separate from the business context so the two can
# never be confused. The rules matter more than the content: this is a lens,
# and the owner is the authority on his own business.
SECTOR_BLOCK = """\
# How businesses like his work

Background on the category, so your advice sounds like someone who knows this
industry rather than someone reacting to one week of notes.

**This is a lens, not evidence.** Never quote it, never present anything in it
as something he said, and never take a number from it. Where it disagrees with
the business context or with his own words, he is right - ask him about the
difference instead of asserting the pattern.

<sector_context>
{sector}
</sector_context>
"""


USER_PROMPT = """\
# This week's voice notes ({week_label})

{notes_block}

{history_block}{previous_report_block}
Write this week's analysis."""

NO_PREVIOUS_REPORT = (
    "# Last week's report\n\nNo previous report exists - this is the first one.\n"
)

# The advisor's memory. Rendered as text with the counts already worked out, so
# the model reads a fact rather than deriving one it could get wrong.
HISTORY_BLOCK = """\
# What you have already told him to do

These are your own past recommendations and where they stand. Use them:

- Anything still open belongs in `follow_up`, by `decision_id`.
- If a topic has been running for weeks without moving, **say so plainly** and
  treat that as more important than anything new this week. Repetition without
  action is the pattern worth naming.
- Do not re-issue an open decision as if it were new. If it still matters,
  either escalate it or say why it has not happened.

<history>
{history}
</history>

"""

PREVIOUS_REPORT_BLOCK = """\
# Last week's report

For the follow-up section, read this and check which decisions were acted on:

<previous_report>
{previous_report}
</previous_report>
"""


def _schema() -> dict:
    """JSON Schema for the report. Mirrors templates/SLOTS.md."""
    text = {"type": "string"}
    paragraphs = {"type": "array", "items": {"type": "string"}}

    def obj(properties: dict) -> dict:
        return {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        }

    return obj({
        "intake_summary": {
            "type": "array",
            "items": obj({"weekday": text, "summary": text}),
        },
        "business_health": obj({
            "status": text,
            "vitals": {
                "type": "array",
                "minItems": 4,
                "maxItems": 4,
                "items": obj({
                    "label": text,
                    "value": text,
                    "direction": {"type": "string", "enum": ["up", "down", "flat"]},
                    "trend": text,
                }),
            },
        }),
        "biggest_issue": obj({
            "title": text,
            "explanation": paragraphs,
            "evidence": {"type": "array", "items": text},
            "recommendation": text,
        }),
        "biggest_opportunity": obj({
            "title": text, "explanation": paragraphs, "recommendation": text,
        }),
        # The chips are three named strings, not an array: the gateway does not
        # enforce `minItems`, and an array is easy for the model to satisfy with
        # `[]`. `_normalise` folds them back into the list the template loops
        # over, so templates/SLOTS.md is unchanged from the renderer's side.
        "avoiding": obj({
            "title": text,
            "pattern": paragraphs,
            "avoided": text,
            "symptom_one": text,
            "symptom_two": text,
        }),
        "leadership_lesson": obj({
            "title": text, "lesson": paragraphs, "prep_for_next_time": text,
        }),
        "marketing_meeting": obj({
            "intro": text,
            "ideas": {
                "type": "array",
                "items": obj({
                    "verdict": {"type": "string", "enum": ["now", "keep", "park"]},
                    "idea": text, "detail": text, "why": text,
                }),
            },
        }),
        # `topic` is not rendered - it is the thread key the decisions store
        # groups by, so "the fourth week running" can be counted rather than
        # guessed. Reusing an existing slug is what makes recurrence visible.
        "next_priorities": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": obj({"rank": {"type": "integer"}, "topic": text,
                          "priority": text, "detail": text, "why_now": text}),
        },
        "action_plan": {"type": "array", "items": obj({"day": text, "task": text})},
        "follow_up": obj({
            "available": {"type": "boolean"},
            "entries": {
                "type": "array",
                "items": obj({
                    "decision_id": text,
                    "status": {"type": "string",
                               "enum": ["done", "partial", "not_started", "dropped"]},
                    "subject": text, "note": text,
                }),
            },
        }),
    })


def build_prompt(
    config: Config,
    week: Week,
    notes: list[VoiceNote],
    previous_report: str | None = None,
    history: str | None = None,
) -> tuple[str, str]:
    """Assemble the (system, user) prompt pair.

    `history` is the advisor's own memory - past recommendations and how long
    they have been open - rendered by `decisions.history_block`. It is left out
    entirely rather than sent empty, so a first report is not told twice that
    there is nothing behind it.
    """
    lines = []
    for note in notes:
        local = note.local_time(config.report_timezone)
        day = note.weekday(config.report_timezone)
        transcript = (note.transcript or "").strip() or "[not transcribed]"
        lines.append(f"- **{day}** ({local:%H:%M}): {transcript}")

    sector = load_sector_context(config)
    system = SYSTEM_PROMPT.format(
        business_context=load_business_context(config),
        sector_context=SECTOR_BLOCK.format(sector=sector) if sector else "",
    )
    user = USER_PROMPT.format(
        week_label=week.label,
        notes_block="\n".join(lines),
        history_block=HISTORY_BLOCK.format(history=history) if history else "",
        previous_report_block=(
            PREVIOUS_REPORT_BLOCK.format(previous_report=previous_report)
            if previous_report
            else NO_PREVIOUS_REPORT
        ),
    )
    return system, user


def log_prompt(config: Config, week: Week, system: str, user: str) -> Path:
    """Write the assembled prompt to disk.

    Section 6 of the brief requires being able to see exactly what produced any
    report - so this happens before the API call, not after a successful one.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.prompts_dir / f"{stamp}-{week.local_start}.md"
    path.write_text(
        f"# Prompt for {week.label}\n\n"
        f"Model: {config.anthropic_model}\n"
        f"Effort: {config.advisor_effort}\n"
        f"Generated: {stamp}\n\n"
        f"## System\n\n{system}\n\n## User\n\n{user}\n",
        encoding="utf-8",
    )
    return path


def log_response(config: Config, week: Week, text: str) -> Path:
    """Write the model's raw output to disk, before it is parsed or validated.

    A validation failure used to discard output that had already been paid for
    and could not then be diagnosed. Saving first means a rejected report is
    still readable, and the prompt/response pair can be compared.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.responses_dir / f"{stamp}-{week.local_start}.json"
    path.write_text(text, encoding="utf-8")
    return path


def _extract_json(text: str) -> dict:
    """Parse the model's JSON, tolerating a markdown fence if one slips in."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 3:
            cleaned = parts[1]
            if "\n" in cleaned:
                first, rest = cleaned.split("\n", 1)
                if first.strip().lower() in ("json", ""):
                    cleaned = rest
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AdvisorError(
            f"the advisor did not return valid JSON ({exc}). "
            "The assembled prompt was logged - inspect it and the raw output."
        ) from exc


def _normalise(sections: dict) -> dict:
    """Reshape model output into what the template expects.

    Only `avoiding` needs it: the chips arrive as three named fields and the
    template renders `connections` as an ordered list of three.
    """
    avoiding = sections.get("avoiding")
    if isinstance(avoiding, dict) and "connections" not in avoiding:
        avoiding["connections"] = [
            avoiding.get("avoided", ""),
            avoiding.get("symptom_one", ""),
            avoiding.get("symptom_two", ""),
        ]
    return sections


# Sections whose prose the design has no graceful empty state for. An empty one
# renders as a heading over blank space, which is worse than a failed run.
_REQUIRED_PROSE = (
    ("biggest_issue", "explanation"),
    ("biggest_issue", "evidence"),
    ("biggest_opportunity", "explanation"),
    ("avoiding", "pattern"),
    ("leadership_lesson", "lesson"),
)


def validate(sections: dict) -> None:
    """Check the contract the template depends on."""
    missing = [
        key for key in (
            "intake_summary", "business_health", "biggest_issue",
            "biggest_opportunity", "avoiding", "leadership_lesson",
            "marketing_meeting", "next_priorities", "action_plan", "follow_up",
        ) if key not in sections
    ]
    if missing:
        raise AdvisorError(f"advisor output is missing section(s): {', '.join(missing)}")

    priorities = sections["next_priorities"]
    if len(priorities) != 3:
        # The brief is explicit: exactly three, ordered.
        raise AdvisorError(f"expected exactly 3 priorities, got {len(priorities)}")

    vitals = sections["business_health"].get("vitals", [])
    if len(vitals) != 4:
        # The vitals strip is a 4-column CSS grid.
        raise AdvisorError(f"expected exactly 4 vitals, got {len(vitals)}")

    chips = sections["avoiding"].get("connections", [])
    if len(chips) != 3 or not all(str(c).strip() for c in chips):
        raise AdvisorError(
            f"expected 3 non-empty connection chips, got {chips!r}"
        )

    for section, field in _REQUIRED_PROSE:
        body = sections.get(section, {}).get(field) or []
        if not [line for line in body if str(line).strip()]:
            raise AdvisorError(
                f"{section}.{field} came back empty - the section would render "
                "as a heading over blank space"
            )


def build_client(config: Config):
    """Anthropic client, optionally routed through a gateway.

    OpenRouter exposes a native Anthropic Messages endpoint, so the official SDK
    works against it with a base-URL override - no OpenAI-compatible shim. A
    gateway authenticates with a bearer token rather than `x-api-key`.
    """
    from anthropic import Anthropic

    key = config.anthropic_api_key
    if not key:
        raise AdvisorError(
            "ANTHROPIC_API_KEY is not set (an OpenRouter key works too, with "
            "ANTHROPIC_BASE_URL=https://openrouter.ai/api)."
        )

    if config.anthropic_base_url:
        return Anthropic(auth_token=key, base_url=config.anthropic_base_url)
    return Anthropic(api_key=key)


def generate(
    config: Config,
    week: Week,
    notes: list[VoiceNote],
    previous_report: str | None = None,
    client=None,
    history: str | None = None,
) -> AdvisorResult:
    """Produce the weekly report sections for one week."""
    if not notes:
        raise AdvisorError(
            f"no voice notes in {week.label} - there is nothing to analyse. "
            "A report invented from an empty week would be worse than no report."
        )

    system, user = build_prompt(config, week, notes, previous_report, history)
    prompt_path = log_prompt(config, week, system, user)
    log.info("assembled prompt logged to %s", prompt_path)

    engine = client or build_client(config)

    try:
        # Streaming because thinking and output share max_tokens, and a large
        # non-streaming request risks an HTTP timeout.
        with engine.messages.stream(
            model=config.anthropic_model,
            max_tokens=config.advisor_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": config.advisor_effort,
                "format": {"type": "json_schema", "schema": _schema()},
            },
        ) as stream:
            message = stream.get_final_message()
    except Exception as exc:
        raise AdvisorError(f"{config.anthropic_model} failed: {exc}") from exc

    if message.stop_reason == "refusal":
        raise AdvisorError("the model declined this request")
    if message.stop_reason == "max_tokens":
        raise AdvisorError(
            f"output hit max_tokens ({config.advisor_max_tokens}). "
            "Raise ADVISOR_MAX_TOKENS or lower ADVISOR_EFFORT."
        )

    text = next((b.text for b in message.content if b.type == "text"), "")
    response_path = log_response(config, week, text)
    log.info("raw response logged to %s", response_path)

    sections = _normalise(_extract_json(text))
    try:
        validate(sections)
    except AdvisorError as exc:
        raise AdvisorError(f"{exc} - raw output kept at {response_path}") from exc

    usage = getattr(message, "usage", None)
    return AdvisorResult(
        sections=sections,
        prompt_path=prompt_path,
        model=getattr(message, "model", config.anthropic_model),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
