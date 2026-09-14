from pathlib import Path

from metabase_export.cli import (
    clear_output,
    extract_parameters,
    extract_sql,
    plan_files,
    render,
    safe_name,
)

MBQL5 = {"stages": [{"native": "select 1", "template-tags": {"b": {}, "a": {}}}]}
LEGACY = {"native": {"query": "select 2", "template-tags": {"x": {}}}}


def card(**overrides):
    base = {
        "id": 1,
        "collection_id": 7,
        "name": "TOTAL",
        "display": "scalar",
        "database_name": "Reports",
        "dataset_query": MBQL5,
    }
    return {**base, **overrides}


def test_reads_both_query_formats():
    assert extract_sql(MBQL5) == "select 1"
    assert extract_sql(LEGACY) == "select 2"
    assert extract_sql({}) == ""


def test_parameters_are_sorted():
    assert extract_parameters(MBQL5) == ["a", "b"]
    assert extract_parameters(LEGACY) == ["x"]
    assert extract_parameters({}) == []


def test_safe_name_strips_path_separators():
    assert safe_name("a/b:c") == "a-b-c"
    assert safe_name("  trailing dot.  ") == "trailing dot"
    # Separators become dashes, so only a name that cleans to nothing needs a fallback.
    assert safe_name("///") == "---"
    assert safe_name("  ...  ") == "unnamed"


def test_render_header_and_body():
    assert render(card(), "Reports/Daily") == (
        "-- Card: 1 | DB: Reports | Viz: scalar\n"
        "-- Collection: Reports/Daily\n"
        "-- Parameters: {{a}} {{b}}\n"
        "\n"
        "select 1\n"
    )


def test_render_omits_parameter_line_when_there_are_none():
    rendered = render(card(dataset_query={"stages": [{"native": "select 1"}]}), "R")
    assert "-- Parameters" not in rendered
    assert rendered.endswith("select 1\n")


def test_render_normalises_line_endings_and_trailing_space():
    rendered = render(card(dataset_query={"stages": [{"native": "a  \r\nb\r\n"}]}), "R")
    assert rendered.endswith("a\nb\n")


def test_plan_skips_cards_without_sql_and_outside_the_tree():
    cards = [
        card(id=1),
        card(id=2, dataset_query={"stages": [{"native": "   "}]}),
        card(id=3, collection_id=99),
    ]
    targets, skipped = plan_files(cards, {7: "Reports"}, Path("out"))
    assert [t[0]["id"] for t in targets] == [1]
    assert [s["id"] for s in skipped] == [2]


def test_plan_disambiguates_duplicate_names():
    targets, _ = plan_files([card(id=1), card(id=2)], {7: "Reports"}, Path("out"))
    assert [t[2].name for t in targets] == ["TOTAL.sql", "TOTAL (card 2).sql"]


def test_clear_output_keeps_underscore_directories(tmp_path):
    (tmp_path / "Reports").mkdir()
    (tmp_path / "Reports" / "a.sql").write_text("x")
    (tmp_path / "_manual").mkdir()
    (tmp_path / "_manual" / "keep.sql").write_text("x")

    removed = clear_output(tmp_path, dry_run=False)

    assert removed == 1
    assert (tmp_path / "_manual" / "keep.sql").exists()
    assert not (tmp_path / "Reports").exists()
