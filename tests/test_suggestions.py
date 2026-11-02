"""AI-assisted mapping suggestions. Regulatory text is the real extracted ECC/SAMA text. The model API is replaced by a
LABELED TEST DOUBLE (an in-process fake transport): these tests never call a real model and prove nothing about
the quality of its answers, only that the plumbing, validation, caching and review rules hold."""
import json
import re

import httpx
import pytest

from backend import llm, suggestions
from tests.conftest import needs_db

# ---------------------------------------------------------------- pure functions (no database)


def test_stemming_joins_word_forms():
    s = suggestions._stem
    assert s("identify") == s("identified") == s("identification") == s("identifies")
    assert s("manage") == s("managed") == s("management")
    assert s("policy") == s("policies")
    assert suggestions.tokens("cyber security") == suggestions.tokens("cybersecurity")


def test_candidates_are_ranked_and_thresholded():
    src = {1: "The backup process must be tested regularly"}          # toy strings, test-only
    tgt = {10: "Backup process testing", 11: "Physical access to the data center", 12: "Encryption keys"}
    found = suggestions.candidates(src, tgt, top_k=2, min_score=0.1)
    assert [t for t, _ in found[1]] == [10]
    assert suggestions.candidates({1: "zzz qqq"}, tgt) == {}


def test_quote_must_appear_verbatim():
    text = "The cybersecurity  strategy shall be\n approved by the head of the entity."
    assert suggestions.quote_found("strategy shall be approved", text)
    assert not suggestions.quote_found("strategy must be approved", text)
    assert not suggestions.quote_found("short", "short text")            # too short to prove anything


def test_parse_answer_is_strict():
    labels = {"C1": {"suggestion_id": 5, "text": "access shall be reviewed every quarter"},
              "C2": {"suggestion_id": 6, "text": "unrelated"}}
    src = "user access rights must be reviewed periodically"
    raw = "Sure!\n" + json.dumps({"results": [
        {"candidate": "C1", "relationship": "partial_overlap", "rationale": "x",
         "source_quote": "access rights must be reviewed", "candidate_quote": "access shall be reviewed"},
        {"candidate": "C2", "relationship": "equivalent", "rationale": "y",
         "source_quote": "invented phrase here", "candidate_quote": "also invented"},
        {"candidate": "C9", "relationship": "equivalent"},               # unknown label: dropped
        {"candidate": "C1", "relationship": "related"},                  # duplicate: ignored
    ]}) + "\nbye"
    out = suggestions.parse_answer(raw, labels, src)
    assert out[5]["verified"] is True and out[6]["verified"] is False and set(out) == {5, 6}
    with pytest.raises(ValueError):
        suggestions.parse_answer("no json here", labels, src)
    bad = suggestions.parse_answer(json.dumps({"results": [{"candidate": "C1", "relationship": "identical"}]}), labels, src)
    assert bad == {}


# ---------------------------------------------------------------- database-backed

ECC = ("2-2-1", "1-1-1", "2-1-1")
SAMA = ("3.3.5-1", "3.3.5-2", "3.1.2-1", "3.3.3-1")


@pytest.fixture()
def ready(world):
    world.approve(*ECC)
    world.approve(*SAMA, framework="SAMA CSF")
    return world


@needs_db
def test_generate_is_idempotent_and_respects_existing_mappings(ready):
    w = ready
    r = suggestions.generate(w.conn, "t", "NCA ECC", "SAMA CSF")
    assert r["source_requirements"] == 3 and r["target_requirements"] == 4 and r["created"] >= 3
    again = suggestions.generate(w.conn, "t", "NCA ECC", "SAMA CSF")
    assert again["created"] == 0 and again["skipped_existing"] == r["created"]
    top = suggestions.list_suggestions(w.conn)["items"][0]
    assert top["source_framework"] == "NCA ECC" and top["stale"] is False and top["llm_model"] is None
    # a pair already mapped by hand (in either direction) is not offered
    w.conn.execute("DELETE FROM mapping_suggestions")
    a, b = w.req("2-2-1"), w.req("3.3.5-1", "SAMA CSF")
    w.client.post("/mappings", json={"source_req_id": b, "target_req_id": a, "relationship": "partial_overlap",
                                     "justification": "Reviewer-written justification for the test."}, headers=w.h("rev1"))
    suggestions.generate(w.conn, "t", "NCA ECC", "SAMA CSF")
    pairs = {(x["source_req_id"], x["target_req_id"]) for x in suggestions.list_suggestions(w.conn)["items"]}
    assert (a, b) not in pairs and (b, a) not in pairs


