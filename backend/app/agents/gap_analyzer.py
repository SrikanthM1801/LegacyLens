"""
Gap and risk analyzer.

1. `compute_topic_stats`: for every topic (system / process / decision), the
   recency-weighted activity per person, shares, bus factor, single-person flag.
2. `analyze_risks`: combines that with documentation coverage (agents/coverage.py)
   into ranked `RiskItem`s with citations:

       risk = normalized_activity x (1 / bus_factor) x (1 - coverage)
       x1.5 if the top owner is departing, capped at 1.   high >= 0.6, medium >= 0.3

Scoring helpers are pure; only `analyze_risks` touches the graph and search.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.agents.coverage import compute_coverage
from app.graph.queries import activity_by_topic
from app.graph.store import GraphStore
from app.models import ArtifactType, Citation, DocType, EntityType, RiskItem

#: A person "knows" a topic if they hold at least this share of its activity.
BUS_FACTOR_MIN_SHARE = 0.15
#: One person holding at least this share makes the topic a single-person risk.
SINGLE_PERSON_SHARE = 0.70


@dataclass(frozen=True)
class TopicStats:
    topic_id: str
    name: str
    type: EntityType
    activity: float                       # total recency-weighted activity (raw)
    by_person: dict[str, float]           # person_id -> activity
    shares: dict[str, float]              # person_id -> share of the topic, sums to 1
    owners: list[str]                     # person ids, highest share first (ties by id)
    bus_factor: int                       # people with share >= BUS_FACTOR_MIN_SHARE (min 1)
    single_person_risk: bool              # top owner share >= SINGLE_PERSON_SHARE

    @property
    def top_owner(self) -> Optional[str]:
        return self.owners[0] if self.owners else None


def shares_of(by_person: dict[str, float]) -> dict[str, float]:
    total = sum(by_person.values())
    return {p: v / total for p, v in by_person.items()} if total > 0 else {}


def bus_factor(shares: dict[str, float], min_share: float = BUS_FACTOR_MIN_SHARE) -> int:
    """Number of people who each hold at least `min_share` of the topic; never below 1."""
    return max(1, sum(1 for s in shares.values() if s >= min_share))


def compute_topic_stats(graph: GraphStore, as_of: Optional[datetime] = None) -> list[TopicStats]:
    """Stats for every topic with activity, busiest first (ties by id)."""
    names = {e.id: e for e in graph.entities()}
    out = []
    for tid, by_person in activity_by_topic(graph, recency=True, as_of=as_of).items():
        shares = shares_of(by_person)
        owners = sorted(shares, key=lambda p: (-shares[p], p))
        out.append(TopicStats(
            topic_id=tid,
            name=names[tid].name,
            type=names[tid].type,
            activity=sum(by_person.values()),
            by_person=by_person,
            shares=shares,
            owners=owners,
            bus_factor=bus_factor(shares),
            single_person_risk=bool(owners) and shares[owners[0]] >= SINGLE_PERSON_SHARE,
        ))
    return sorted(out, key=lambda s: (-s.activity, s.topic_id))


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

DEPARTING_BOOST = 1.5
RISK_HIGH = 0.6
RISK_MEDIUM = 0.3
#: Activity is normalized against the busiest topic *of the same type* (a decision is
#: discussed, not committed to or fixed, so it can never match a system's raw activity).
#: The floor stops a barely-active topic from scoring 1.0 just because it is the only one
#: of its type: it must reach about three resolved incidents' worth of activity (3 x 5).
ACTIVITY_FLOOR = 15.0
#: Coverage below this means the topic still needs a document.
COVERAGE_ADEQUATE = 0.5
EVIDENCE_LIMIT = 5
_EVIDENCE_TYPES = [t.value for t in (ArtifactType.TICKET, ArtifactType.CHAT, ArtifactType.EMAIL, ArtifactType.COMMIT)]


def normalize_activity(stats: list[TopicStats]) -> dict[str, float]:
    """{topic_id: 0..1} relative to the busiest topic of the same type, with a floor."""
    peak: dict[str, float] = {}
    for s in stats:
        peak[s.type] = max(peak.get(s.type, 0.0), s.activity)
    return {s.topic_id: min(1.0, s.activity / max(peak[s.type], ACTIVITY_FLOOR)) for s in stats}


def risk_score(norm_activity: float, bus_factor: int, coverage: float, departing: bool = False) -> float:
    score = norm_activity * (1.0 / max(bus_factor, 1)) * (1.0 - coverage)
    return round(min(1.0, score * (DEPARTING_BOOST if departing else 1.0)), 4)


def risk_level(score: float) -> str:
    return "high" if score >= RISK_HIGH else "medium" if score >= RISK_MEDIUM else "low"


def _summary(name: str, stats: TopicStats, coverage: float, people: dict, departing: bool) -> str:
    top = people.get(stats.top_owner)
    who = top.name if top else "nobody"
    share = round(100 * stats.shares.get(stats.top_owner, 0))
    if stats.bus_factor == 1:
        head = f"Only {who} ({share}% of activity) really knows {name}"
    else:
        head = f"{stats.bus_factor} people share knowledge of {name} (led by {who}, {share}%)"
    tail = f"; documentation coverage is {round(100 * coverage)}%"
    if departing:
        date = (top.attributes.get("departure_date") or "") if top else ""
        tail += f". {who} is departing" + (f" on {date[:10]}" if date else "")
    return head + tail + "."


def collect_evidence(
    entity, graph: GraphStore, search, *, limit: int = EVIDENCE_LIMIT, snippet_chars: int = 300,
    types: Optional[list[str]] = None,
) -> list[Citation]:
    """Non-documentation artifacts that discuss the topic, retrieved by search and
    confirmed by a MENTIONS edge in the graph."""
    query = " ".join(dict.fromkeys([entity.name, *entity.aliases[:3]]))
    out: list[Citation] = []
    for hit in search.search(query, top_k=limit * 3, artifact_types=types or _EVIDENCE_TYPES, snippet_chars=snippet_chars):
        if graph.edges(source=hit.artifact_id, target=entity.id):
            out.append(hit)
        if len(out) == limit:
            break
    return out


def analyze_risks(graph: GraphStore, search, as_of: Optional[datetime] = None) -> list[RiskItem]:
    """Ranked knowledge risks, highest first (ties: more activity, then id)."""
    stats = compute_topic_stats(graph, as_of)
    norm = normalize_activity(stats)
    people = {e.id: e for e in graph.entities(EntityType.PERSON)}
    items = []
    for s in stats:
        entity = graph.get_entity(s.topic_id)
        cov = compute_coverage(entity, graph, search)
        top = people.get(s.top_owner)
        departing = bool(top and top.attributes.get("is_departing"))
        score = risk_score(norm[s.topic_id], s.bus_factor, cov.score, departing)

        missing: list[DocType] = []
        if cov.score < COVERAGE_ADEQUATE:
            missing.append(DocType.DECISION_LOG if s.type == EntityType.DECISION else DocType.RUNBOOK)
            if departing:
                missing.append(DocType.HANDOVER)
        candidates = [p for p in s.owners if s.shares[p] >= BUS_FACTOR_MIN_SHARE] or s.owners[:1]

        items.append(RiskItem(
            id=f"risk_{s.topic_id}",
            topic=s.name,
            entity_id=s.topic_id,
            entity_type=s.type,
            summary=_summary(s.name, s, cov.score, people, departing),
            owner_candidates=candidates,
            bus_factor=s.bus_factor,
            coverage_score=cov.score,
            activity_score=round(norm[s.topic_id], 4),
            risk_score=score,
            risk_level=risk_level(score),
            missing_doc_types=missing,
            evidence=collect_evidence(entity, graph, search),
        ))
    return sorted(items, key=lambda r: (-r.risk_score, -r.activity_score, r.id))
