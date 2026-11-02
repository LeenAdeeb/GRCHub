"""CLI: register/acquire -> extract -> validate -> load to PostgreSQL -> human review."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path

from backend import changes, evidence, review, users
from backend.acquisition.fetcher import acquire, load_sources, register_local
from backend.database.loader import connect, init_schema, load_source
from backend.extraction.ecc import extract_ecc, validate_ecc
from backend.extraction.pdf_parser import extract_pages
from backend.extraction.sama import extract_sama, validate_sama
from backend.extraction.sama_maturity import extract_maturity, validate_maturity

STORAGE = Path("storage")


def run_extract(source_id: str, pdf: Path) -> int:
    source = load_sources()[source_id]
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    pages = extract_pages(pdf)
    empty = [p.index for p in pages if not p.text.strip()]

    if source["parser"] == "ecc":
        reqs = extract_ecc(pages, source["framework"], source["version"], digest)
        issues = validate_ecc(pages, reqs)
    elif source["parser"] == "sama_csf":
        reqs, ambiguous, skipped = extract_sama(pages, source["framework"], source["version"], digest)
        issues = validate_sama(pages, reqs, ambiguous, skipped)
        levels, sections, m_skipped = extract_maturity(pages, digest)
        issues += [f"maturity model: {i}" for i in validate_maturity(pages, levels, sections, m_skipped)]
        mat_out = STORAGE / "extracted" / f"{source_id}.maturity.json"
        mat_out.parent.mkdir(parents=True, exist_ok=True)
        mat_out.write_text(json.dumps({"levels": [l.model_dump() for l in levels],
                                       "sections": [x.model_dump() for x in sections]}), encoding="utf-8")
        print(f"{len(levels)} maturity levels + {len(sections)} sections -> {mat_out}")
    else:
        sys.exit(f"No parser configured for {source_id}")

    out = STORAGE / "extracted" / f"{source_id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(r.model_dump_json() for r in reqs), encoding="utf-8")
    kinds: dict[str, int] = {}
    for r in reqs:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    print(f"{len(reqs)} candidate requirements {kinds} -> {out} (all pending_review)")
    print(f"pages without text layer (physical index): {empty}")
    print("VALIDATION PASSED" if not issues else "VALIDATION ISSUES:")
    for i in issues:
        print(" -", i)
    return 1 if issues else 0


def show(row: dict) -> None:
    print(f"#{row['id']}  {row['framework']} {row['version']}  {row['control_id']}  [{row['kind']}]  {row['validation_status']}")
    print(f"  {row['domain']} > {row['subdomain_id']} {row['subdomain_title']}")
    print(f"  Verify in the PDF: physical page {row['page_index']} (printed page {row['page_label']})")
    for n in row["parse_notes"]:
        print(f"  NOTE: {n}")
    print(f"  Confidence: {row['extraction_confidence']}")
    print(f"  Text: {row['original_text']}")
    if row["reviewed_by"]:
        print(f"  Reviewed by {row['reviewed_by']} at {row['reviewed_at']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="grchub")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sources")
    sub.add_parser("acquire").add_argument("source_id")
    r = sub.add_parser("register-local")
    r.add_argument("source_id"); r.add_argument("file", type=Path); r.add_argument("--expect-sha256")
    e = sub.add_parser("extract")
    e.add_argument("source_id"); e.add_argument("pdf", type=Path)
    sub.add_parser("db-init", help="create tables (uses DATABASE_URL)")
    d = sub.add_parser("db-load", help="load extracted requirements into PostgreSQL")
    d.add_argument("source_id")

    oc = sub.add_parser("org-create", help="create an organization")
    oc.add_argument("name")
    uc = sub.add_parser("user-create", help="create a user (password from GRCHUB_NEW_PASSWORD or a prompt)")
    uc.add_argument("email")
    uc.add_argument("--role", required=True, choices=sorted(users.PLATFORM_ROLES | users.ORG_ROLES))
    uc.add_argument("--org-id", type=int)
    ip = sub.add_parser("import-pdf", help="register + extract + validate + load one official PDF in a single step")
    ip.add_argument("source_id"); ip.add_argument("file", type=Path); ip.add_argument("--expect-sha256")
    sg = sub.add_parser("suggest-mappings", help="generate (and optionally AI-refine) mapping suggestions between two frameworks")
    sg.add_argument("source_framework"); sg.add_argument("target_framework")
    sg.add_argument("--top-k", type=int, default=5); sg.add_argument("--min-score", type=float, default=0.15)
    sg.add_argument("--include-unapproved", action="store_true", help="also score requirements still awaiting review")
    sg.add_argument("--ai", action="store_true", help="refine with the language model (needs ANTHROPIC_API_KEY)")
    sg.add_argument("--provider", choices=["anthropic", "openai", "gemini"]); sg.add_argument("--max-calls", type=int, default=50); sg.add_argument("--dry-run", action="store_true", help="only estimate the AI cost")
    sub.add_parser("evidence-expire", help="mark accepted evidence past its review due date as expired")
    df = sub.add_parser("diff", help="compare two loaded versions of a framework")
    df.add_argument("framework"); df.add_argument("from_version"); df.add_argument("to_version")

    rv = sub.add_parser("review").add_subparsers(dest="rcmd", required=True)
    rv.add_parser("stats")
    ls = rv.add_parser("list")
    ls.add_argument("--status", default="pending_review", help="pending_review|approved|rejected|all")
    ls.add_argument("--framework"); ls.add_argument("--subdomain")
    ls.add_argument("--flagged", action="store_true", help="only low-confidence or noted items")
    ls.add_argument("--limit", type=int, default=50)
    rv.add_parser("show").add_argument("id", type=int)
    dc = rv.add_parser("decide")
    dc.add_argument("ids", type=int, nargs="+")
    g = dc.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true"); g.add_argument("--reject", action="store_true")
    dc.add_argument("--reviewer", required=True); dc.add_argument("--note")
    ex = rv.add_parser("export")
    ex.add_argument("--out", type=Path, default=STORAGE / "review_export.csv"); ex.add_argument("--framework")
    args = parser.parse_args()
    known = load_sources()
    if getattr(args, "source_id", None) is not None and args.source_id not in known:
        sys.exit(f"Unknown source id '{args.source_id}'. The source id is not the file name; use one of: "
                 + ", ".join(known) + "\nExample: import-pdf nca-ecc-2-2024-en /pdfs/<your file>.pdf")

    if args.cmd == "sources":
        for s in load_sources().values():
            print(f"{s['id']:42} {s['status']:9} {s['framework']} {s['version'] or ''}")
    elif args.cmd == "acquire":
        print(json.dumps(acquire(args.source_id, STORAGE), indent=2))
    elif args.cmd == "register-local":
        print(json.dumps(register_local(args.source_id, args.file, STORAGE, args.expect_sha256), indent=2))
    elif args.cmd == "import-pdf":
        # Same checks as the separate commands, in order; stops before touching the database if anything fails.
        try:
            record = register_local(args.source_id, args.file, STORAGE, args.expect_sha256)
        except ValueError as e:
            sys.exit(f"Refused: {e}")
        print(f"registered {args.file.name}: sha256 {record['sha256']}")
        if run_extract(args.source_id, Path(record["stored_path"])) != 0:
            sys.exit("Validation failed: nothing was loaded. Check that this is the right document and version.")
        with connect() as conn:
            result = load_source(conn, load_sources()[args.source_id],
                                 STORAGE / "extracted" / f"{args.source_id}.jsonl", STORAGE)
        print(json.dumps(result, indent=2))
        print("Loaded. Every requirement is pending_review until a reviewer approves it.")
    elif args.cmd == "suggest-mappings":
        from backend import llm, suggestions
        with connect() as conn:
            conn.autocommit = True
            if not args.dry_run:
                print(json.dumps(suggestions.generate(conn, "cli", args.source_framework, args.target_framework, args.top_k,
                                                      args.min_score, not args.include_unapproved), indent=2))
            if args.ai or args.dry_run:
                print("AI estimate:", json.dumps(suggestions.estimate(conn, args.source_framework, args.target_framework, args.provider)))
            if args.ai and not args.dry_run:
                if not llm.configured():
                    sys.exit("Set ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY to use --ai")
                try:
                    client = llm.Client(args.provider)
                except llm.LLMError as e:
                    sys.exit(str(e))
                try:
                    print(json.dumps(suggestions.refine(conn, "cli", args.source_framework, args.target_framework, client,
                                                        args.max_calls), indent=2))
                finally:
                    client.close()
    elif args.cmd == "extract":
        sys.exit(run_extract(args.source_id, args.pdf))
    elif args.cmd == "db-init":
        with connect() as conn:
            init_schema(conn)
        print("schema created")
    elif args.cmd == "db-load":
        with connect() as conn:
            result = load_source(conn, load_sources()[args.source_id],
                                 STORAGE / "extracted" / f"{args.source_id}.jsonl", STORAGE)
        print(json.dumps(result, indent=2))
    elif args.cmd == "org-create":
        with connect() as conn:
            print(f"organization id {users.create_organization(conn, args.name, 'cli')}")
    elif args.cmd == "user-create":
        password = os.environ.get("GRCHUB_NEW_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
        with connect() as conn:
            print(f"user id {users.create_user(conn, args.email, password, args.role, args.org_id, 'cli')}")
    elif args.cmd == "evidence-expire":
        with connect() as conn:
            print(f"{evidence.expire_due(conn)} evidence item(s) marked expired")
    elif args.cmd == "diff":
        with connect() as conn:
            d = changes.compare(conn, args.framework, args.from_version, args.to_version)
        print(f"added {len(d['added'])}, removed {len(d['removed'])}, modified {len(d['modified'])}, unchanged {d['unchanged']}")
        for key in ("added", "removed", "modified"):
            for item in d[key]:
                print(f"  {key:9} {item['control_id']}")
    elif args.cmd == "review":
        with connect() as conn:
            if args.rcmd == "stats":
                for row in review.stats(conn):
                    print(f"{row['framework']:10} {row['version']:10} {row['status']:15} {row['n']}")
            elif args.rcmd == "list":
                status = None if args.status == "all" else args.status
                for row in review.list_requirements(conn, status, args.framework, args.subdomain, args.flagged, args.limit):
                    print(f"#{row['id']:<5} {row['control_id']:12} p{row['page_index']:<3} conf={row['extraction_confidence']:.2f} "
                          f"{row['validation_status']:15} {row['original_text'][:70]}")
            elif args.rcmd == "show":
                show(review.get(conn, args.id))
            elif args.rcmd == "decide":
                decision = "approved" if args.approve else "rejected"
                for i in args.ids:
                    review.decide(conn, i, decision, args.reviewer, args.note)
                print(f"{decision}: {args.ids}")
            elif args.rcmd == "export":
                print(f"{review.export_csv(conn, args.out, args.framework)} rows -> {args.out}")


if __name__ == "__main__":
    main()
