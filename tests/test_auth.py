"""Authentication and role tests. Users/orgs are labeled test fixtures (see conftest)."""
from backend import security
from tests.conftest import PASSWORD, needs_db

pytestmark = needs_db


def test_login_and_me(world):
    r = world.client.post("/auth/login", json={"email": "a_admin@example.test", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "org_admin"
    me = world.client.get("/auth/me", headers=world.h("a_admin"))
    assert me.json()["org_id"] == world.org["a"]


def test_wrong_password_and_unknown_user_look_the_same(world):
    a = world.client.post("/auth/login", json={"email": "a_admin@example.test", "password": "wrong-password-1"})
    b = world.client.post("/auth/login", json={"email": "nobody@example.test", "password": "wrong-password-1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_lockout_after_repeated_failures(world):
    for _ in range(security.MAX_FAILED_LOGINS):
        world.client.post("/auth/login", json={"email": "a_viewer@example.test", "password": "wrong-password-1"})
    r = world.client.post("/auth/login", json={"email": "a_viewer@example.test", "password": PASSWORD})
    assert r.status_code == 429


def test_deactivated_user_loses_access_immediately(world):
    headers = world.h("a_viewer")
    assert world.client.get("/dashboard", headers=headers).status_code == 200
    r = world.client.patch(f"/users/{world.user['a_viewer']}/active", json={"is_active": False}, headers=world.h("a_admin"))
    assert r.status_code == 200
    assert world.client.get("/dashboard", headers=headers).status_code == 401
    assert world.client.post("/auth/login", json={"email": "a_viewer@example.test", "password": PASSWORD}).status_code == 401


def test_org_admin_cannot_manage_other_orgs_or_platform_users(world):
    h = world.h("a_admin")
    other = world.client.patch(f"/users/{world.user['b_admin']}/active", json={"is_active": False}, headers=h)
    assert other.status_code == 404
    mk = lambda **kw: world.client.post("/users", json={"email": "n@example.test", "password": PASSWORD, **kw}, headers=h)
    assert mk(role="reviewer").status_code == 403
    assert mk(role="viewer", org_id=world.org["b"]).status_code == 403
    ok = mk(role="viewer")
    assert ok.status_code == 201 and ok.json()["org_id"] == world.org["a"]
    users = world.client.get("/users", headers=h).json()
    assert {u["org_id"] for u in users} == {world.org["a"]}


def test_weak_password_rejected(world):
    r = world.client.post("/users", json={"email": "w@example.test", "password": "short", "role": "viewer"}, headers=world.h("a_admin"))
    assert r.status_code == 422


def test_role_boundaries(world):
    assert world.client.post("/organizations", json={"name": "X"}, headers=world.h("a_admin")).status_code == 403
    assert world.client.post("/organizations", json={"name": "Test Org C"}, headers=world.h("padmin")).status_code == 201
    rid = world.req("1-1-1")
    # org users cannot curate regulatory content
    r = world.client.post(f"/requirements/{rid}/review", json={"decision": "approved"}, headers=world.h("a_admin"))
    assert r.status_code == 403
    # viewers are read-only
    assert world.client.post("/assessments", json={"requirement_id": rid, "result": "non_compliant"}, headers=world.h("a_viewer")).status_code == 403


def test_review_uses_authenticated_name(world):
    rid = world.req("1-1-1")
    r = world.client.post(f"/requirements/{rid}/review", json={"decision": "approved", "note": "checked against PDF"},
                          headers=world.h("rev1"))
    assert r.status_code == 200, r.text
    row = world.conn.execute("SELECT reviewer FROM requirement_reviews WHERE requirement_id=%s", (rid,)).fetchone()
    assert row[0] == "rev1@example.test"


def test_first_run_setup_is_one_shot(world):
    assert world.client.get("/auth/setup").json() == {"needed": False}      # fixture users already exist
    r = world.client.post("/auth/setup", json={"email": "x@example.test", "password": "fixture-password-123"})
    assert r.status_code == 409
    world.conn.execute("TRUNCATE users, organizations RESTART IDENTITY CASCADE")
    assert world.client.get("/auth/setup").json() == {"needed": True}
    assert world.client.post("/auth/setup", json={"email": "x@example.test", "password": "short"}).status_code == 422
    ok = world.client.post("/auth/setup", json={"email": "first@example.test", "password": "fixture-password-123"})
    assert ok.status_code == 201 and ok.json()["role"] == "platform_admin"
    again = world.client.post("/auth/setup", json={"email": "second@example.test", "password": "fixture-password-123"})
    assert again.status_code == 409
    login = world.client.post("/auth/login", json={"email": "first@example.test", "password": "fixture-password-123"})
    assert login.status_code == 200 and login.json()["role"] == "platform_admin"


def test_user_list_shows_organization_name(world):
    rows = world.client.get("/users", headers=world.h("padmin")).json()
    assert any(u["org_name"] == "Test Org A" for u in rows) and any(u["org_name"] is None for u in rows)


def test_platform_admin_builds_organization_and_users_through_the_api(world):
    h = world.h("padmin")
    org = world.client.post("/organizations", json={"name": "Test Org C"}, headers=h).json()["id"]
    assert world.client.post("/organizations", json={"name": "Test Org C"}, headers=h).status_code == 422   # duplicate name
    mk = lambda email, role, **kw: world.client.post("/users", json={"email": email, "password": PASSWORD, "role": role, **kw}, headers=h)
    assert mk("c_admin@example.test", "org_admin", org_id=org).status_code == 201
    assert mk("rev3@example.test", "reviewer").status_code == 201
    assert mk("x@example.test", "assessor").status_code == 422                       # org role needs an organization
    assert mk("y@example.test", "reviewer", org_id=org).status_code == 422           # platform role cannot have one
    listed = world.client.get("/users", headers=h).json()
    assert any(u["email"] == "c_admin@example.test" and u["org_name"] == "Test Org C" for u in listed)


def test_change_password_invalidates_old_token(world):
    old = world.h("a_analyst")
    r = world.client.post("/auth/change-password", json={"current_password": "wrong-password-1", "new_password": "another-password-9"}, headers=old)
    assert r.status_code == 403
    r = world.client.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}, headers=old)
    assert r.status_code == 422
    r = world.client.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": "another-password-9"}, headers=old)
    assert r.status_code == 200
    assert world.client.get("/auth/me", headers=old).status_code == 401              # old token no longer works
    ok = world.client.post("/auth/login", json={"email": "a_analyst@example.test", "password": "another-password-9"})
    assert ok.status_code == 200
    assert world.client.post("/auth/login", json={"email": "a_analyst@example.test", "password": PASSWORD}).status_code == 401


def test_admin_password_reset_rules(world):
    h = world.h("a_admin")
    r = world.client.put(f"/users/{world.user['a_viewer']}/password", json={"new_password": "reset-password-77"}, headers=h)
    assert r.status_code == 200
    assert world.client.post("/auth/login", json={"email": "a_viewer@example.test", "password": "reset-password-77"}).status_code == 200
    assert world.client.put(f"/users/{world.user['b_admin']}/password", json={"new_password": "reset-password-77"}, headers=h).status_code == 404
    assert world.client.put(f"/users/{world.user['rev1']}/password", json={"new_password": "reset-password-77"}, headers=h).status_code == 404
    assert world.client.put(f"/users/{world.user['a_admin']}/password", json={"new_password": "reset-password-77"}, headers=h).status_code == 422
    assert world.client.put(f"/users/{world.user['a_viewer']}/password", json={"new_password": "reset-password-77"}, headers=world.h("a_analyst")).status_code == 403
    assert world.client.put(f"/users/{world.user['b_admin']}/password", json={"new_password": "short"}, headers=world.h("padmin")).status_code == 422