@needs_db
def test_unapproved_requirements_excluded_by_default(world):
    world.approve("2-2-1")                                              # the SAMA side has nothing approved
    r = world.client.post("/suggestions/generate", json={"source_framework": "NCA ECC", "target_framework": "SAMA CSF"},
                          headers=world.h("rev1"))
    assert r.status_code == 200 and r.json()["created"] == 0
    r = world.client.post("/suggestions/generate", json={"source_framework": "NCA ECC", "target_framework": "SAMA CSF",
                                                         "only_approved": False}, headers=world.h("rev1"))
    assert r.json()["created"] > 0
    assert world.client.post("/suggestions/generate", json={"source_framework": "NCA ECC", "target_framework": "NCA ECC"},
                             headers=world.h("rev1")).status_code == 422
    assert world.client.post("/suggestions/generate", json={"source_framework": "Nope", "target_framework": "SAMA CSF",
                                                            "only_approved": False}, headers=world.h("rev1")).status_code == 404


@needs_db
def test_propose_goes_through_four_eyes(ready):
    w = ready
    w.client.post("/suggestions/generate", json={"source_framework": "NCA ECC", "target_framework": "SAMA CSF"}, headers=w.h("rev1"))
    item = w.client.get("/suggestions", headers=w.h("rev1")).json()["items"][0]
    sid = item["id"]
    short = w.client.post(f"/suggestions/{sid}/propose", json={"relationship": "partial_overlap", "justification": "too short"}, headers=w.h("rev1"))
    assert short.status_code == 422
    ok = w.client.post(f"/suggestions/{sid}/propose", json={"relationship": "partial_overlap",
                       "justification": "Both require the policy to be defined and approved."}, headers=w.h("rev1"))
    assert ok.status_code == 201 and ok.json()["approval_status"] == "pending"
    row = w.conn.execute("SELECT method, approval_status, created_by FROM control_mappings WHERE id=%s", (ok.json()["mapping_id"],)).fetchone()
    assert row[0] == "rule" and row[1] == "pending" and row[2] == w.user["rev1"]
    assert w.client.post(f"/suggestions/{sid}/propose", json={"relationship": "related", "justification": "x" * 30}, headers=w.h("rev1")).status_code == 404
    mid = ok.json()["mapping_id"]
    assert w.client.post(f"/mappings/{mid}/decision", json={"decision": "approved"}, headers=w.h("rev1")).status_code == 403
    assert w.client.post(f"/mappings/{mid}/decision", json={"decision": "approved"}, headers=w.h("rev2")).status_code == 200
    assert w.client.get("/suggestions?status=proposed", headers=w.h("rev1")).json()["total"] == 1


@needs_db
def test_dismiss_and_roles(ready):
    w = ready
    w.client.post("/suggestions/generate", json={"source_framework": "NCA ECC", "target_framework": "SAMA CSF"}, headers=w.h("rev1"))
    sid = w.client.get("/suggestions", headers=w.h("rev1")).json()["items"][0]["id"]
    assert w.client.get("/suggestions", headers=w.h("a_admin")).status_code == 403
    assert w.client.post(f"/suggestions/{sid}/dismiss", json={}, headers=w.h("a_admin")).status_code == 403
    assert w.client.post(f"/suggestions/{sid}/dismiss", json={"note": "different obligations"}, headers=w.h("rev1")).status_code == 200
    assert w.client.post(f"/suggestions/{sid}/dismiss", json={}, headers=w.h("rev1")).status_code == 404
    assert w.client.get("/suggestions?status=dismissed", headers=w.h("rev1")).json()["total"] == 1


@needs_db
def test_changed_requirement_text_marks_suggestion_stale(ready):
    w = ready
    suggestions.generate(w.conn, "t", "NCA ECC", "SAMA CSF")
    w.conn.execute("UPDATE requirements SET original_text = original_text || ' (test edit)' WHERE id=%s", (w.req("2-2-1"),))
    items = suggestions.list_suggestions(w.conn, limit=100)["items"]
    assert any(i["stale"] for i in items) and any(not i["stale"] for i in items)
    assert suggestions.estimate(w.conn, "NCA ECC", "SAMA CSF")["calls"] < len({i["source_req_id"] for i in items})  # stale pairs get no model call


@needs_db
def test_ai_run_unavailable_without_key_and_for_reviewers(ready, monkeypatch):
    w = ready
    for k in llm.KEY_ENV.values():
        monkeypatch.delenv(k, raising=False)
    body = {"source_framework": "NCA ECC", "target_framework": "SAMA CSF"}
    assert w.client.get("/suggestions/config", headers=w.h("rev1")).json()["llm_configured"] is False
    assert w.client.post("/suggestions/llm/run", json=body, headers=w.h("rev1")).status_code == 403
    assert w.client.post("/suggestions/llm/run", json=body, headers=w.h("padmin")).status_code == 503


