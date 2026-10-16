import re

import pytest
from fastapi.testclient import TestClient

from app.agents.generator import (
    DraftItem, RunbookDraft, Source, _validate, classify, generate_runbook, render_runbook, rule_draft,
)
from app.graph import NetworkXGraphStore, get_graph
from app.main import app
from app.models import Citation, Entity, EntityType
from app.services import doc_store
from app.services.search import InMemorySearch

GW = "risk_sys_payment_gateway"
ANSWERS = [
    "First I run `gwctl pool-status` and read the stale count; above 20% means idle sockets, so drain and restart deploy/pg-adapter.",
    "Restart with `gwctl drain` then `kubectl rollout restart deploy/pg-adapter`. Never restart between 02:00 and 03:00 UTC.",
    "If the cert pin changed, update PARTNER_CERT_PIN in the pg-adapter configmap and restart; INC-171 was this.",
    "Confirm recovery on the PG Auth Grafana dashboard: success rate above 98% within 5 minutes. Escalate to the acquirer NOC after 10 minutes without recovery.",
]
REF = re.compile(r"\[(\d+)\]")


def assert_fully_cited(doc):
    """Every list item cites sources, every citation is used, and numbers are dense from 1."""
    body = doc.markdown.split("## Sources")[0]
    items = [ln for ln in body.splitlines() if re.match(r"^(- |\d+\. )", ln)]
    items = [ln for ln in items if not ln.startswith("- No source") and "Run an interview" not in ln]
    open_q = doc.markdown.split("## Open questions")[-1].split("## Sources")[0] if "## Open questions" in doc.markdown else ""
    cited_lines = [ln for ln in items if ln not in open_q]
    assert cited_lines
    refs = set()
    for ln in cited_lines:
        nums = [int(n) for n in REF.findall(ln)]
        assert nums and ln.rstrip().endswith("]"), f"uncited item: {ln}"
        refs |= set(nums)
    assert refs == set(range(1, len(doc.citations) + 1))
    sources_block = doc.markdown.split("## Sources")[1].strip().splitlines()
    assert len(sources_block) == len(doc.citations)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh(client):
    client.post("/ingest")


def publish_interview(client, risk=GW, texts=ANSWERS):
    s = client.post(f"/interviews/{risk}/start").json()
    for q, t in zip(s["questions"], texts):
        client.post(f"/interviews/{s['id']}/answer", json={"question_id": q["id"], "text": t})
    assert client.post(f"/interviews/{s['id']}/approve").status_code == 200


@pytest.mark.parametrize("text,section", [
    ("Escalate to the acquirer NOC after ten minutes.", "escalation"),
    ("Never restart between 02:00 and 03:00 UTC.", "gotchas"),
    ("Confirm recovery on the dashboard within five minutes.", "verification"),
    ("Drain the adapter and restart deploy/pg-adapter.", "fix"),
    ("Check `gwctl pool-status` and read the stale count.", "diagnosis"),
    ("Card authorisations are timing out for 30% of checkouts.", "symptoms"),
    ("The weather was lovely today in the office.", None),
    ("We went to checkouts at the supermarket.", None),   # "checkouts" is not "check"
])
def test_classify(text, section):
    assert classify(text) == section


def test_rule_draft_routes_dedupes_and_skips_noise():
    src = [
        Source(Citation(artifact_id="a", snippet="", title="A"), "Question: why?\n\nDrain the adapter and restart it now.\nDrain the adapter and restart it now.", True),
        Source(Citation(artifact_id="b", snippet="", title="B"),
               "INC-1: Title line that is long enough to count\nSeverity: P1\n[2026-01-01 10:00] Ravi: @arjun Never restart it during the settlement window.\nIs this a question we should skip?", False),
    ]
    d = rule_draft(src).sections
    assert [i.text for i in d["fix"]] == ["Drain the adapter and restart it now."] and d["fix"][0].sources == [1]
    assert [i.text for i in d["gotchas"]] == ["Never restart it during the settlement window."] and d["gotchas"][0].sources == [2]
    assert sum(len(v) for v in d.values()) == 2


def test_validate_drops_uncited_and_out_of_range_items():
    draft = RunbookDraft(sections={
        "fix": [DraftItem(text="ok", sources=[1, 99, 1]), DraftItem(text="no sources", sources=[]),
                DraftItem(text="bad idx", sources=[0, 5])],
        "bogus": [DraftItem(text="unknown section", sources=[1])],
    })
    out = _validate(draft, 2)
    assert [(i.text, i.sources) for i in out["fix"]] == [("ok", [1])] and "bogus" not in out


def test_render_numbers_citations_by_first_use():
    cits = [Citation(artifact_id=f"a{i}", snippet="", title=f"T{i}", artifact_type="chat") for i in (1, 2, 3)]
    srcs = [Source(c, "", False) for c in cits]
    secs = {k: [] for k in ("symptoms", "diagnosis", "fix", "verification", "escalation", "gotchas")}
    secs["symptoms"] = [DraftItem(text="late one", sources=[3])]
    secs["fix"] = [DraftItem(text="step", sources=[1, 3])]
    md, used = render_runbook("Runbook: X", "X", secs, srcs, ["What else?"], 0)
    assert [c.artifact_id for c in used] == ["a3", "a1"]            # source 2 never used, so absent
    assert "- late one [1]" in md and "1. step [2][1]" in md and "DRAFT" in md and "- What else?" in md
    assert md.index("1. T3") < md.index("2. T1")


