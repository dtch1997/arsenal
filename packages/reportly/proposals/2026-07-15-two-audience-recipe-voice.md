# Proposal: two-audience reports + method-as-recipe voice

Status: **proposed** (researcher-originated, from the risk-averse-ai
full-rerun report, 2026-07-15). Worked example:
`risk-averse-ai/experiments/constitution-distill/reports/2026-07-15-full-rerun.md`.

## The problem

REPORTING.md currently assumes one reader: the PI, fully in-context, wanting
the answer sheet and the knobs. Real reports turned out to have **two
audiences with conflicting needs**:

- **Internal** (the PI and the repo's agents): wants *all* implementation
  detail — exact configs, checkpoint pointers, harness mechanics, reproduce
  commands, continuity tables against prior runs — so the work can be picked
  up with minimal friction.
- **External** (a colleague, a public write-up reader): wants a concise
  document focused on the salient result, and is actively harmed by the
  plumbing — every internal PR number and config key costs attention and
  dates the document.

Serving the external reader today means a separate scrubbing pass that
forks the document. Forks drift.

## Convention 1 — two audiences, one file

The **rendered** document is the external write-up. Implementation detail
lives in **`<!-- internal: … -->` HTML comment blocks placed beside the
section they support** — visible to anyone reading the markdown source
(agents included), invisible in any HTML render (cowrite, GitHub, Pages).

- Salient science outside; plumbing inside. Rough test: would an external
  reader act on this line? If not, it goes in a comment block.
- Typical internal-block content: harness/infra mechanics, exact
  training/eval knobs beyond what interpretation needs, reproduce commands,
  spend accounting, continuity tables versus prior internal runs,
  internal-only next steps.
- The first block in the file states the convention itself, so an agent
  opening the source learns the rule before editing.
- Nothing about the *content* standard changes for the internal layer: the
  answer sheet, evidence anchors, and honesty rules apply to the whole
  file. The external rendering is a projection, not a different report.
- Bonus: when a report graduates to a public write-up, the external version
  already exists — render it; no scrubbing pass.

## Convention 2 — method as a first-person recipe

Write the method so the reader can *imagine performing it*: first-person,
concrete, stepwise — "We write the risk attitude down as ten first-person
sentences … We render the constitution as the system prompt of a second
copy of Qwen3-8B … We distill, never showing the student a gamble." Quote
real inputs where they help the reader see the step (an actual trait
sentence beats a description of trait sentences).

This does **not** repeal "no methods narrative." The banned thing is the
*chronology of attempts* (what you tried, in order, with dead ends). A
recipe is the minimal idealized procedure — what someone would do to get
the result, not what happened. One is a lab log; the other is the fastest
path into the reader's head.

## Changes if accepted

1. **REPORTING.md** gains two sections: *The two audiences* (convention 1,
   with the rough test) and *Method as recipe* (convention 2, with the
   narrative-vs-recipe distinction). "The reader" section is reframed: the
   *internal layer* is written for the PI; the *rendered projection* is
   written for an external colleague.
2. **`reportly lint`** learns to look inside `<!-- internal: -->` blocks:
   the Reproduce-fenced-commands and provenance-footer checks must accept
   those sections living in comment blocks (today they would fail on a
   compliant two-audience report). New warning: fenced shell commands or
   config-key runs in *rendered* prose ("plumbing outside").
3. **`reportly scaffold`** emits the convention-stating comment block at
   the top of new reports and an empty `<!-- internal: Reproduce — … -->`
   stub.

## Open questions

- Does the answer sheet still open the *rendered* document, or may the
  external projection open motivation-led (precedent: sci-mt's
  `reportly.toml` relaxation for the risk-averse report)? Suggest: keep the
  answer sheet; motivation-led remains a per-repo config.
- Should `reportly build` grow a `--internal` flag that renders comment
  blocks as visible admonitions, for internal HTML review?