# ---------------------------------------------------------------- refinement with a fake model API

def fake_transport(calls: list, mode: str = "ok"):
    """Test double for the model API. In 'ok' mode it quotes a real phrase for C1 and an invented one for C2."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if mode == "down":
            return httpx.Response(500, json={"error": "boom"})
        user = json.loads(request.content)["messages"][0]["content"]
        src = re.search(r"<source[^>]*>(.*?)</source>", user, re.S).group(1)
        cands = dict(re.findall(r"<candidate label=\"(C\d+)\">(.*?)</candidate>", user, re.S))
        results = []
        for i, (label, text) in enumerate(cands.items()):
            quote = (src[20:60], text[10:50]) if i == 0 else ("a phrase the model invented", "another invented one")
            results.append({"candidate": label, "relationship": "partial_overlap", "rationale": "test double",
                            "source_quote": quote[0], "candidate_quote": quote[1]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps({"results": results})}]})
    return httpx.MockTransport(handler)


@needs_db
def test_refine_verifies_quotes_caches_and_orders(ready, monkeypatch):
    w = ready
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    suggestions.generate(w.conn, "t", "NCA ECC", "SAMA CSF")
    est = suggestions.estimate(w.conn, "NCA ECC", "SAMA CSF")
    assert est["calls"] >= 2 and est["calls"] < est["pairs"] and est["configured"]
    calls: list = []
    client = llm.Client(transport=fake_transport(calls), backoff=0)
    stats = suggestions.refine(w.conn, "t", "NCA ECC", "SAMA CSF", client, max_calls=50, workers=2)
    assert stats["calls"] == est["calls"] == len(calls) and stats["failed"] == 0 and stats["refined"] == est["pairs"]
    assert stats["unverified"] >= 1                                        # invented quotes are flagged, not trusted
    rows = w.conn.execute("SELECT llm_verified, llm_model, prompt_version FROM mapping_suggestions").fetchall()
    assert all(r[1] == client.label and r[2] == suggestions.PROMPT_VERSION for r in rows)
    first = suggestions.list_suggestions(w.conn)["items"][0]
    assert first["llm_verified"] is True                                   # verified answers sort first
    # cached: a second run asks the model nothing
    before = len(calls)
    again = suggestions.refine(w.conn, "t", "NCA ECC", "SAMA CSF", client)
    assert again["calls"] == 0 and len(calls) == before
    # accepting an AI-checked suggestion records method 'llm'
    mid = suggestions.propose(w.conn, w.user["rev1"], "rev1", first["id"], "partial_overlap", "Reviewer's own written justification.")
    assert w.conn.execute("SELECT method FROM control_mappings WHERE id=%s", (mid,)).fetchone()[0] == "llm"


@needs_db
def test_refine_respects_budget_and_survives_api_failure(ready, monkeypatch):
    w = ready
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    suggestions.generate(w.conn, "t", "NCA ECC", "SAMA CSF")
    total = suggestions.estimate(w.conn, "NCA ECC", "SAMA CSF")["calls"]
    calls: list = []
    client = llm.Client(transport=fake_transport(calls), backoff=0)
    stats = suggestions.refine(w.conn, "t", "NCA ECC", "SAMA CSF", client, max_calls=1)
    assert stats["calls"] == 1 and stats["remaining_after_budget"] == total - 1
    down = llm.Client(transport=fake_transport([], "down"), retries=1, backoff=0)
    stats = suggestions.refine(w.conn, "t", "NCA ECC", "SAMA CSF", down)
    assert stats["failed"] == total - 1 and stats["refined"] == 0          # nothing half-written; still pending
    assert suggestions.estimate(w.conn, "NCA ECC", "SAMA CSF")["calls"] == total - 1


def test_llm_client_needs_key_and_never_retries_auth_errors(monkeypatch):
    for k in llm.KEY_ENV.values():
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(llm.LLMError):
        llm.Client()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    hits: list = []
    c = llm.Client(transport=httpx.MockTransport(lambda r: (hits.append(1), httpx.Response(401, json={}))[1]), backoff=0)
    with pytest.raises(llm.LLMError, match="401"):
        c.complete("s", "u")
    assert len(hits) == 1


# ---------------------------------------------------------------- several providers (request/response shapes only; test doubles)

def _provider_env(monkeypatch, **keys):
    for k in llm.KEY_ENV.values():
        monkeypatch.delenv(k, raising=False)
    for k in ("GRCHUB_LLM_PROVIDER", "GRCHUB_LLM_MODEL", "GRCHUB_OPENAI_MODEL", "GRCHUB_GEMINI_MODEL", "GRCHUB_ANTHROPIC_MODEL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in keys.items():
        monkeypatch.setenv(k, v)


def test_provider_selection_and_models(monkeypatch):
    _provider_env(monkeypatch)
    assert llm.available() == [] and llm.default_provider() is None and not llm.configured()
    _provider_env(monkeypatch, OPENAI_API_KEY="k", GEMINI_API_KEY="k")
    assert llm.available() == ["openai", "gemini"] and llm.default_provider() == "openai"
    with pytest.raises(llm.LLMError, match="GRCHUB_OPENAI_MODEL"):         # no guessed default for non-Anthropic models
        llm.Client("openai")
    monkeypatch.setenv("GRCHUB_GEMINI_MODEL", "some-gemini-model")
    assert llm.Client("gemini").label == "gemini/some-gemini-model"
    monkeypatch.setenv("GRCHUB_LLM_PROVIDER", "gemini")
    assert llm.default_provider() == "gemini"
    _provider_env(monkeypatch, ANTHROPIC_API_KEY="k")
    assert llm.label() == f"anthropic/{llm.DEFAULT_MODEL['anthropic']}"
    with pytest.raises(llm.LLMError):
        llm.Client("openai")                                                # no key for that provider


def _capture(responder):
    seen = []
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.headers), json.loads(request.content)))
        return responder(len(seen))
    return seen, httpx.MockTransport(handler)


def test_openai_request_and_response_shape(monkeypatch):
    _provider_env(monkeypatch, OPENAI_API_KEY="sk-test", GRCHUB_OPENAI_MODEL="m1")
    seen, tr = _capture(lambda n: httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]}))
    c = llm.Client("openai", transport=tr, backoff=0)
    assert c.complete("SYS", "USER") == "hello"
    path, headers, body = seen[0]
    assert path == "/v1/chat/completions" and headers["authorization"] == "Bearer sk-test"
    assert body["model"] == "m1" and body["messages"][0] == {"role": "system", "content": "SYS"} and body["temperature"] == 0


def test_openai_adapts_to_models_that_renamed_parameters(monkeypatch):
    _provider_env(monkeypatch, OPENAI_API_KEY="sk-test", GRCHUB_OPENAI_MODEL="m1")
    def responder(n):
        if n == 1:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens'. 'temperature' does not support 0"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    seen, tr = _capture(responder)
    assert llm.Client("openai", transport=tr, backoff=0).complete("s", "u") == "ok"
    assert "max_tokens" in seen[0][2] and "max_completion_tokens" in seen[1][2] and "temperature" not in seen[1][2]


def test_gemini_request_and_response_shape(monkeypatch):
    _provider_env(monkeypatch, GEMINI_API_KEY="g-test", GRCHUB_GEMINI_MODEL="gm")
    seen, tr = _capture(lambda n: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "he"}, {"text": "llo"}]}}]}))
    c = llm.Client("gemini", transport=tr, backoff=0)
    assert c.complete("SYS", "USER") == "hello"
    path, headers, body = seen[0]
    assert path == "/v1beta/models/gm:generateContent" and headers["x-goog-api-key"] == "g-test"
    assert body["systemInstruction"]["parts"][0]["text"] == "SYS" and body["contents"][0]["parts"][0]["text"] == "USER"


def test_anthropic_request_shape_and_base_url_override(monkeypatch):
    _provider_env(monkeypatch, ANTHROPIC_API_KEY="a-test")
    seen, tr = _capture(lambda n: httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]}))
    assert llm.Client(transport=tr, backoff=0).complete("S", "U") == "hi"
    path, headers, body = seen[0]
    assert path == "/v1/messages" and headers["x-api-key"] == "a-test" and body["system"] == "S"
    monkeypatch.setenv("GRCHUB_ANTHROPIC_BASE_URL", "http://127.0.0.1:9/")
    assert str(llm.Client()._http.base_url) == "http://127.0.0.1:9"


@needs_db
def test_api_reports_providers_and_rejects_bad_choice(ready, monkeypatch):
    w = ready
    _provider_env(monkeypatch, OPENAI_API_KEY="k")
    cfg = w.client.get("/suggestions/config", headers=w.h("rev1")).json()
    assert cfg["providers"] == [{"provider": "openai", "model": None, "ready": False}] and "k" not in json.dumps(cfg)
    body = {"source_framework": "NCA ECC", "target_framework": "SAMA CSF"}
    assert w.client.post("/suggestions/llm/run", json=body, headers=w.h("padmin")).status_code == 422      # model not named
    assert w.client.post("/suggestions/llm/run", json={**body, "provider": "gemini"}, headers=w.h("padmin")).status_code == 422
