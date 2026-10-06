from agentgate import normalize as N
from agentgate.spec import TaskSpec
from agentgate.trace import Origin, ValueIndex


def test_normalization_rules():
    assert N.norm_value("Mark.Black@Gmail.com") == "mark.black@gmail.com"
    assert N.norm_value("https://www.Example.com/a/") == "example.com/a"
    assert N.norm_value("GB29 NWBK 6016 1331 9268 19") == "GB29NWBK60161331926819"
    assert list(N.iter_values(["a", ["b"]])) == ["a", "b"]


def test_contains_word_boundary_and_kinds():
    h = N.norm_haystack("Contact Bobby at https://www.site.com/x/ or DE89 3704 0044 0532 0130 00")
    assert not N.contains(h, "Bob")
    assert N.contains(h, "Bobby")
    assert N.contains(h, "site.com/x")
    assert N.contains(h, "DE89370400440532013000")


def test_origin_prompt_tool_named_unseen():
    spec = TaskSpec(named_sources=["bill.txt"])
    vi = ValueIndex("Pay to alice@example.com please", spec)
    vi.add_response(0, "read_file", {"file_path": "bill.txt"}, "IBAN: DE89370400440532013000", "IBAN: DE89370400440532013000")
    vi.add_response(1, "search_emails", {"query": "x"}, "from eve@evil.com", "from eve@evil.com")
    assert vi.origin_of("ALICE@example.com").origin == Origin.PROMPT
    assert vi.origin_of("DE89 3704 0044 0532 0130 00").origin == Origin.NAMED_SOURCE
    r = vi.origin_of("eve@evil.com")
    assert r.origin == Origin.OTHER_TOOL and r.step == 1 and r.tool == "search_emails"
    assert vi.origin_of("generated@nowhere.org").origin == Origin.UNSEEN


def test_allowed_dest_and_trusted_tool():
    vi = ValueIndex("hi", TaskSpec(allowed_dest=["x@y.com"]))
    vi.add_response(0, "search_contacts_by_name", {"query": "Sarah"}, "sarah@corp.com", "sarah@corp.com")
    assert vi.origin_of("x@y.com").origin == Origin.ALLOWED_DEST
    assert vi.origin_of("sarah@corp.com").origin == Origin.TRUSTED_TOOL


def test_short_ids_match_exact_fields():
    vi = ValueIndex("delete the largest file", TaskSpec())
    vi.add_response(0, "list_files", {}, [{"id_": "11", "size": 900}], "- id_: '11'")
    assert vi.origin_of("11").origin == Origin.OTHER_TOOL
    assert vi.origin_of("1").origin == Origin.UNSEEN


def test_overlap_counts_distinct_reads():
    vi = ValueIndex("p", TaskSpec())
    vi.add_response(0, "get_file_by_id", {"file_id": "1"}, "the quarterly revenue figures are confidential", "")
    vi.add_response(1, "get_file_by_id", {"file_id": "2"}, "unrelated text about the weather", "")
    hits = vi.overlap("FYI: the quarterly revenue figures are confidential!", 16, ["file_content"])
    assert [h.sid for h in hits] == ["step0"]
