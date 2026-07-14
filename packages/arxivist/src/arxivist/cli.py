"""CLI designed for coding agents: cheap targeted reads instead of full dumps.

    arxivist outline 2401.12345          # TOC with word counts — start here
    arxivist abstract 2401.12345
    arxivist section 2401.12345 3.2      # one section, by number or title
    arxivist refs 2401.12345
    arxivist get 2401.12345 -o papers/   # full markdown (+ json) to disk
    arxivist get 2401.12345              # full markdown to stdout
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="arxivist",
        description="arXiv papers as structured, agent-legible markdown.",
        epilog="IDs may be bare (2401.12345), versioned (2401.12345v2), "
        "old-style (hep-th/9901001), or any arxiv.org URL.",
    )
    parser.add_argument("--refresh", action="store_true", help="bypass the cache")
    sub = parser.add_subparsers(dest="command", required=True)

    p_get = sub.add_parser("get", help="full paper as markdown (stdout or --out dir)")
    p_get.add_argument("id")
    p_get.add_argument("-o", "--out", help="directory to write paper.md + paper.json into")
    p_get.add_argument("--pdf", action="store_true", help="force the PDF parser")
    p_get.add_argument("--json", action="store_true", help="emit JSON instead of markdown")

    for name, help_text in [
        ("outline", "table of contents with word counts"),
        ("abstract", "title, authors, and abstract"),
        ("refs", "the paper's reference list"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id")

    p_sec = sub.add_parser("section", help="one section by number ('3.2') or title substring")
    p_sec.add_argument("id")
    p_sec.add_argument("query")

    args = parser.parse_args(argv)

    import arxivist

    if args.command == "abstract":
        meta = arxivist.get_metadata(args.id, refresh=args.refresh)
        authors = ", ".join(a.name for a in meta.authors)
        print(f"# {meta.title}\n\n**Authors:** {authors}\n\n{meta.abstract}")
        return 0

    prefer = "pdf" if getattr(args, "pdf", False) else None
    paper = arxivist.get(args.id, refresh=args.refresh, prefer=prefer)

    if args.command == "get":
        if args.out:
            out = Path(args.out)
            # write into <out>/<id-slug>/ unless the user pointed at a fresh leaf dir
            target = out if not out.exists() else out / arxivist.parse_arxiv_id(args.id).slug
            paper.save(target)
            print(target)
        elif args.json:
            import json

            print(json.dumps(paper.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(paper.to_markdown())
    elif args.command == "outline":
        print(paper.outline())
    elif args.command == "refs":
        for r in paper.references:
            print(f"[{r.label}] {r.text}")
    elif args.command == "section":
        section = paper.get_section(args.query)
        if section is None:
            print(f"no section matching {args.query!r}; outline:\n", file=sys.stderr)
            print(paper.outline(), file=sys.stderr)
            return 1
        print(section.to_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