def test_runbook_draft_from_conversations_is_cited_and_flagged_as_draft(client):
    r = client.post("/artifacts/runbook", json={"topic": "PG"})  # alias resolves to the gateway
    assert r.status_code == 200
    doc = r.json()
    assert doc["type"] == "runbook" and doc["title"] == "Runbook: Payment Gateway" and doc["topic"] == "Payment Gateway"
    assert doc["model"] == "mock" and doc["id"].startswith("doc_")
    assert "DRAFT" in doc["markdown"] and any("interview" in q.lower() for q in doc["open_questions"])
    assert all(c["artifact_type"] in {"chat", "email", "ticket"} for c in doc["citations"])
    assert "## Fix steps" in doc["markdown"] and "gwctl" in doc["markdown"]
    from app.models import GeneratedDoc
    assert_fully_cited(GeneratedDoc.model_validate(doc))


def test_published_interviews_become_the_primary_sources(client):
    publish_interview(client)
    doc = client.post("/artifacts/runbook", json={"topic": "Payment Gateway", "risk_id": GW}).json()
    assert "DRAFT" not in doc["markdown"] and "published interview answers" in doc["markdown"]
    interview_cites = [c for c in doc["citations"] if c["artifact_type"] == "interview"]
    assert len(interview_cites) >= 3
    for must in ("gwctl drain", "PARTNER_CERT_PIN", "Never restart between 02:00 and 03:00 UTC", "Escalate to the acquirer NOC"):
        assert must in doc["markdown"], must
    assert all(not q.startswith("Run an interview") for q in doc["open_questions"])
    assert "98450" not in doc["markdown"]
    from app.models import GeneratedDoc
    assert_fully_cited(GeneratedDoc.model_validate(doc))
    off = client.post("/artifacts/runbook", json={"topic": "PG", "include_interviews": False}).json()
    assert not [c for c in off["citations"] if c["artifact_type"] == "interview"] and "DRAFT" in off["markdown"]


def test_documents_are_stored_listed_and_cleared_on_reset(client):
    a = client.post("/artifacts/runbook", json={"topic": "Settlement Batch Job"}).json()
    assert client.get(f"/artifacts/{a['id']}").json() == a
    assert [d["id"] for d in client.get("/artifacts", params={"type": "runbook"}).json()] == [a["id"]]
    assert client.get("/artifacts", params={"type": "handover"}).json() == []
    assert client.get("/artifacts/doc_nope").status_code == 404
    client.post("/ingest")
    assert client.get("/artifacts").json() == []


def test_errors(client):
    assert client.post("/artifacts/runbook", json={"topic": "No Such Thing"}).status_code == 404
    assert client.post("/artifacts/runbook", json={"topic": "x", "risk_id": "risk_nope"}).status_code == 404
    assert client.post("/artifacts/runbook", json={}).status_code == 422
    assert client.post("/artifacts/runbook", json={"topic": "PG", "extra": 1}).status_code == 422


def test_no_sources_means_no_runbook(tmp_path):
    g = NetworkXGraphStore(tmp_path / "g.json")
    ent = Entity(id="sys_x", type=EntityType.SYSTEM, name="X")
    g.add_entity(ent)
    from app.config import get_settings
    from app.services.llm import MockLLM
    llm = MockLLM(get_settings())
    assert generate_runbook(ent, g, InMemorySearch(llm), llm) is None


# --- LLM path ------------------------------------------------------------------------


class ScriptedLLM:
    model_name = "scripted-gpt"

    def __init__(self, draft=None, fail=False):
        self.draft, self.fail, self.prompts = draft, fail, []

    def chat_json(self, system, user, schema):
        self.prompts.append((system, user))
        if self.fail:
            raise ValueError("boom")
        return self.draft


@pytest.fixture
def world(client):
    from app.services.search import get_search
    return get_graph(), get_search()


def test_llm_runbook_is_validated_renumbered_and_labelled(client, world):
    g, s = world
    publish_interview(client)
    draft = RunbookDraft(
        sections={"fix": [DraftItem(text="Drain, then restart `deploy/pg-adapter`.", sources=[1]),
                          DraftItem(text="Invented step with no source.", sources=[]),
                          DraftItem(text="Cites a source that does not exist.", sources=[42])],
                  "gotchas": [DraftItem(text="Do not restart between 02:00 and 03:00 UTC.", sources=[2, 1])]},
        open_questions=["Who is the backup contact at the acquirer?"],
    )
    llm = ScriptedLLM(draft)
    doc = generate_runbook(g.get_entity("sys_payment_gateway"), g, s, llm)
    assert doc.model == "scripted-gpt" and "Invented step" not in doc.markdown and "does not exist" not in doc.markdown
    assert "1. Drain, then restart `deploy/pg-adapter`. [1]" in doc.markdown
    assert "- Do not restart between 02:00 and 03:00 UTC. [2][1]" in doc.markdown
    assert "Who is the backup contact at the acquirer?" in doc.open_questions
    assert len(doc.citations) == 2 and doc.citations[0].artifact_type == "interview"
    system, user = llm.prompts[0]
    assert "(INTERVIEW)" in user and "[1]" in user and "Never invent" in system
    assert_fully_cited(doc)


@pytest.mark.parametrize("llm", [ScriptedLLM(fail=True), ScriptedLLM(RunbookDraft()),
                                 ScriptedLLM(RunbookDraft(sections={"fix": [DraftItem(text="x", sources=[])]}))])
def test_llm_failure_or_empty_output_falls_back_to_rules(client, world, llm):
    g, s = world
    doc = generate_runbook(g.get_entity("sys_payment_gateway"), g, s, llm)
    assert doc.model == "rules" and "## Fix steps" in doc.markdown
    assert_fully_cited(doc)
