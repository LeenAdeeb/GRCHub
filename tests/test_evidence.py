"""Evidence and assessment workflow tests (labeled fixtures; regulatory text is the real extracted text)."""
from datetime import date, timedelta

from tests.conftest import needs_db

pytestmark = needs_db
PDF = b"%PDF-1.4 test fixture"


def make(world, who="a_analyst", name="Fixture evidence", reqs=None, files=None, **form):
    data = {"name": name, **form}
    if reqs:
        data["requirement_ids"] = ",".join(str(r) for r in reqs)
    return world.client.post("/evidence", data=data, files=files if files is not None else {"file": ("p.pdf", PDF)},
                             headers=world.h(who))


def accept(world, eid, judge="a_assessor"):
    h = world.h(judge)
    assert world.client.post(f"/evidence/{eid}/transition", json={"to": "under_review"}, headers=h).status_code == 200
    return world.client.post(f"/evidence/{eid}/transition", json={"to": "accepted"}, headers=h)


def test_create_with_file_and_download_headers(world):
    rid = world.approve("1-1-1")
    r = make(world, reqs=[rid])
    assert r.status_code == 201, r.text
    ev = r.json()
    assert ev["status"] == "submitted" and ev["requirements"][0]["control_id"] == "1-1-1" and "storage_path" not in ev
    d = world.client.get(f"/evidence/{ev['id']}/download", headers=world.h("a_viewer"))
    assert d.content == PDF
    assert d.headers["content-type"] == "application/octet-stream"
    assert d.headers["x-content-type-options"] == "nosniff" and "attachment" in d.headers["content-disposition"]


def test_upload_validation_through_api(world):
    bad_ext = make(world, files={"file": ("x.exe", b"MZ")})
    fake_pdf = make(world, files={"file": ("x.pdf", b"not a pdf")})
    assert bad_ext.status_code == 422 and fake_pdf.status_code == 422
    assert make(world, files={}).json()["status"] == "pending"      # no attachment yet -> pending


def test_only_approved_requirements_can_be_linked(world):
    unapproved = world.req("1-1-2")
    assert make(world, reqs=[unapproved]).status_code == 422


def test_pending_then_attach_then_four_eyes(world):
    rid = world.approve("1-1-1")
    ev = make(world, files={}, reqs=[rid]).json()
    assert ev["status"] == "pending"
    att = world.client.post(f"/evidence/{ev['id']}/attachment", files={"file": ("p.pdf", PDF)}, headers=world.h("a_analyst"))
    assert att.status_code == 200 and att.json()["status"] == "submitted"
    # the org_admin who submitted cannot accept their own submission
    own = make(world, who="a_admin").json()
    h = world.h("a_admin")
    assert world.client.post(f"/evidence/{own['id']}/transition", json={"to": "under_review"}, headers=h).status_code == 200
    r = world.client.post(f"/evidence/{own['id']}/transition", json={"to": "accepted"}, headers=h)
    assert r.status_code == 403
    # a different judge can
    assert accept(world, ev["id"]).json()["status"] == "accepted"


def test_invalid_transitions_and_reject_needs_comment(world):
    ev = make(world).json()
    h = world.h("a_assessor")
    assert world.client.post(f"/evidence/{ev['id']}/transition", json={"to": "accepted"}, headers=h).status_code == 422
    world.client.post(f"/evidence/{ev['id']}/transition", json={"to": "under_review"}, headers=h)
    assert world.client.post(f"/evidence/{ev['id']}/transition", json={"to": "rejected"}, headers=h).status_code == 422
    ok = world.client.post(f"/evidence/{ev['id']}/transition", json={"to": "rejected", "comment": "unreadable scan"}, headers=h)
    assert ok.json()["status"] == "rejected"
    # a rejected item can be replaced and resubmitted
    again = world.client.post(f"/evidence/{ev['id']}/attachment", files={"file": ("p2.pdf", PDF + b"2")}, headers=world.h("a_analyst"))
    assert again.json()["status"] == "submitted"


def test_roles_on_evidence(world):
    assert make(world, who="a_viewer").status_code == 403
    assert make(world, who="a_assessor").status_code == 403
    ev = make(world).json()
    assert world.client.post(f"/evidence/{ev['id']}/transition", json={"to": "under_review"}, headers=world.h("a_analyst")).status_code == 403


def test_org_isolation(world):
    ev = make(world).json()
    for path in (f"/evidence/{ev['id']}", f"/evidence/{ev['id']}/download"):
        assert world.client.get(path, headers=world.h("b_admin")).status_code == 404
    assert world.client.post(f"/evidence/{ev['id']}/transition", json={"to": "under_review"}, headers=world.h("b_assessor")).status_code == 404
    assert world.client.get("/evidence", headers=world.h("b_admin")).json()["total"] == 0
    # a client-supplied org_id is ignored for organization users
    assert world.client.get(f"/evidence?org_id={world.org['a']}", headers=world.h("b_admin")).json()["total"] == 0
    assert world.client.get(f"/evidence?org_id={world.org['a']}", headers=world.h("padmin")).json()["total"] == 1


