# REPORTING — the content standard for experiment reports

This is the *semantic* standard: what a report must communicate, in what order,
and for whom. `reportly lint` enforces the mechanical skeleton derived from it
(see the last section); everything else in this file is a rubric for the author
(usually an agent) and for the reviewer. Where the two disagree, this file wins.

## The reader

The report is written for the PI, not a general audience. Assume full
familiarity with the project's high-level ideas, motivation, and prior results.
Consequences:

- **No background.** Don't explain what the technique is, why the area matters,
  or restate the spec's motivation. Zero sentences of throat-clearing.
- **Low-level over high-level.** The informative bits are the numbers, the
  knobs, and the surprises — not the framing.
- **Optimize for review cost.** The reader should know what happened in
  30 seconds (the answer sheet) and be able to audit any single claim in a
  couple of minutes (answer → evidence → what-was-run).

## A report is an answer sheet

The organizing device: **questions fixed before the results existed, answered
up front.**

- Questions come from the **experiment spec**, not from the writeup session.
  Fixing them at design time is what stops post-hoc narrative laundering.
- The spec carries the universal baseline questions (below) plus its own
  specific ones. The **author prunes by judgment** — include the ones that make
  sense for this experiment, drop the rest silently.
- A question the experiment *set out* to answer may not be silently dropped:
  keep it and answer "**Not answered** — \<why\>".
- A question invented at writeup time (because the data surprised you) is
  welcome, but mark it *(post-hoc)*.

### Universal baseline questions

- **Headline.** What is the effect of X on Y — direction and magnitude?
- **Reality.** Is it real — does it survive seeds, controls, baselines, and the
  most obvious confound?
- **Variation.** How does it vary along the axes the spec called out (scale,
  depth, dataset, …)?
- **Failure.** What broke or didn't work, and does it threaten the headline?
- **Decision.** What does this change about what we do next?

### Answer format

Each item is one paragraph — the bold question, then the answer directly
beneath it with **no blank line between them** (that's what makes it lintable):

```markdown
**Q1. Does installing one fact make the model fabricate collateral claims?**
Yes — +32pp fabrication on neighboring claims vs control (Fig 1). High confidence.
```

An answer is 1–3 lines: direction + magnitude with the actual number, a pointer
to the evidence (Fig 1 / Table 2 / a link), and a confidence tag
(high / medium / low).

## Ordering: evidence before interpretation

Result first, then enough experimental detail to understand what was done, then
interpretation — the reverse of paper order.

1. **H1** — the finding as a sentence, not a topic label.
2. **Questions** — the answer sheet. This replaces the TL;DR.
3. **Evidence** — figures and number tables, each tagged with the question it
   answers (Q1, Q2, …) and captioned with the claim it establishes. Minimal
   prose; numbers in tables, not sentences.
4. **What was run** — only what's needed to interpret the evidence: models,
   data, seeds, the knobs that matter, the controls. Not a methods narrative.
5. **Interpretation** — only now: takeaways, caveats, failed controls,
   surprises, what would change the conclusion, and honest deviations
   ("we set out to answer X, we actually answered X′").
6. **Next steps** — concrete follow-ups.
7. **Reproduce** + provenance footer — exact commands; branch, model, artifacts.

## What not to write

- Motivation or background the reader already has.
- A methods narrative (the chronology of what you tried) — that belongs in the
  lab log, not the report.
- Numbers buried in prose — put them in tables.
- A null or negative result dressed up as "mixed": answer the question with
  "no" and set `vibe:` accordingly.
- Interpretation claims with no evidence anchor upstream.

## What lint checks vs. what review checks

**Mechanical — enforced by `reportly lint`:** the skeleton exists and is
honest. A Questions section with at least one `**Qn. …?**` item; every question
answered in place (error if not — "Not answered — \<why\>" counts); answers
point at evidence (warning if not); the answer sheet precedes Setup (warning on
paper-order); figures exist on disk; Reproduce carries fenced commands; a
provenance footer names branch/model/artifacts/code.

**Semantic — this rubric, checked by the reviewer or a judging agent:** answers
actually answer their questions; the evidence supports the stated answers;
confidence tags are calibrated; no padding; interpretation doesn't smuggle in
claims the evidence section never established.
