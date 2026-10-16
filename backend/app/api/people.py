"""GET /people: who holds how much of each topic. Topics, not employee rankings."""

from __future__ import annotations

from fastapi import APIRouter

from app.graph import get_graph
from app.graph.queries import activity_by_topic
from app.models import EntityType, PersonSummary, PersonTopic

router = APIRouter(tags=["people"])

CONCENTRATION_THRESHOLD = 0.7  # one person holding >= 70% of a topic's activity (Plan.md 11.2)


@router.get("/people", response_model=list[PersonSummary])
def people() -> list[PersonSummary]:
    g = get_graph()
    activity = activity_by_topic(g)
    names = {e.id: e for e in g.entities()}
    out = []
    for person in g.entities(EntityType.PERSON):
        topics, concentrated = [], []
        for tid, by_person in activity.items():
            mine = by_person.get(person.id, 0.0)
            if mine <= 0:
                continue
            share = mine / sum(by_person.values())
            t = names[tid]
            topics.append(PersonTopic(entity_id=tid, name=t.name, type=t.type, activity=round(mine, 2), share=round(share, 3)))
            if share >= CONCENTRATION_THRESHOLD:
                concentrated.append(t.name)
        topics.sort(key=lambda x: (-x.share, x.name))
        out.append(PersonSummary(
            id=person.id, name=person.name, role=person.attributes.get("role", ""),
            is_departing=bool(person.attributes.get("is_departing")),
            topics=topics, concentrated_topics=sorted(concentrated),
        ))
    return sorted(out, key=lambda p: p.name)  # alphabetical on purpose: no ranking of people
