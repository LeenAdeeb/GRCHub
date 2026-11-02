# Testing GRCHub

GRCHub has two kinds of testing, and you need both:

1. **Automated tests** prove the code behaves consistently and catch regressions.
2. **Human verification** proves the extracted requirements match the official documents. Automated
   tests cannot do this for you: they can show the parser is self-consistent and unchanged, but only a
   person comparing text with the PDF can show the requirement is *correct*.

## 1. Set up once (without Docker; with Docker see the README)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt

# Register the official PDFs (you download them; sources.yaml lists where from).
python -m backend.cli register-local nca-ecc-2-2024-en  <ECC-2024-EN.pdf>  --expect-sha256 <hash>
python -m backend.cli register-local sama-csf-v1-en     <SAMA-CSF.pdf>
python -m backend.cli extract nca-ecc-2-2024-en  storage/originals/nca-ecc-2-2024-en/<sha256>.pdf
python -m backend.cli extract sama-csf-v1-en     storage/originals/sama-csf-v1-en/<sha256>.pdf
```

`extract` prints `VALIDATION PASSED` or a list of issues and exits non-zero on failure.

## 2. Automated test layers

| Layer | File | Needs | What it proves |
|---|---|---|---|
| Data-free unit tests | `tests/test_unit.py` | nothing | Password hashing, tokens, lockout, upload validation, source registry consistency, wrong-file/hash rejection, anonymous and bad-token requests rejected |
| Real-document tests | `tests/test_real_documents.py` | registered PDFs | Parsers pass their own validation; known requirements are exact (text, page, printed page); golden counts (ECC 108 + 91, SAMA 147 + 1, six maturity levels) |
| Database tests | `tests/test_database.py` | scratch PostgreSQL + extracted files | Load matches extraction; reload is idempotent; approvals survive; text drift returns to review; decisions pinned to text hash and audited |
| Auth and roles | `tests/test_auth.py` | same | Login, lockout, deactivation, role boundaries, org admin limited to own organization, reviewer name comes from the login, creating organizations and users through the API, password change (old tokens stop working) and admin password reset |
| Evidence and assessments | `tests/test_evidence.py` | same | Upload checks through the API, only approved requirements linkable, four-eyes, transitions, expiry, download headers, organization isolation, assessment rules, maturity validation, dashboard counts |
| Compliance posture | `tests/test_posture.py` | same | Threshold rules, unassessed counted against the rate, expired evidence not counted as demonstrated, per-domain breakdown, organization isolation |
| Mapping suggestions | `tests/test_suggestions.py` | same (pure-function tests need nothing) | Stemming and ranking, strict validation of model answers, verbatim-quote check, idempotent generation, four-eyes on proposed suggestions, stale detection, caching, call budget, API failure handling. The model APIs (Anthropic, OpenAI, Gemini) are labeled test doubles: request and response shapes are checked against each provider's documented format, but none was called for real |
| Schema upgrade | `tests/test_migrate.py` | same | An older database keeps its data and gains new tables |
| Mappings and changes | `tests/test_mappings_changes.py` | same | Mapping four-eyes and duplicate rules; version comparison and impact on an organization |

The organizations, users, passwords, evidence files and the "second version" used by these tests are
**labeled fixtures that exist only inside the tests** (`tests/conftest.py`). Regulatory text in them is always the
real extracted text. Nothing from the fixtures is loaded into the real application.

Tests whose prerequisites are missing are **skipped, not failed**, so a green run with many skips
means those layers did not run. Check the summary line.

### Run them

```bash
pytest -q tests/test_unit.py                 # anywhere, no setup

