from arxivist.html_parser import parse_html
from arxivist.metadata import PaperMetadata
from arxivist.models import Author

FIXTURE = """
<html><body>
<article class="ltx_document">
<h1 class="ltx_title ltx_title_document">A Test Paper</h1>
<div class="ltx_abstract"><h6 class="ltx_title">Abstract</h6>
<p class="ltx_p">We test things.</p></div>
<section id="S1" class="ltx_section">
  <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
  <div id="S1.p1" class="ltx_para"><p class="ltx_p">Deep learning
  <cite class="ltx_cite">[<a href="#bib.bib1" class="ltx_ref">1</a>]</cite> uses
  <math alttext="\\alpha_{i}" class="ltx_Math"><semantics><mi>x</mi></semantics></math>
  and <span class="ltx_text ltx_font_bold">bold claims</span>.</p></div>
  <table id="S1.E1" class="ltx_equation ltx_eqn_table">
    <tr><td class="ltx_eqn_cell">
      <math alttext="y=f(x)" display="block"><semantics><mi>y</mi></semantics></math>
    </td><td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(1)</span></td></tr>
  </table>
  <section id="S1.SS1" class="ltx_subsection">
    <h3 class="ltx_title ltx_title_subsection"><span class="ltx_tag ltx_tag_subsection">1.1 </span>Background</h3>
    <div class="ltx_para"><p class="ltx_p">Prior work exists.<span class="ltx_note ltx_role_footnote"><sup class="ltx_note_mark">1</sup><span class="ltx_note_outer"><span class="ltx_note_content"><sup class="ltx_note_mark">1</sup><span class="ltx_tag ltx_tag_note">1</span>A footnote.</span></span></span></p></div>
    <ul class="ltx_itemize">
      <li class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span><div class="ltx_para"><p class="ltx_p">first item</p></div></li>
      <li class="ltx_item"><span class="ltx_tag ltx_tag_item">•</span><div class="ltx_para"><p class="ltx_p">second item</p></div></li>
    </ul>
  </section>
</section>
<section id="S2" class="ltx_section">
  <h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">2 </span>Results</h2>
  <figure id="S2.F1" class="ltx_figure"><img src="x.png"/>
    <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_figure">Figure 1: </span>Accuracy over time.</figcaption>
  </figure>
  <figure id="S2.T1" class="ltx_table">
    <table class="ltx_tabular"><tr class="ltx_tr"><td class="ltx_td">model</td><td class="ltx_td">acc</td></tr>
    <tr class="ltx_tr"><td class="ltx_td">ours</td><td class="ltx_td">0.9</td></tr></table>
    <figcaption class="ltx_caption"><span class="ltx_tag ltx_tag_table">Table 1: </span>Main results.</figcaption>
  </figure>
</section>
<section id="A1" class="ltx_appendix">
  <h2 class="ltx_title ltx_title_appendix"><span class="ltx_tag ltx_tag_appendix">Appendix A </span>Proofs</h2>
  <div class="ltx_para"><p class="ltx_p">Trivial.</p></div>
</section>
<section class="ltx_bibliography" id="bib">
  <h2 class="ltx_title ltx_title_bibliography">References</h2>
  <ul class="ltx_biblist">
    <li id="bib.bib1" class="ltx_bibitem"><span class="ltx_tag ltx_tag_bibitem">[1]</span>
      <span class="ltx_bibblock">A. Author. Great paper. 2020.</span></li>
  </ul>
</section>
</article></body></html>
"""

META = PaperMetadata(
    arxiv_id="9999.99999",
    version=1,
    title="A Test Paper",
    abstract="We test things.",
    authors=[Author(name="A. Author")],
    categories=["cs.LG"],
)


def _paper():
    return parse_html(FIXTURE, META)


def test_section_tree():
    paper = _paper()
    assert [s.title for s in paper.sections] == ["Introduction", "Results", "Proofs"]
    assert [s.number for s in paper.sections] == ["1", "2", "Appendix A"]
    intro = paper.sections[0]
    assert [c.title for c in intro.children] == ["Background"]
    assert intro.children[0].number == "1.1"


def test_math_recovered_as_latex():
    intro = _paper().sections[0]
    assert "$\\alpha_{i}$" in intro.content
    assert "$$y=f(x)$$  (1)" in intro.content


def test_inline_formatting_and_citations():
    intro = _paper().sections[0]
    assert "**bold claims**" in intro.content
    assert "[1]" in intro.content  # citation text preserved
    background = intro.children[0]
    assert "(footnote: A footnote.)" in background.content
    assert "- first item" in background.content
    assert "- second item" in background.content


def test_figures_and_tables():
    paper = _paper()
    labels = [f.label for f in paper.figures]
    assert "Figure 1" in labels and "Table 1" in labels
    results = paper.get_section("Results")
    assert "**Figure 1:** Accuracy over time." in results.content
    assert "| model | acc |" in results.content
    assert "| ours | 0.9 |" in results.content


def test_references():
    paper = _paper()
    assert len(paper.references) == 1
    ref = paper.references[0]
    assert ref.label == "1"
    assert "Great paper" in ref.text


def test_lookup_and_roundtrip():
    paper = _paper()
    assert paper.get_section("1.1").title == "Background"
    assert paper.get_section("background").title == "Background"
    assert paper.get_section("nope") is None
    from arxivist.models import Paper

    clone = Paper.from_dict(paper.to_dict())
    assert clone.to_markdown() == paper.to_markdown()
    assert "## 1 Introduction" in clone.to_markdown()
    assert paper.outline().count("Background") == 1