def test_expiry_is_effective_immediately_and_blocks_accept(world):
    ev = make(world, review_due_date=(date.today() + timedelta(days=30)).isoformat()).json()
    accept(world, ev["id"])
    world.conn.execute("UPDATE evidence SET review_due_date = CURRENT_DATE - 1 WHERE id=%s", (ev["id"],))
    assert world.client.get(f"/evidence/{ev['id']}", headers=world.h("a_viewer")).json()["status"] == "expired"
    assert world.client.get("/evidence?status=expired", headers=world.h("a_viewer")).json()["total"] == 1
    from backend import evidence
    assert evidence.expire_due(world.conn) == 1
    assert world.conn.execute("SELECT status FROM evidence WHERE id=%s", (ev["id"],)).fetchone()[0] == "expired"


# ---------------------------------------------------------------- assessments

def assess(world, rid, result, who="a_assessor", **kw):
    return world.client.post("/assessments", json={"requirement_id": rid, "result": result, **kw}, headers=world.h(who))


def test_assessment_rules(world):
    rid, other = world.approve("1-1-1", "1-1-2")
    unapproved = world.req("1-2-1")
    assert assess(world, unapproved, "non_compliant").status_code == 422
    assert assess(world, rid, "compliant").status_code == 422                       # needs accepted evidence
    assert assess(world, rid, "not_applicable").status_code == 422                  # needs justification
    assert assess(world, rid, "not_applicable", notes="Not in scope: no such system").status_code == 201
    ev = make(world, reqs=[rid]).json()
    assert assess(world, rid, "compliant", evidence_ids=[ev["id"]]).status_code == 422   # not accepted yet
    accept(world, ev["id"])
    assert assess(world, other, "compliant", evidence_ids=[ev["id"]]).status_code == 422  # linked to another requirement
    r = assess(world, rid, "compliant", evidence_ids=[ev["id"]])
    assert r.status_code == 201 and len(r.json()["history"]) == 2
    cur = world.client.get("/assessments", headers=world.h("a_viewer")).json()
    assert cur["total"] == 1 and cur["items"][0]["result"] == "compliant" and cur["items"][0]["evidence_still_valid"]
    assert assess(world, rid, "non_compliant", who="b_assessor").status_code == 201   # other org: separate record
    assert world.client.get("/assessments", headers=world.h("a_viewer")).json()["total"] == 1


def test_maturity_level_validated_against_framework(world):
    ecc = world.approve("1-1-1")
    assert assess(world, ecc, "non_compliant", maturity_level=2).status_code == 422       # ECC has no maturity model
    sama = world.approve("3.1.1-1", framework="SAMA CSF")
    assert assess(world, sama, "non_compliant", maturity_level=7).status_code == 422
    ok = assess(world, sama, "non_compliant", maturity_level=1)
    assert ok.status_code == 201


def test_evidence_becoming_invalid_flags_assessment_and_dashboard(world):
    rid = world.approve("1-1-1")
    ev = make(world, reqs=[rid]).json()
    accept(world, ev["id"])
    assert assess(world, rid, "compliant", evidence_ids=[ev["id"]]).status_code == 201
    dash = world.client.get("/dashboard", headers=world.h("a_viewer")).json()
    ecc = next(f for f in dash["frameworks"] if f["framework"] == "NCA ECC")
    assert ecc["approved_requirements"] == 1 and ecc["compliant"] == 1 and ecc["with_accepted_evidence"] == 1
    assert ecc["awaiting_review"] == 198 and ecc["not_assessed"] == 0
    assert dash["evidence"]["assessments_relying_on_invalid_evidence"] == 0
    world.conn.execute("UPDATE evidence SET review_due_date = CURRENT_DATE - 1 WHERE id=%s", (ev["id"],))
    dash = world.client.get("/dashboard", headers=world.h("a_viewer")).json()
    assert dash["evidence"]["assessments_relying_on_invalid_evidence"] == 1
    assert dash["evidence"]["needs_attention"]["expired"] == 1
    assert world.client.get("/assessments", headers=world.h("a_viewer")).json()["items"][0]["evidence_still_valid"] is False


def test_dashboard_is_org_scoped(world):
    rid = world.approve("1-1-1")
    assess(world, rid, "non_compliant")
    b = world.client.get("/dashboard", headers=world.h("b_admin")).json()
    assert next(f for f in b["frameworks"] if f["framework"] == "NCA ECC")["non_compliant"] == 0
