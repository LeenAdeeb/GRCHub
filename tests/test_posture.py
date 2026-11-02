"""Compliance posture rules. Threshold tests use plain numbers (test inputs); the scenario uses real ECC requirement text
with labeled fixture organizations, users and evidence."""
import pytest

from backend import dashboard
from tests.conftest import needs_db
from tests.test_evidence import accept, assess, make


@pytest.mark.parametrize("applicable,assessed,demonstrated,level", [
    (0, 0, 0, "not_assessed"),
    (10, 4, 4, "insufficient"),                 # under half assessed: no label even though the rate looks perfect
    (10, 5, 5, "partially_compliant"),          # 5/10 = 50% demonstrated
    (10, 10, 9, "substantially_compliant"),
    (10, 10, 7, "mostly_compliant"),
    (10, 10, 4, "partially_compliant"),
    (10, 10, 3, "largely_non_compliant"),
    (10, 10, 0, "largely_non_compliant"),
])
def test_classify_thresholds(applicable, assessed, demonstrated, level):
    assert dashboard.classify(applicable, assessed, demonstrated)["level"] == level


def test_unassessed_count_against_the_rate():
    p = dashboard.classify(10, 5, 5)
    assert p["demonstrated_rate"] == 0.5 and p["assessed_share"] == 0.5


@needs_db
def test_posture_scenario(world):
    w = world
    ids = dict(zip(["1-1-1", "1-1-2", "1-1-3", "1-2-1", "2-1-1"], w.approve("1-1-1", "1-1-2", "1-1-3", "1-2-1", "2-1-1")))
    ev = make(w, reqs=[ids["1-1-1"]]).json()
    accept(w, ev["id"])
    assert assess(w, ids["1-1-1"], "compliant", evidence_ids=[ev["id"]]).status_code == 201
    assert assess(w, ids["1-1-2"], "partially_compliant", evidence_ids=[ev["id"]]).status_code == 422   # evidence is not linked to 1-1-2
    assert assess(w, ids["1-1-3"], "non_compliant").status_code == 201
    assert assess(w, ids["1-2-1"], "not_applicable", notes="Out of scope for this fixture organization").status_code == 201
    dash = w.client.get("/dashboard", headers=w.h("a_viewer")).json()
    ecc = next(f for f in dash["frameworks"] if f["framework"] == "NCA ECC")
    p = ecc["posture"]
    assert (p["applicable"], p["assessed"], p["demonstrated"], p["not_applicable"], p["not_assessed"]) == (4, 2, 1, 1, 2)
    assert p["demonstrated_rate"] == 0.25 and p["assessed_share"] == 0.5 and p["level"] == "largely_non_compliant"
    assert p["provisional"] is True and p["scope_complete"] is False
    assert any("awaiting reviewer approval" in r for r in p["reasons"]) and any("not been assessed" in r for r in p["reasons"])
    assert sum(d["approved"] for d in ecc["domains"]) == 5 and len(ecc["domains"]) >= 2
    assert [g["control_id"] for g in dash["top_gaps"]] == ["1-1-3"]
    # SAMA's single control reference is not an assessable control: the framework total excludes it
    sama = next(f for f in dash["frameworks"] if f["framework"] == "SAMA CSF")
    assert sama["awaiting_review"] == 147 and sama["posture"]["level"] == "not_assessed"
    # the evidence expires: the compliant result is no longer demonstrated and is flagged for revalidation
    w.conn.execute("UPDATE evidence SET review_due_date = CURRENT_DATE - 1 WHERE id=%s", (ev["id"],))
    p = next(f for f in w.client.get("/dashboard", headers=w.h("a_viewer")).json()["frameworks"] if f["framework"] == "NCA ECC")["posture"]
    assert p["demonstrated"] == 0 and p["compliant_needs_revalidation"] == 1 and p["demonstrated_rate"] == 0.0
    assert any("expired or been rejected" in r for r in p["reasons"])


@needs_db
def test_due_soon_list_and_org_isolation(world):
    w = world
    from datetime import date, timedelta
    ev = make(w, review_due_date=(date.today() + timedelta(days=10)).isoformat()).json()
    accept(w, ev["id"])
    mine = w.client.get("/dashboard", headers=w.h("a_viewer")).json()
    assert [e["id"] for e in mine["evidence"]["due_soon"]] == [ev["id"]]
    theirs = w.client.get("/dashboard", headers=w.h("b_admin")).json()
    assert theirs["evidence"]["due_soon"] == []
    assert all(f["posture"]["level"] == "not_assessed" for f in theirs["frameworks"])
