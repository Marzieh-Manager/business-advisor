# Report template — slot contract

The contract between the **advisor** (which produces structured JSON) and
`weekly-report.html`. Section order is fixed by §5 of the build brief and must not
change.

`weekly-report.html` **is the approved design**, with the dynamic parts replaced by
Jinja2 tags. Its `<head>`, CSS and markup structure match the original — verified by
rendering it with the mock's own content and diffing against the pristine original. Do not
redesign it.

## Rendering notes

- The document is English, LTR (`<html lang="en" dir="ltr">`). The owner decided in
  August 2026 that the whole product — bot replies, transcripts and report — is English.
- Numbers arrive as ordinary Western digits, already formatted as strings. The template does
  no number conversion and no rounding.
- Autoescaping is on — transcripts are owner-supplied content and may contain `<` or `&`.
- `advisor.validate()` rejects a report whose prose arrays came back empty
  (`biggest_issue.explanation` and `.evidence`, `biggest_opportunity.explanation`,
  `avoiding.pattern`, `leadership_lesson.lesson`). The design has no graceful empty
  state for these — a heading over blank space is worse than a failed run.
- Every model response is written to `data/responses/` **before** it is parsed or
  validated, so a rejected report is still readable and diagnosable.
- Fonts are a system stack (Inter and friends). Nothing is loaded from a CDN, so the report
  looks correct offline and in an email client.

## Top-level variables

| Variable | Type | Notes |
|---|---|---|
| `week_label` | str | e.g. `10th–16th August 2026`, or `31st August – 6th September 2026` across a month boundary. Produced by `Week.label`, not by the model. Appears in the masthead and the footer. |
| `recipient` | str | e.g. `Business owner` |
| `entry_count` | int | Drives the masthead line, the intake badge **and** the footer — one value, so they cannot disagree. |

> The approved mock had its header say `7 voice notes` while its intake badge said `9` and it
> listed 9 notes. Driving all three from `entry_count` fixes that.

## Sections, in order

### 1. `intake_summary` — What you told me this week
One row per note, each with a weekday chip. Flat, not grouped.
```
intake_summary: [ { weekday: str, summary: str } ]
```
Renders a "no notes this week" row when empty.

### 2. `business_health` — Overall business health
The vitals strip. **Exactly 4 vitals** — the CSS grid is 4 columns wide.
```
business_health: {
  status: str,                    # short label beside the pulse dot, e.g. "Needs attention"
  vitals: [ { label: str,         # e.g. "Customer satisfaction"
              value: str,         # e.g. "At risk"
              direction: "up" | "down" | "flat",   # sets the trend colour
              trend: str } ]      # e.g. "↓ 2 complaints" - include the arrow glyph
}
```

### 3. `biggest_issue` — Biggest issue
**`evidence` must be quoted from the owner's own notes**, in his words. This is what makes
the report credible rather than generic. The CSS adds the quote marks, so do not include
them in the string.
```
biggest_issue: {
  title: str,
  explanation: [str],             # one string per paragraph
  evidence: [str],
  recommendation: str             # specific to this business, not generic advice
}
```

### 4. `biggest_opportunity` — Best opportunity
Same shape **minus `evidence`** — the approved design has no evidence block here.
```
biggest_opportunity: { title: str, explanation: [str], recommendation: str }
```

### 5. `avoiding` — What you may be avoiding
**The highest-value section.** Connect separate notes into one underlying issue the owner
keeps circling but not acting on. Not a summary — a connection he has not made himself.
```
avoiding: {
  title: str,
  pattern: [str],                 # one string per paragraph - never empty
  avoided: str,                   # the thing itself, 3-6 words
  symptom_one: str,               # first visible consequence, 3-6 words
  symptom_two: str                # second visible consequence, 3-6 words
}
```
`advisor._normalise` folds the three named fields into
`avoiding.connections = [avoided, symptom_one, symptom_two]`, which is what the template
loops over. They render as `avoided ← symptom + symptom`, so order matters.

> **Why named fields and not an array.** The gateway does not enforce `minItems`, and the
> model twice returned `avoiding` with a title but `pattern: []` and `connections: []` —
> hollowing out the section the prompt calls the most important one. Three required *strings*
> cannot be satisfied the way an empty array can.

### 6. `leadership_lesson` — Leadership lesson · for next time
Turn a people moment into a forward-looking lesson plus a concrete prep step. A system, not
sympathy. This section is about his own growth as a decision-maker.
```
leadership_lesson: { title: str, lesson: [str], prep_for_next_time: str }
```

### 7. `marketing_meeting` — For your next marketing meeting
Curate the week's marketing ideas. **The value is in filtering**, so `park` verdicts are a
success, not a gap. Three verdicts, and the advisor should emit them in this order:

| `verdict` | Badge | Meaning |
|---|---|---|
| `now` | Now · urgent | Ties to a live business reality; lead the meeting with it |
| `keep` | Keep | Worth doing, low risk, not urgent |
| `park` | Park for now | Say no — not connected to any clear outcome |

```
marketing_meeting: {
  intro: str,
  ideas: [ { verdict: "now"|"keep"|"park", idea: str, detail: str, why: str } ]
}
```
`why` should carry its own lead-in, e.g. `Why lead with it: …` or `Why park it: …`.

> This section assumes the business has someone who owns marketing. If it does not, record
> that as a `[TODO]` in `business_context/business.md`.

### 8. `next_priorities` — Next week · just 3 priorities
**Exactly three**, ordered. `validate()` rejects advisor output with a different count.
```
next_priorities: [ { rank: int,       # 1 / 2 / 3
                     priority: str, detail: str,
                     why_now: str } ] # carries its own lead-in, e.g. "Why first: …"
```

### 9. `action_plan` — Day by day
One row per day.
```
action_plan: [ { day: str, task: str } ]
```

### 10. `follow_up` — Follow-up from last week
Did last week's decisions actually happen? In Phase 1 this comes from passing the previous
report file into the prompt — there is no cross-week memory store yet (that is Phase 2).

```
follow_up: {
  available: bool,                # false -> renders "this is the first report"
  entries: [ { status: "done" | "partial" | "not_started",
               subject: str,      # rendered bold, e.g. "Late-paying cafes"
               note: str } ]
}
```

| `status` | Mark | Colour |
|---|---|---|
| `done` | ✓ | green |
| `partial` | ◐ | ochre |
| `not_started` | ✕ | clay |

> Keyed **`entries`, not `items`** — `follow_up.items` resolves to the `dict.items` method in
> Jinja2 instead of the data, which fails at render time.
