# Contributing

Thanks for helping. A few rules keep the project trustworthy:

1. **Never commit regulatory documents or text extracted from them** (`*.pdf`, `storage/`). Tests compare SHA-256 digests,
   not text. If you add an extraction check, add the digest, not the sentence.
2. **No synthetic regulatory data.** Test fixtures (organizations, users, evidence files, a second framework version) are
   allowed only inside tests and must be clearly labeled as fixtures.
3. **Run the tests.** `pytest -q tests/test_unit.py` needs nothing. The full suite needs a scratch PostgreSQL database whose
   name contains `test` and the registered PDFs (see [docs/TESTING.md](docs/TESTING.md)).
4. **Frontend:** `cd frontend && npm ci && npm run build` must pass (it type-checks).
5. Keep human-in-the-loop rules intact: four-eyes approval, review decisions pinned to text hashes, and AI output that is only
   ever a suggestion.

Describe what you changed and how you checked it in the pull request.
