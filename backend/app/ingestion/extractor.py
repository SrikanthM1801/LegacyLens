"""
Entity and relation extraction: Artifacts -> graph Entities + Edges.

Two layers:
  1. RuleExtractor  - deterministic and offline. Uses structured facts (ticket
     resolver, commit systems, authorship, per-speaker messages) and the alias
     catalog for free text. This is the mock-mode path and the reliable backbone.
  2. LLMExtractor   - runs the rule layer, then asks the LLM for additional
     entities in free text (new systems/processes/decisions). Aliases are resolved
     against known entities so "PG" and "gateway" never become two nodes. With the
     mock LLM it degrades to exactly the rule layer.

Edge semantics (weights accumulate; the analyzer decides how much each is worth):
  RESOLVED person->incident   AFFECTS incident/decision->system
  COMMITTED_TO person->topic  DISCUSSED person->topic (once per message)
  PARTICIPATED_IN person->topic (ticket work)  DECIDED person->decision
  AUTHORED person->artifact   MENTIONS artifact->entity
  DOCUMENTED_BY topic->doc (only docs that are *about* the topic)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, Field

from app.agents.prompts import EXTRACTION_SYSTEM, build_extraction_user
from app.config import get_settings
from app.ingestion.cache import JsonCache, content_key
from app.ingestion.catalog import BY_SLUG, TOPICS, Topic
from app.models import Artifact, ArtifactType, Edge, Entity, EntityType, Person, RelationType
from app.services.llm import LLMService, get_llm

log = logging.getLogger(__name__)

_INCIDENT_ID = re.compile(r"\bINC-(\d+)\b")
_CHAT_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}[ T][\d:]+)\]\s+([^:\n]+):\s?(.*)$")
_COMMENT_LINE = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\]\s+([^:\n]+):\s?(.*)$")
_EMAIL_FROM = re.compile(r"^From:\s*(.*?)\s*<([^>]+)>", re.MULTILINE)
_EMAIL_SPLIT = "\n\n---\n\n"
#: A doc is "about" a topic if it is in the title or mentioned at least this often.
_DOC_ABOUT_MIN_MENTIONS = 3


@dataclass
class ExtractionResult:
    entities: list[Entity] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class Extractor(Protocol):
    def extract(self, artifacts: list[Artifact], people: list[Person]) -> ExtractionResult: ...


# ---------------------------------------------------------------------------
# Alias matching
# ---------------------------------------------------------------------------


def alias_regex(alias: str) -> re.Pattern:
    flags = 0 if (len(alias) <= 3 and alias.isupper()) else re.IGNORECASE  # "PG" must be capitalised
    return re.compile(rf"(?<![\w]){re.escape(alias)}(?![\w])", flags)


_TOPIC_PATTERNS: dict[str, list[re.Pattern]] = {t.id: [alias_regex(a) for a in t.aliases] for t in TOPICS}
_TOPIC_BY_ID: dict[str, Topic] = {t.id: t for t in TOPICS}


def topic_mentions(text: str) -> dict[str, int]:
    """{topic_id: number of alias hits} for every catalog topic found in text."""
    out: dict[str, int] = {}
    for tid, patterns in _TOPIC_PATTERNS.items():
        n = sum(len(p.findall(text)) for p in patterns)
        if n:
            out[tid] = n
    return out


# ---------------------------------------------------------------------------
# Accumulator
# ---------------------------------------------------------------------------


class _Acc:
    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self._edges: dict[tuple[str, str, str], Edge] = {}

    def entity(self, e: Entity) -> Entity:
        cur = self.entities.get(e.id)
        if cur is None:
            self.entities[e.id] = e
            return e
        cur.aliases = list(dict.fromkeys([*cur.aliases, *e.aliases]))
        cur.artifact_ids = list(dict.fromkeys([*cur.artifact_ids, *e.artifact_ids]))
        cur.description = cur.description or e.description
        cur.attributes = {**e.attributes, **cur.attributes}
        return cur

    def seen(self, entity_id: str, artifact_id: str) -> None:
        e = self.entities[entity_id]
        if artifact_id not in e.artifact_ids:
            e.artifact_ids.append(artifact_id)

    def edge(self, src: str, dst: str, rel: RelationType, evidence: str | list[str], weight: float = 1.0) -> None:
        ev = [evidence] if isinstance(evidence, str) else list(evidence)
        key = (src, dst, rel.value)
        cur = self._edges.get(key)
        if cur is None:
            self._edges[key] = Edge(source=src, target=dst, relation=rel, weight=weight, evidence_artifact_ids=ev)
        else:
            cur.weight += weight
            cur.evidence_artifact_ids = list(dict.fromkeys([*cur.evidence_artifact_ids, *ev]))

    def has_edge(self, src: str, dst: str, rel: RelationType) -> bool:
        return (src, dst, rel.value) in self._edges

    def load(self, base: "ExtractionResult") -> None:
        for e in base.entities:
            self.entity(e)
        for ed in base.edges:
            self._edges[(ed.source, ed.target, str(ed.relation))] = ed.model_copy(deep=True)

    def result(self, warnings: list[str]) -> ExtractionResult:
        edges = sorted(self._edges.values(), key=lambda e: (e.source, e.target, e.relation))
        return ExtractionResult(sorted(self.entities.values(), key=lambda e: e.id), edges, warnings)


# ---------------------------------------------------------------------------
# Message parsing (who said what)
# ---------------------------------------------------------------------------


class _PeopleIndex:
    def __init__(self, people: list[Person]) -> None:
        self.by_key: dict[str, str] = {}
        for p in people:
            keys = {p.id, p.name, p.name.split()[0], *(p.aliases), *( [p.email] if p.email else [])}
            for k in keys:
                self.by_key[k.lower()] = p.id

    def resolve(self, name_or_email: str) -> Optional[str]:
        return self.by_key.get(name_or_email.strip().lower())

    def add(self, name: str, person_id: str) -> None:
        for k in (name, name.split()[0]):
            self.by_key.setdefault(k.strip().lower(), person_id)


def parse_messages(a: Artifact, people: _PeopleIndex) -> list[tuple[Optional[str], str]]:
    """Split a thread artifact into (person_id, text) messages. Others: one message by the author."""
    if a.type == ArtifactType.CHAT:
        msgs: list[list] = []
        for line in a.body.splitlines():
            m = _CHAT_LINE.match(line)
            if m:
                msgs.append([people.resolve(m.group(2)), m.group(3)])
            elif msgs:
                msgs[-1][1] += "\n" + line
        return [(who, text) for who, text in msgs]
    if a.type == ArtifactType.EMAIL:
        out = []
        for block in a.body.split(_EMAIL_SPLIT):
            m = _EMAIL_FROM.search(block)
            who = people.resolve(m.group(2)) or people.resolve(m.group(1)) if m else None
            text = block.split("\n\n", 1)[1] if "\n\n" in block else block
            out.append((who, text))
        return out
    return [(a.author, a.body)]


# ---------------------------------------------------------------------------
# Rule-based extractor
# ---------------------------------------------------------------------------


def _person_entity(p: Person) -> Entity:
    return Entity(
        id=p.id, type=EntityType.PERSON, name=p.name,
        aliases=list(dict.fromkeys([*p.aliases, p.name.split()[0]])),
        description=p.role,
        attributes={
            "role": p.role,
            "is_departing": p.is_departing,
            "departure_date": p.departure_date.isoformat() if p.departure_date else None,
        },
    )


def _topic_entity(t: Topic) -> Entity:
    return Entity(id=t.id, type=t.type, name=t.name, aliases=list(t.aliases), description=t.description)


class RuleExtractor:
    """Deterministic extraction; needs no LLM.

    `catalog=False` turns off the sample project's topic catalog (keyword matches such as
    "release process" -> the sample's Release Process topic). Live data uses it off, so real
    documents only get topics the LLM finds in them."""

    def __init__(self, catalog: bool = True) -> None:
        self._catalog = catalog

    def extract(self, artifacts: list[Artifact], people: list[Person]) -> ExtractionResult:
        acc = _Acc()
        for p in people:
            acc.entity(_person_entity(p))
        index = _PeopleIndex(people)
        ids = {p.id for p in people}
        for a in artifacts:
            self._one(a, acc, index, ids)
        for t in TOPICS:  # decisions affect the system they concern
            if t.affects and t.id in acc.entities and t.affects in acc.entities:
                acc.edge(t.id, t.affects, RelationType.AFFECTS, acc.entities[t.id].artifact_ids)
        return acc.result([])

    # -- per artifact ---------------------------------------------------------
    def _one(self, a: Artifact, acc: _Acc, index: _PeopleIndex, known_people: set[str]) -> None:
        if a.author in known_people:
            acc.edge(a.author, a.id, RelationType.AUTHORED, a.id)

        if a.type == ArtifactType.TICKET:
            self._ticket(a, acc, known_people)
        if a.type == ArtifactType.COMMIT:
            self._commit(a, acc, known_people)

        # Incident references in any artifact ("INC-142") link to the incident entity.
        for num in dict.fromkeys(_INCIDENT_ID.findall(a.title + " " + a.body)):
            inc_id = f"inc_{num}"
            if inc_id in acc.entities:
                acc.seen(inc_id, a.id)
                acc.edge(a.id, inc_id, RelationType.MENTIONS, a.id)

        # Free-text topic mentions, attributed to whoever wrote the message.
        total: dict[str, int] = {}
        for who, text in (parse_messages(a, index) if self._catalog else []):
            for tid, n in topic_mentions(text).items():
                total[tid] = total.get(tid, 0) + n
                topic = _TOPIC_BY_ID[tid]
                acc.entity(_topic_entity(topic))
                if who in known_people and a.type in (ArtifactType.CHAT, ArtifactType.EMAIL):
                    acc.edge(who, tid, RelationType.DISCUSSED, a.id)
                if who in known_people and topic.decision_cues and any(c in text.lower() for c in topic.decision_cues):
                    acc.edge(who, tid, RelationType.DECIDED, a.id)
        for slug in (a.metadata.get("systems", []) if self._catalog else []):  # structured hints from tickets/commits
            topic = BY_SLUG.get(slug)
            if topic:
                total.setdefault(topic.id, 1)
        for tid in total:
            acc.entity(_topic_entity(_TOPIC_BY_ID[tid]))
            acc.seen(tid, a.id)
            acc.edge(a.id, tid, RelationType.MENTIONS, a.id)

        if a.type == ArtifactType.DOC:
            title_hits = topic_mentions(a.title)
            for tid, n in total.items():
                if tid in title_hits or n >= _DOC_ABOUT_MIN_MENTIONS:
                    acc.edge(tid, a.id, RelationType.DOCUMENTED_BY, a.id)

    def _ticket(self, a: Artifact, acc: _Acc, people: set[str]) -> None:
        md = a.metadata
        key = md.get("key", a.source_id)
        topics = [BY_SLUG[s] for s in md.get("systems", []) if s in BY_SLUG]
        for t in topics:
            acc.entity(_topic_entity(t))
        resolver = md.get("resolved_by")
        m = _INCIDENT_ID.fullmatch(key)
        if m:  # an incident ticket
            inc_id = f"inc_{m.group(1)}"
            acc.entity(Entity(
                id=inc_id, type=EntityType.INCIDENT, name=key, aliases=[key],
                description=a.title.removeprefix(key).strip(), artifact_ids=[a.id],
                attributes={k: md.get(k) for k in ("severity", "status", "resolved_by", "resolved_at")} | {"opened_at": a.timestamp.isoformat()},
            ))
            for t in topics:
                acc.edge(inc_id, t.id, RelationType.AFFECTS, a.id)
            if resolver in people:
                acc.edge(resolver, inc_id, RelationType.RESOLVED, a.id)
            for p in a.participants:
                if p in people and p != resolver:
                    acc.edge(p, inc_id, RelationType.PARTICIPATED_IN, a.id)
        else:  # ordinary work ticket: the resolver worked on the topic
            for t in topics:
                if resolver in people:
                    acc.edge(resolver, t.id, RelationType.PARTICIPATED_IN, a.id)

    def _commit(self, a: Artifact, acc: _Acc, people: set[str]) -> None:
        for slug in a.metadata.get("systems", []):
            t = BY_SLUG.get(slug)
            if t and a.author in people:
                acc.entity(_topic_entity(t))
                acc.edge(a.author, t.id, RelationType.COMMITTED_TO, a.id)


# ---------------------------------------------------------------------------
# LLM-augmented extractor
# ---------------------------------------------------------------------------


class LLMEntity(BaseModel):
    name: str
    type: Literal["system", "process", "decision", "incident"]
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    decided_by: Optional[str] = None
    owners: list[str] = Field(default_factory=list)


class LLMExtraction(BaseModel):
    entities: list[LLMEntity] = Field(default_factory=list)


_NOT_A_PERSON = re.compile(r"\b(team|owner|board|committee|manager|lead|engineer|admin|group|department|vendor|"
                           r"client|sponsor|steerco|pmo|qa|l&d|nfsl|pspl|presidio|ltd|limited|inc)\b|\d", re.IGNORECASE)


def _named_person(name: str, acc: "_Acc", index: _PeopleIndex) -> Optional[str]:
    """A person named in a live document or chat who isn't a known Teams user yet (e.g. 'Dinesh Udhyakumar')."""
    name = " ".join(name.replace("(", " ").replace(")", " ").split())
    if not name or len(name) > 40 or _NOT_A_PERSON.search(name) or not name[0].isupper():
        return None
    pid = "person_doc_" + _slug(name)
    if pid not in acc.entities:
        acc.entity(Entity(id=pid, type=EntityType.PERSON, name=name, aliases=[name.split()[0]],
                          attributes={"source": "documents"}))
    index.add(name, pid)
    return pid


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60]