# everything, with a throwaway database (the tests DROP and recreate its schema)
docker compose --profile test up -d db_test
export GRCHUB_TEST_DATABASE_URL=postgresql://postgres:test@localhost:5433/grchub_test
pytest -q
docker compose --profile test down
```

The database name must contain `test`; the tests refuse to run against anything else, so they cannot
wipe your real `grchub` database.

### What "passing" means for the parsers

`extract` runs independent checks against the source text, not against the parser's own output:

- ECC: control IDs found by scanning the PDF equal the parsed IDs, in order; numbers are contiguous in
  every subdomain; and a word-count comparison shows no source word was dropped or invented.
- SAMA: subdomains and titles equal the document's own table of contents; every subdomain has a
  principle and objective; every source word is accounted for; ambiguous lines are listed.

### Proving the tests work

Break something on purpose and confirm a test fails. Example: set `MAIN_X0_MAX = 200.0` in
`backend/extraction/sama.py`; `test_sama_validation_passes` and `test_sama_golden_counts` fail.
Revert afterwards. If a parser change alters the golden counts, re-check the extraction against the
PDF before updating the numbers.

## 3. Human verification (the test that matters most)

Every requirement starts as `pending_review`. Nothing should be relied on until it is approved.

```bash
python -m backend.cli review list --flagged            # review these first: low confidence or source notes
python -m backend.cli review show <id>                 # prints the PDF page to check
python -m backend.cli review decide <id> --approve --reviewer "Your Name"
python -m backend.cli review decide <id> --reject  --reviewer "Your Name" --note "what is wrong"
python -m backend.cli review export --out storage/review_export.csv   # work through it beside the PDF
```

Suggested protocol:

1. Review **100% of flagged items**.
2. For each subdomain, check the **first and last** item and everything with a long or multi-part text.
3. Check a **random 10% sample** of the rest. A reproducible sample:
   `SELECT id, control_id FROM requirements WHERE validation_status='pending_review' ORDER BY md5(id::text) LIMIT 35;`
4. Compare against the PDF page shown by `review show` (physical page and printed page). Check wording,
   the ID, and that nothing is merged, split, or cut off.
5. **If you find any error, stop sampling.** Review that whole subdomain, fix the parser, re-extract,
   and re-run the automated tests. Do not approve around a parser bug.
6. Once everything is approved, the audit trail (`requirement_reviews`, `audit_logs`) shows who approved
   what, against which exact text (SHA-256).

Known items to look at by hand: ECC subdomain 1-7 (printed "1.7" in the PDF), SAMA 3.3.12 (an unnumbered
reference list), and SAMA IDs such as `3.1.2-1`, which are derived because the PDF prints none.

## 4. Testing the API manually

Start the server as shown in the README, then open http://127.0.0.1:8000/docs. Call `POST /auth/login`,
copy the `access_token`, and use "Authorize" in the docs page. Everything except `/health` and `/auth/login`
needs the token.

## 5. The web page

```bash
cd frontend && npm install && npm run build      # type-checks (tsc) and builds into frontend/dist
cd .. && uvicorn backend.api.main:app --host 127.0.0.1 --port 8000   # page at http://127.0.0.1:8000/ui/
```

`npm run build` fails on any TypeScript error, so it doubles as the frontend check (it also runs in CI).
Manual checklist (I ran the login, review, maturity and dashboard flow in a headless browser against the real
extracted data in a scratch database; evidence, assessments and mappings screens are covered by the API tests
but not by browser automation):

1. Sign in with a wrong password: a clear error. Sign in correctly: tabs match your role.
2. Reviewer: progress counts match `review stats`; filters and pagination work; each item shows the physical
   and printed PDF page and source notes. **Approve** stays disabled until the "I compared this text with the
   PDF" box is ticked; **Reject** needs a note. The decision is recorded under your login.
3. Maturity model tab: six levels and the narrative sections, each with the page to verify; reviewers can approve them.
4. Platform admin: Users tab adds an organization and users of each role; org admin: Users tab lists and adds only their organization's users; everyone: My password signs you out and the new password works.
5. Organization user: Dashboard, Evidence (add, review, accept, download), Assessments, Mappings (read), Changes.
6. Try breaking the rules: accept your own submission, assess an unapproved requirement, mark compliant without
   accepted evidence. Each must be refused with a clear message.
7. The layout holds at phone width.

The login token is kept in the browser session only and expires after 8 hours.

## 6. When a regulator publishes a new version

Register the new PDF (it is stored under its own SHA-256; the old one is kept), extract, and load it
under a **new version label**. The loader refuses to load a different document over an existing version.
Re-run the real-document tests; golden counts are expected to change, so verify the new numbers against
the PDF before updating them.

## 7. What is not tested yet

Arabic text, browser automation of the evidence/assessment/mapping screens (they type-check and the API behind
them is tested, but a person should click through them), malware scanning (none exists), login lockout across
several server processes (the counter is in memory), load and concurrency, and anything involving real
organizational data. Do not describe these as working in the README or on LinkedIn.

## 8. Checking the AI suggestions yourself

The automated tests use a fake model, so they cannot tell you whether Claude's judgments are good. To judge quality,
use a handful of mappings you already know are right: run *Find candidates*, check that the expected pair appears
in the list (if the free step misses it, the pair never reaches the AI, so also try a lower similarity threshold
with the command line `--min-score 0.1`), run the AI step, and read the highlighted quotes against both texts. Treat
the AI's relationship and rationale as a draft; the reviewer's justification is the record.
