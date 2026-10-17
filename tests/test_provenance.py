"""값 정규화·포함 판정과 ValueIndex의 출처(origin) 판정을 검사한다."""

from agentgate.gate.normalization import build_haystack, haystack_contains, iterate_leaf_values, normalize_value
from agentgate.gate.provenance import Origin, ValueIndex
from agentgate.gate.task_spec import TaskSpec


def test_normalization_rules():
    assert normalize_value("Mark.Black@Gmail.com") == "mark.black@gmail.com"
    assert normalize_value("https://www.Example.com/a/") == "example.com/a"
    assert normalize_value("GB29 NWBK 6016 1331 9268 19") == "GB29NWBK60161331926819"
    assert list(iterate_leaf_values(["a", ["b"]])) == ["a", "b"]


def test_contains_word_boundary_and_kinds():
    haystack = build_haystack("Contact Bobby at https://www.site.com/x/ or DE89 3704 0044 0532 0130 00")
    assert not haystack_contains(haystack, "Bob")
    assert haystack_contains(haystack, "Bobby")
    assert haystack_contains(haystack, "site.com/x")
    assert haystack_contains(haystack, "DE89370400440532013000")


def test_origin_prompt_tool_named_unseen():
    spec = TaskSpec(named_sources=["bill.txt"])
    value_index = ValueIndex("Pay to alice@example.com please", spec)
    value_index.add_response(0, "read_file", {"file_path": "bill.txt"}, "IBAN: DE89370400440532013000",
                             "IBAN: DE89370400440532013000")
    value_index.add_response(1, "search_emails", {"query": "x"}, "from eve@evil.com", "from eve@evil.com")
    assert value_index.origin_of("ALICE@example.com").origin == Origin.PROMPT
    assert value_index.origin_of("DE89 3704 0044 0532 0130 00").origin == Origin.NAMED_SOURCE
    result = value_index.origin_of("eve@evil.com")
    assert result.origin == Origin.OTHER_TOOL and result.step == 1 and result.tool == "search_emails"
    assert value_index.origin_of("generated@nowhere.org").origin == Origin.UNSEEN


def test_allowed_dest_and_trusted_tool():
    value_index = ValueIndex("hi", TaskSpec(allowed_dest=["x@y.com"]))
    value_index.add_response(0, "search_contacts_by_name", {"query": "Sarah"}, "sarah@corp.com", "sarah@corp.com")
    assert value_index.origin_of("x@y.com").origin == Origin.ALLOWED_DEST
    assert value_index.origin_of("sarah@corp.com").origin == Origin.TRUSTED_TOOL


def test_short_ids_match_exact_fields():
    value_index = ValueIndex("delete the largest file", TaskSpec())
    value_index.add_response(0, "list_files", {}, [{"id_": "11", "size": 900}], "- id_: '11'")
    assert value_index.origin_of("11").origin == Origin.OTHER_TOOL
    assert value_index.origin_of("1").origin == Origin.UNSEEN


def test_overlap_counts_distinct_reads():
    value_index = ValueIndex("p", TaskSpec())
    value_index.add_response(0, "get_file_by_id", {"file_id": "1"}, "the quarterly revenue figures are confidential",
                             "")
    value_index.add_response(1, "get_file_by_id", {"file_id": "2"}, "unrelated text about the weather", "")
    hits = value_index.find_overlapping_sources("FYI: the quarterly revenue figures are confidential!", 16,
                                                ["file_content"])
    assert [h.source_id for h in hits] == ["step0"]
