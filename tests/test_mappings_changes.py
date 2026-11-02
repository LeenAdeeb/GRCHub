"""Mapping workflow and version-change tests. The 'second version' is a LABELED TEST FIXTURE: the real extracted
ECC rows with one text altered, one control removed and one control added, loaded under the label 'TEST-v2'.
It exists only inside these tests."""
import json
from datetime import datetime, timezone

from backend.database.loader import load_source
from tests.conftest import ECC_JSONL, needs_db
from tests.test_evidence import PDF, accept, assess, make

pytestmark = needs_db


def mk_mapping(world, who, a, b, **kw):
    body = {"source_req_id": a, "target_req_id": b, "relationship": "partial_overlap",
            "justification": "Reviewer-written justification for the test mapping.", **kw}
    return world.client.post("/mappings", json=body, headers=world.h(who))


def test_mapping_four_eyes_and_rules(world):
    a = world.approve("1-1-1")
    b = world.approve("3.1.1-1", framework="SAMA CSF")
    unapproved = world.req("1-1-2")
    assert mk_mapping(world, "rev1", a, unapproved).status_code == 422
    assert mk_mapping(world, "rev1", a, a).status_code == 422
    assert mk_mapping(world, "rev1", a, b, justification="too short").status_code == 422
    assert mk_mapping(world, "a_admin", a, b).status_code == 403                    # org users cannot curate
    m = mk_mapping(world, "rev1", a, b)
    assert m.status_code == 201
    mid = m.json()["id"]
    assert mk_mapping(world, "rev2", b, a).status_code == 422                         # duplicate, reverse direction
    own = world.client.post(f"/mappings/{mid}/decision", json={"decision": "approved"}, headers=world.h("rev1"))
    assert own.status_code == 403
    assert world.client.post(f"/mappings/{mid}/decision", json={"decision": "rejected"}, headers=world.h("rev2")).status_code == 422
    ok = world.client.post(f"/mappings/{mid}/decision", json={"decision": "approved"}, headers=world.h("rev2"))
    assert ok.status_code == 200
    assert world.client.post(f"/mappings/{mid}/decision", json={"decision": "approved"}, headers=world.h("padmin")).status_code == 422
    lst = world.client.get(f"/mappings?requirement_id={b}&status=approved", headers=world.h("a_viewer")).json()
    assert lst["total"] == 1 and lst["items"][0]["source_framework"] == "NCA ECC" and lst["items"][0]["decided_by"] == "rev2@example.test"


def load_v2(world, tmp_path):
    rows = [json.loads(l) for l in ECC_JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    fake_sha = "f" * 64
    out = []
    for r in rows:
        if r["control_id"] == "1-1-3":
            continue                                                     # removed in v2
        r = dict(r, source_document_sha256=fake_sha)
        if r["control_id"] == "1-1-1":
            r["original_text"] += " [TEST FIXTURE EDIT]"                 # modified in v2
        out.append(r)
    out.append(dict(out[0], control_id="9-9-9", printed_id="9-9-9", original_text="Fixture-only added control.",
                    parent_control_id=None))
    path = tmp_path / "v2.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in out), encoding="utf-8")
    store = tmp_path / "store" / "originals" / "fixture-ecc-v2"
    store.mkdir(parents=True)
    (store / "history.jsonl").write_text(json.dumps({"sha256": fake_sha, "retrieved_from": "fixture",
                                                      "stored_path": "fixture",
                                                      "retrieved_at": datetime.now(timezone.utc).isoformat()}))
    src = {"id": "fixture-ecc-v2", "framework": "NCA ECC", "version": "TEST-v2", "language": "en"}
    load_source(world.conn, src, path, tmp_path / "store")


def test_change_detection_and_impact(world, tmp_path):
    rid = world.approve("1-1-1")
    ev = make(world, reqs=[rid]).json()
    accept(world, ev["id"])
    assert assess(world, rid, "compliant", evidence_ids=[ev["id"]]).status_code == 201
    load_v2(world, tmp_path)
    r = world.client.get("/changes?framework=NCA ECC&from_version=ECC-2:2024&to_version=TEST-v2", headers=world.h("a_viewer"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["control_id"] for x in d["added"]] == ["9-9-9"]
    assert [x["control_id"] for x in d["removed"]] == ["1-1-3"]
    assert [x["control_id"] for x in d["modified"]] == ["1-1-1"]
    assert d["unchanged"] == 199 - 2
    assert [a["control_id"] for a in d["impact"]["assessments_to_redo"]] == ["1-1-1"]
    assert [e["evidence_id"] for e in d["impact"]["evidence_to_revalidate"]] == [ev["id"]]
    # another organization sees no impact from the same change
    other = world.client.get("/changes?framework=NCA ECC&from_version=ECC-2:2024&to_version=TEST-v2", headers=world.h("b_admin")).json()
    assert other["impact"]["assessments_to_redo"] == [] and other["impact"]["evidence_to_revalidate"] == []
    # old assessment stays tied to the old framework version
    cur = world.client.get("/assessments", headers=world.h("a_viewer")).json()["items"][0]
    assert cur["version"] == "ECC-2:2024"


def test_changes_errors(world):
    h = world.h("a_viewer")
    assert world.client.get("/changes?framework=NCA ECC&from_version=x&to_version=y", headers=h).status_code == 404
    assert world.client.get("/changes?framework=NCA ECC&from_version=ECC-2:2024&to_version=ECC-2:2024", headers=h).status_code == 422
