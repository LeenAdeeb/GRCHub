# GRCHub

An open-source GRC (governance, risk and compliance) platform for **Saudi cybersecurity regulations**. It turns the
official NCA Essential Cybersecurity Controls (ECC-2:2024) and SAMA Cyber Security Framework (v1.0) PDFs into
traceable, human-verified requirements, then lets an organization collect evidence, assess compliance, map controls
across frameworks, and see its compliance posture.

> **Early-stage software (v0.3).** Not affiliated with, endorsed by, or approved by NCA, SAMA or any authority. Not legal
> or compliance advice, and not a certification or audit tool. It has not had an independent security review; do not
> use it with sensitive production data until you have read [docs/SECURITY.md](docs/SECURITY.md).

## What makes it different

- **Verified source, not scraped text.** Every requirement is extracted from the official PDF, checked to be the right
  document (identity phrases and SHA-256), and validated against the document itself (ID sequences, table of
  contents, and a word-conservation check proving nothing was dropped or invented). Each requirement records its
  framework, version, control ID, physical and printed page, and document hash.
- **Nothing is trusted until a person reviews it.** All extractions start as `pending_review`. A reviewer approves each
  one against the PDF page; the decision is pinned to the hash of the exact text reviewed, and a changed text returns to review.
- **Four-eyes controls.** Evidence cannot be accepted by whoever submitted it; a mapping cannot be approved by whoever
  proposed it; AI suggestions are never mappings until two people agree.
- **Honest compliance posture.** Compliance counts only when an assessor recorded it and its evidence is still accepted.
  Unassessed requirements count against the rate, and results stay *provisional* until all requirements are reviewed and assessed.
- **No synthetic regulatory data.** The automated tests use real extracted text; the only made-up data (test
  organizations, users, evidence files) exists inside the tests and is clearly labeled.

## What is not included

- **The regulatory documents and extracted text are not in this repository** and must not be committed. Download the
  official PDFs yourself from NCA and SAMA, and check their terms of use before redistributing any of their content.
  "NCA", "SAMA" and the framework names belong to their owners and are used here only to identify the documents.
- Not implemented yet: malware scanning of uploads, MFA/SSO, Arabic text, other frameworks. See the limits in
  [docs/SECURITY.md](docs/SECURITY.md).

## Features

Reviewer workflow with audit trail (web page and command line) · login with six roles (JWT) · evidence management with
status workflow and expiry · assessments with SAMA maturity levels · manual and AI-assisted cross-framework mappings ·
compliance dashboard with posture per framework and domain · regulatory version comparison with impact on an
organization · user and organization management in the web page · Docker one-command setup · automatic schema upgrades.

Stack: Python 3.12, FastAPI, PostgreSQL 16, PyMuPDF, React + TypeScript.

### AI-assisted mapping suggestions

In **Mappings**, a reviewer picks two frameworks and presses *Find candidates*. This is free, runs locally and takes
milliseconds: it ranks the most similar approved requirements (word and word-pair similarity), keeping only a few
candidates per requirement instead of comparing every pair. A platform admin can then press *Estimate AI cost* and
*Run AI refinement*. This needs an AI key in `.env` (Anthropic, OpenAI or Gemini, one or several; the admin chooses per
run) and sends **only requirement text** to the chosen provider. The model is asked once per requirement about all its
candidates and must quote a phrase from each text; answers are cached so a pair is never paid for twice, and a quote
that is not found word for word in the real text is flagged as unverified. A suggestion is never a mapping: a
reviewer proposes it with a justification in their own words, and a second reviewer approves it. Command line:
`python -m backend.cli suggest-mappings "NCA ECC" "SAMA CSF" --ai --provider openai --dry-run`.
For OpenAI and Gemini you must also name the model (`GRCHUB_OPENAI_MODEL`, `GRCHUB_GEMINI_MODEL`); Anthropic has a
default. The provider integrations are tested against documented request formats, not against live services.

## Principles

- No synthetic regulatory data. Every requirement comes from an official document and is traceable
  (framework, version, control ID, physical and printed page, document SHA-256).
- Original text is never modified. All extractions start as `pending_review`.
- Registration refuses a file whose first pages do not match the source's identity phrases, and can
  check an expected SHA-256.
- Each parser is validated against its own source: ID sequences, TOC match, and a word-conservation
  check proving nothing was dropped or invented.
