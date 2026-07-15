# Proposal: two-audience reports + method-as-recipe voice

Status: **accepted — implemented in this PR** (reportly 0.3.0: comment-aware
core + layer-split lint + `plumbing_outside`, build strips comments /
`--internal` admonition mode, two-audience scaffold, REPORTING.md sections;
both open questions resolved as suggested below). Researcher-originated, from
the risk-averse-ai full-rerun report, 2026-07-15. Worked example:
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
- **Caveat — comments hide, they don't protect.** The internal layer is
  invisible in renders but sits in plaintext in the markdown source. The
  no-scrubbing guarantee holds only when what goes public is the rendered
  HTML; if the *source* ever becomes public (a repo flipped public, Pages
  serving raw markdown), every internal block — spend accounting,
  checkpoint pointers, internal PR numbers — leaks. This convention assumes
  the current setup (private repos, gated Pages). To make the guarantee
  mechanical rather than conventional, `reportly build` should strip
  comment blocks from any external output path.

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
2. **`reportly` core + lint become comment-aware.** Today the parser
   (`core.py`) blanks fenced code but is blind to HTML comments, so
   content inside `<!-- internal: -->` blocks is parsed as if it were
   rendered. The failure mode is therefore not that a two-audience report
   fails lint — it mostly passes, *silently*: a `## Reproduce` heading, a
   figure, or a provenance footer inside a comment block satisfies its
   check even though the rendered projection is missing that element. The
   real work item is to teach `core.py` to segment comment regions (the
   same way it blanks fences), then assign each rule a layer: whole-file
   checks (Reproduce commands, provenance footer — comment placement is
   compliant) versus rendered-projection checks (answer sheet, evidence
   anchors, result figures — must hold with comments stripped). New
   warning on the rendered layer: "plumbing outside" for Reproduce-style
   multi-command fenced blocks or config-key runs in rendered prose —
   scoped narrowly, since convention 2 encourages quoting the one command
   or input that defines the method.
3. **`reportly scaffold`** emits the convention-stating comment block at
   the top of new reports and an empty `<!-- internal: Reproduce — … -->`
   stub.
4. **`reportly build`** strips comment blocks from external output (don't
   rely on the HTML renderer happening to hide them), making the
   no-scrubbing guarantee mechanical.

## Open questions

- Does the answer sheet still open the *rendered* document, or may the
  external projection open motivation-led (precedent: sci-mt's
  `reportly.toml` relaxation for the risk-averse report)? Suggest: keep the
  answer sheet; motivation-led remains a per-repo config.
- Should `reportly build` grow a `--internal` flag that renders comment
  blocks as visible admonitions, for internal HTML review?
