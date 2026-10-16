"""Guards the synthetic dataset: determinism, schema validity and the planted risks.

If one of these fails after editing scripts/generate_dataset.py, the demo is at
risk: fix the generator (or deliberately regenerate data/sample) rather than the test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_dataset as gd  # noqa: E402
from app.models import Artifact, ArtifactType, Person  # noqa: E402

SAMPLE = ROOT / "data" / "sample"


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("ds")
    gd.generate(out)
    return out


def load(d: Path, name: str) -> list[dict]:
    return json.loads((d / name).read_text(encoding="utf-8"))


def artifacts(d: Path) -> list[Artifact]:
    names = ["tickets", "emails", "chats", "wiki", "commits"]
    return [Artifact.model_validate(r) for n in names for r in load(d, f"{n}.json")]


def test_generation_is_byte_identical(generated, tmp_path):
    gd.generate(tmp_path)
    for f in sorted(generated.glob("*.json")):
        assert f.read_bytes() == (tmp_path / f.name).read_bytes(), f.name


def test_committed_sample_is_up_to_date(generated):
    for f in sorted(generated.glob("*.json")):
        assert (SAMPLE / f.name).read_bytes() == f.read_bytes(), (
            f"data/sample/{f.name} is stale: run scripts/generate_dataset.py"
        )


def test_volumes(generated):
    assert len(load(generated, "people.json")) == 4
    assert len(load(generated, "tickets.json")) >= 15
    assert len(load(generated, "wiki.json")) == 6
    assert len(load(generated, "commits.json")) >= 35
    msgs = sum(a.metadata["message_count"] for n in ("emails", "chats") for a in map(Artifact.model_validate, load(generated, f"{n}.json")))
    assert msgs >= 60


def test_every_record_validates_and_references_real_people(generated):
    people = {Person.model_validate(p).id for p in load(generated, "people.json")}
    arts = artifacts(generated)
    assert len({a.id for a in arts}) == len(arts), "artifact ids must be unique"
    assert len({(a.type, a.source_id) for a in arts}) == len(arts)
    for a in arts:
        assert a.author in people and set(a.participants) <= people, a.id
        resolver = a.metadata.get("resolved_by")
        assert resolver is None or resolver in people


def test_ravi_is_the_only_departing_person(generated):
    departing = [p["id"] for p in load(generated, "people.json") if p["is_departing"]]
    assert departing == ["ravi"]


def test_gateway_risk_is_planted(generated):
    arts = artifacts(generated)
    gw = {a.source_id: a for a in arts if a.type == ArtifactType.TICKET and "payment-gateway" in a.metadata["systems"]}
    assert set(gw) == {"INC-142", "INC-155", "INC-171", "INC-188"}
    assert all(t.metadata["resolved_by"] == "ravi" for t in gw.values())
    chat_text = " ".join(a.body for a in arts if a.type == ArtifactType.CHAT)
    assert "gwctl pool-status" in chat_text and "PARTNER_CERT_PIN" in chat_text


def test_settlement_risk_is_planted(generated):
    arts = artifacts(generated)
    resolvers = {a.metadata["resolved_by"] for a in arts if a.type == ArtifactType.TICKET and "settlement-batch-job" in a.metadata["systems"] and "incident" in a.tags}
    assert resolvers == {"arjun"}
    chats = [a for a in arts if a.type == ArtifactType.CHAT and "--skip-recon" in a.body]
    assert chats and all(a.author in {"arjun", "meera", "priya"} for a in chats)
    commits = [a for a in arts if a.type == ArtifactType.COMMIT and "settlement-batch-job" in a.metadata["systems"] and a.metadata["files"][0].startswith("services/settlement")]
    assert {c.author for c in commits} == {"arjun"}


def test_kafka_rationale_only_in_priyas_emails(generated):
    arts = artifacts(generated)
    rationale = [a for a in arts if "replay" in a.body.lower() and "rabbitmq" in a.body.lower()]
    assert rationale and all(a.type == ArtifactType.EMAIL for a in rationale)
    assert "priya" in {a.author for a in rationale}


def test_no_wiki_page_documents_planted_topics(generated):
    for doc in (a for a in artifacts(generated) if a.type == ArtifactType.DOC):
        body = doc.body.lower()
        for needle in ("gwctl", "pool-status", "--skip-recon", "--recon-only", "rabbitmq", "idempotency-key"):
            assert needle not in body, f"{doc.source_id} documents a planted topic ({needle})"


def test_contrast_topics_have_wiki_pages_and_multiple_people(generated):
    docs = {a.source_id for a in artifacts(generated) if a.type == ArtifactType.DOC}
    assert {"wiki/release-process", "wiki/onboarding-guide", "wiki/api-authentication"} <= docs
    arts = artifacts(generated)
    for system in ("api-authentication", "release-process"):
        people = {a.author for a in arts if a.type == ArtifactType.COMMIT and system in a.metadata["systems"]}
        people |= {a.metadata.get("resolved_by") for a in arts if a.type == ArtifactType.TICKET and system in a.metadata["systems"]}
        assert len(people - {None}) >= 2, system


def test_dataset_contains_pii_for_the_redactor(generated):
    assert any("+91 98450 12345" in a.body for a in artifacts(generated))