- Source anomalies are preserved and flagged in `parse_notes` (for example, ECC subdomain 1-7 is
  printed as "1.7" in the PDF). SAMA consideration IDs (e.g. `3.1.2-1`) are derived, because the PDF
  prints no ID for them; they are marked `id_is_derived`.

## Quick start (Docker, the only thing to install)

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and start it. Then, in PowerShell inside
the project folder:

```powershell
copy .env.example .env          # open .env and fill in POSTGRES_PASSWORD and GRCHUB_JWT_SECRET
# Download the official PDFs yourself and put them in the "pdfs" folder (NCA ECC-2:2024 EN, SAMA CSF v1.0 EN)
docker compose up -d --build
docker compose exec app python -m backend.cli import-pdf nca-ecc-2-2024-en /pdfs/<ECC file>.pdf
docker compose exec app python -m backend.cli import-pdf sama-csf-v1-en /pdfs/<SAMA file>.pdf
docker compose exec app python -m backend.cli org-create "Your organization"
docker compose exec -it app python -m backend.cli user-create you@example.com --role platform_admin
docker compose exec -it app python -m backend.cli user-create admin@example.com --role org_admin --org-id 1
```

The command line is only for this first start: the first organization and its administrators. Everything after that
is done in the web page. Sign in and use **Users**: a platform admin adds organizations and users of any role
(reviewers, org admins, analysts, assessors, viewers), disables accounts and resets passwords; an org admin does
the same for their own organization. Everyone can change their own password under **My password**.

Open http://127.0.0.1:8000/ui/. `import-pdf` checks that the file is the right document, extracts, validates,
and loads it; if anything fails it stops before touching the database. Stop with `docker compose down` (your data
is kept); `docker compose down -v` deletes it.

Without Docker, see the manual steps in [docs/TESTING.md](docs/TESTING.md) (Python 3.12, Node 22, PostgreSQL 16).

## Testing

See [docs/TESTING.md](docs/TESTING.md): automated test layers, how to run them against a throwaway
database, and the human verification protocol that checks extracted requirements against the PDFs.

### API and web page

```bash
export DATABASE_URL=...
export GRCHUB_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
python -m backend.cli org-create "Your organization"                         # first organization only
python -m backend.cli user-create you@example.com --role platform_admin       # first admin; password: prompt or GRCHUB_NEW_PASSWORD
# every other organization and user is added in the web page (Users tab)
(cd frontend && npm install && npm run build)   # once, builds the web page
uvicorn backend.api.main:app --host 127.0.0.1 --port 8000
# web page: http://127.0.0.1:8000/ui/   API docs: http://127.0.0.1:8000/docs
```

Roles: `platform_admin` and `reviewer` curate the shared regulatory content and mappings; `org_admin`,
`analyst`, `assessor` and `viewer` work on one organization's evidence and assessments only. Passwords need
12+ characters. Evidence submitted by someone cannot be accepted by the same person, and a mapping cannot be
approved by its creator.

### Database and review

```bash
export DATABASE_URL=postgresql://grchub:<password>@localhost:5432/grchub
python -m backend.cli db-init                 # skip if you started the Docker Compose database
python -m backend.cli db-load nca-ecc-2-2024-en
python -m backend.cli db-load sama-csf-v1-en
python -m backend.cli review stats
python -m backend.cli review list --flagged   # low-confidence or noted items first
python -m backend.cli review show <id>        # shows the PDF page to check against
python -m backend.cli review decide <id>... --approve --reviewer "Your Name"   # or use the web page
python -m backend.cli review decide <id> --reject --reviewer "Your Name" --note "why"
python -m backend.cli review export --out storage/review_export.csv
```

Re-loading is safe: review decisions are kept unless a requirement's text changed, in which case it
returns to review. Each decision is stored with the SHA-256 of the exact text reviewed. A changed
document must be loaded as a new framework version, never over an existing one.

Database tests need a scratch database: `GRCHUB_TEST_DATABASE_URL=postgresql://.../grchub_test pytest`
(the schema is dropped and recreated, so the name must contain `test`).

`extract` writes `storage/extracted/<source>.jsonl` and exits non-zero if validation fails.
Database: `POSTGRES_PASSWORD=... docker compose up -d`.

## Contributing and license

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately
(see [docs/SECURITY.md](docs/SECURITY.md)). Code licensed under the [Apache License 2.0](LICENSE); the regulatory
documents are not covered by this license and are not distributed here.
