import pytest

from arxivist.ids import ArxivId, InvalidArxivId, parse_arxiv_id


@pytest.mark.parametrize(
    "raw,expected_id,expected_version",
    [
        ("2401.12345", "2401.12345", None),
        ("2401.12345v2", "2401.12345", 2),
        ("1706.03762", "1706.03762", None),
        ("2104.5678", "2104.5678", None),  # 4-digit sequence (pre-2015 new-style)
        ("hep-th/9901001", "hep-th/9901001", None),
        ("math.GT/0309136v1", "math.GT/0309136", 1),
        ("https://arxiv.org/abs/2401.12345", "2401.12345", None),
        ("https://arxiv.org/abs/2401.12345v3", "2401.12345", 3),
        ("http://arxiv.org/pdf/2401.12345.pdf", "2401.12345", None),
        ("arxiv.org/pdf/2401.12345v2", "2401.12345", 2),
        ("https://arxiv.org/html/2401.12345v1", "2401.12345", 1),
        ("https://ar5iv.labs.arxiv.org/html/2401.12345", "2401.12345", None),
        ("https://arxiv.org/abs/hep-th/9901001", "hep-th/9901001", None),
        ("https://www.arxiv.org/abs/2401.12345/", "2401.12345", None),
    ],
)
def test_parse(raw, expected_id, expected_version):
    aid = parse_arxiv_id(raw)
    assert aid.id == expected_id
    assert aid.version == expected_version


@pytest.mark.parametrize("raw", ["", "not-a-paper", "https://example.com/abs/123", "12.34"])
def test_invalid(raw):
    with pytest.raises(InvalidArxivId):
        parse_arxiv_id(raw)


def test_versioned_and_slug():
    assert ArxivId("2401.12345", 2).versioned == "2401.12345v2"
    assert ArxivId("2401.12345").versioned == "2401.12345"
    assert ArxivId("hep-th/9901001", 1).slug == "hep-th_9901001v1"
