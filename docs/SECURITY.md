# Security notes

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private vulnerability reporting on this repository
(Security tab, "Report a vulnerability"). Include steps to reproduce and the version. There is no bug bounty.

What is protected, and what is not. Read this before putting real organizational data into GRCHub.

## Protected

- **Authentication**: scrypt password hashes, minimum 12-character passwords, signed JWT (HS256, 8 hours) using
  `GRCHUB_JWT_SECRET` (32+ characters; the API refuses to issue tokens without it). Unknown users and wrong
  passwords give the same answer and take similar time.
- **Lockout**: 5 failed logins per email in 15 minutes returns 429.
- **Current role on every request**: the role, organization and active flag are re-read from the database, so a
  deactivated user or a changed role takes effect immediately even with an old token.
- **Authorization**: roles are enforced per endpoint. The organization comes from the login, never from request
  input; another organization's records answer 404. Platform roles (`platform_admin`, `reviewer`) cannot be
  mixed with an organization.
- **Four-eyes rules**: the submitter of evidence cannot accept or reject it; the creator of a mapping cannot
  approve it.
- **Audit**: reviews, evidence, assessments and mappings write to `audit_logs` with the actor.
- **Uploads**: extension allow-list, content checked against the extension, 25 MB limit, stored under their
  SHA-256 (the client file name is never a path), downloaded only as an attachment with
  `application/octet-stream` and `nosniff`.

## AI suggestions

- Only regulatory requirement text (public documents) is sent to the model API when an admin runs AI refinement.
  Evidence, assessments, users and organization names are never sent. If you cannot send even that text to an external
  service, leave `ANTHROPIC_API_KEY` empty: the local similarity step still works.
- API keys (Anthropic, OpenAI, Gemini) live only in the server's environment (`.env`) and are never returned by the API
  (it reports only which providers are configured) or written to logs. Each provider you enable receives the requirement text.
- Model output is untrusted: it is validated strictly, quotes are checked against the real text, and it can only
  create a *suggestion*. A reviewer's proposal plus a second reviewer's approval is still required.
- Requirement text is passed to the model as quoted data, but no prompt defence is perfect; this is why the model
  cannot change anything by itself.
- The run is a background job kept in server memory: one at a time, and its progress is lost on restart (saved
  results are kept).

## Not protected / known limits

- **No malware scanning.** Type and content-prefix checks are not a scanner. Add one (for example ClamAV)
  before accepting files from real users.
- **Login lockout is in process memory**: it resets on restart and is not shared between several server
  processes. Put a rate limiter in front for anything public.
- **No HTTPS in the app.** Run it behind a TLS-terminating reverse proxy. Do not expose it directly.
- **No refresh tokens, self-service "forgot password", MFA or SSO.** An administrator resets a forgotten password in
  the Users tab. A password change or reset ends that user's existing sessions; to cut someone off without a password
  change, disable the account. Administrators choose initial passwords, so ask people to change them at first sign-in.
- **Uploaded files are stored unencrypted** on local disk (`storage/evidence`). Use disk encryption and backups.
- **The evidence-expiry job is manual**: `python -m backend.cli evidence-expire`. Expiry is always applied when
  reading, so the dashboard and assessments are correct even if you never run it.
- Not a certification tool: a "compliant" result is a recorded human judgment, not a regulatory finding.

## Compliance posture

The dashboard's posture is an internal indicator computed by fixed rules (see the "How the posture is decided" panel and
`backend/dashboard.py`). The thresholds are this tool's convention, not a regulatory standard; it is not a certification
and must not be presented as an audit result. It is provisional while requirements remain unreviewed or unassessed.