_PREFIX = {"system": "sys", "process": "proc", "decision": "dec", "incident": "inc"}
_LLM_TYPES = (ArtifactType.CHAT, ArtifactType.EMAIL, ArtifactType.DOC, ArtifactType.TICKET)


class LLMExtractor:
    """Rule extraction plus LLM discovery of entities the catalog doesn't know."""

    def __init__(self, llm: LLMService, rules: Optional[RuleExtractor] = None, cache: Optional[JsonCache] = None) -> None:
        self._llm = llm
        self._rules = rules or RuleExtractor()
        self._cache = cache

    def extract(self, artifacts: list[Artifact], people: list[Person]) -> ExtractionResult:
        base = self._rules.extract(artifacts, people)
        acc = _Acc()
        acc.load(base)
        index = _PeopleIndex(people)
        warnings = list(base.warnings)

        for a in artifacts:
            if a.type not in _LLM_TYPES:
                continue
            known = list(acc.entities.values())
            try:
                out = self._call_llm(a, known)
            except Exception as exc:  # one bad artifact must not abort the run
                warnings.append(f"{a.source_id}: LLM extraction failed ({type(exc).__name__}); used rules only")
                continue
            for item in out.entities:
                self._apply(item, a, acc, index)
        if self._cache:
            self._cache.save()
        return acc.result(warnings)

    def _call_llm(self, a: Artifact, known: list[Entity]) -> LLMExtraction:
        user = build_extraction_user(a, known)
        key = content_key(EXTRACTION_SYSTEM, user)  # known entities are part of the key
        if self._cache and (hit := self._cache.get(key)) is not None:
            return LLMExtraction.model_validate(hit)
        out = self._llm.chat_json(EXTRACTION_SYSTEM, user, LLMExtraction)
        if self._cache:
            self._cache.set(key, out.model_dump())
        return out

    def _resolve(self, item: LLMEntity, acc: _Acc) -> Optional[str]:
        wanted = {item.name.lower(), *(x.lower() for x in item.aliases)}
        for e in acc.entities.values():
            if e.type == EntityType.PERSON:
                continue
            if wanted & {e.name.lower(), *(x.lower() for x in e.aliases)}:
                return e.id
        return None

    def _apply(self, item: LLMEntity, a: Artifact, acc: _Acc, index: _PeopleIndex) -> None:
        eid = self._resolve(item, acc)
        if eid is None:
            if not item.name.strip():
                return
            m = _INCIDENT_ID.search(item.name)
            eid = f"inc_{m.group(1)}" if item.type == "incident" and m else f"{_PREFIX[item.type]}_{_slug(item.name)}"
            acc.entity(Entity(id=eid, type=EntityType(item.type), name=item.name.strip(),
                              aliases=item.aliases, description=item.description))
        acc.seen(eid, a.id)
        if not acc.has_edge(a.id, eid, RelationType.MENTIONS):  # don't double count what rules found
            acc.edge(a.id, eid, RelationType.MENTIONS, a.id)
        live = bool(a.metadata.get("live"))
        if item.decided_by and acc.entities[eid].type == EntityType.DECISION:
            who = index.resolve(item.decided_by) or (_named_person(item.decided_by, acc, index) if live else None)
            if who:
                acc.edge(who, eid, RelationType.DECIDED, a.id)
        if live:
            self._link_live(item, eid, a, acc, index)

    @staticmethod
    def _link_live(item: LLMEntity, eid: str, a: Artifact, acc: _Acc, index: _PeopleIndex) -> None:
        """Live data has no topic catalog, so who-knows-what comes from the LLM's reading:
        people named as owners/fixers PARTICIPATED_IN the topic; a chat or email author DISCUSSED it;
        a live document DOCUMENTS what it discusses (coverage + its author's AUTHORED_DOC credit)."""
        for name in item.owners[:5]:
            who = index.resolve(name) or _named_person(name, acc, index)
            if who:
                acc.edge(who, eid, RelationType.PARTICIPATED_IN, a.id, weight=1.5)
        if a.type in (ArtifactType.CHAT, ArtifactType.EMAIL) and a.author in acc.entities:
            acc.edge(a.author, eid, RelationType.DISCUSSED, a.id)
        if a.type == ArtifactType.DOC and acc.entities[eid].type != EntityType.INCIDENT:
            acc.edge(eid, a.id, RelationType.DOCUMENTED_BY, a.id)


@lru_cache
def get_extractor() -> Extractor:
    s = get_settings()
    if s.use_mocks or not s.azure_openai_configured:
        return RuleExtractor()
    return LLMExtractor(get_llm(), rules=RuleExtractor(catalog=s.use_sample_data),
                        cache=JsonCache(s.state_dir / "extraction.json"))
