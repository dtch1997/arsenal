# REPORTING — the content standard for experiment reports

This is the *semantic* standard: what a report must communicate, in what order,
and for whom. `reportly lint` enforces the mechanical skeleton derived from it
(see the last section); everything else in this file is a rubric for the author
(usually an agent) and for the reviewer. Where the two disagree, this file wins.

## The reader

A report has two readers, served by two layers of the same file (next
section). The **internal layer** — the full markdown source — is written for
the PI and the repo's agents. Assume full familiarity with the project's
high-level ideas, motivation, and prior results. The **rendered projection**
is written for an external colleague: fully informed on the science, but with
no stake in this repo's plumbing. Consequences for both:

- **No background.** Don't explain what the technique is, why the area matters,
  or restate the spec's motivation. Zero sentences of throat-clearing.
- **Low-level over high-level.** The informative bits are the numbers, the
  knobs, and the surprises — not the framing.
- **Optimize for review cost.** The reader should know what happened in
  30 seconds (the answer sheet) and be able to audit any single claim in a
  couple of minutes (answer → evidence → what-was-run).

## The two audiences

The two readers want conflicting things: the PI wants *all* implementation
detail — exact configs, checkpoint pointers, reproduce commands, continuity
tables against prior runs — while the external reader is actively harmed by
it: every internal PR number and config key costs attention and dates the
document. Serving both with separate documents means a scrubbing pass that
forks the report, and forks drift. Instead: **one file, two layers.**

The **rendered** document is the external write-up. Implementation detail
lives in **`<!-- internal: … -->` HTML comment blocks placed beside the
section they support** — visible to anyone reading the markdown source
(agents included), invisible in any render.

- **Rough test:** would an external reader act on this line? If not, it goes
  in a comment block.
- Typical internal-block content: harness/infra mechanics, exact training and
  eval knobs beyond what interpretation needs, reproduce commands, spend
  accounting, continuity tables versus prior internal runs, internal-only
  next steps.
- The first block in the file states the convention itself, so an agent
  opening the source learns the rule before editing (`reportly new` emits it).
- Nothing about the content standard changes for the internal layer: the
  answer sheet, evidence anchors, and honesty rules apply to the whole file.
  The external rendering is a projection, not a different report.
- When a report graduates to a public write-up, the external version already
  exists — `reportly build` renders it with the internal layer stripped.
- **Comments hide, they don't protect.** The internal layer sits in plaintext
  in the markdown source; the no-scrubbing guarantee holds only when what
  goes public is the built HTML. If the *source* ever becomes public (a repo
  flipped public, Pages serving raw markdown), every internal block leaks.
  Publish `reportly build` output, never the source.

## Method as recipe

Write the method so the reader can *imagine performing it*: first-person,
concrete, stepwise — "We write the risk attitude down as ten first-person
sentences … We render the constitution as the system prompt of a second copy
of Qwen3-8B … We distill, never showing the student a gamble." Quote real
inputs where they help the reader see the step: an actual trait sentence
beats a description of trait sentences.

This does **not** repeal "no methods narrative" (below). The banned thing is
the *chronology of attempts* — what you tried, in order, with dead ends. A
recipe is the minimal idealized procedure: what someone would do to get the
result, not what happened. One is a lab log; the other is the fastest path
into the reader's head.

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
7. **Reproduce** + provenance footer — exact commands; branch, model,
   artifacts. Plumbing: conventionally an `<!-- internal: Reproduce -->` block.

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

Lint is layer-aware: *rendered* checks (the H1, the answer sheet, evidence
figures, ordering, most required sections) must hold with comment blocks
stripped — content hiding in a comment can't satisfy them. *Whole-file*
checks (Reproduce commands, the provenance footer, and the section kinds in
`internal_ok`, default `reproduce`/`appendix`) accept either layer, since
plumbing conventionally lives in `<!-- internal: -->` blocks. Once a report
uses internal blocks, a multi-command fence in rendered prose draws a
"plumbing outside" warning — quoting the one command that defines the method
stays fine.

**Semantic — this rubric, checked by the reviewer or a judging agent:** answers
actually answer their questions; the evidence supports the stated answers;
confidence tags are calibrated; no padding; interpretation doesn't smuggle in
claims the evidence section never established.
