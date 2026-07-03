#!/usr/bin/env python3
"""
Build a Liscere Technical Report PDF from a single markdown source.

Usage:
    python build_ltr.py path/to/LTR-XXXX-XX.md [-o output.pdf]

The markdown file must begin with a YAML front-matter block:

    ---
    title: "Report title, may use <br/> for a line break"
    id: LTR-2026-04
    version: v1
    date: "July 2026"
    author: Bruno Salmazo
    series: Liscere Technical Report
    url: liscere.com
    ---

    ## Abstract {.abstract}
    ...

Figures are authored directly as HTML <figure> blocks in the markdown, with
image sources relative to the markdown file's directory:

    <figure>
      <img src="figures/fig1.png">
      <figcaption>Figure 1. ...</figcaption>
    </figure>

    <figure class="stacked">
      <img src="figures/fig3a.png">
      <img src="figures/fig3b.png">
      <figcaption>Figure 3. ...</figcaption>
    </figure>

The stylesheet (ltr-series.css, alongside this script) styles the header,
footer, typography, tables, the abstract, and the figure blocks. Output
defaults to <id>.pdf in the current directory.
"""

import argparse
import os
import re
import sys

import yaml
import markdown
from weasyprint import HTML

HERE = os.path.dirname(os.path.abspath(__file__))
CSS_PATH = os.path.join(HERE, "ltr-series.css")

REQUIRED_KEYS = ("title", "id", "version", "date", "author", "series", "url")


def split_front_matter(text):
    """Return (metadata_dict, body_markdown). Requires a leading YAML block."""
    if not text.lstrip().startswith("---"):
        sys.exit("Error: markdown must start with a YAML front-matter block (---).")
    # Split on the first two --- fences.
    stripped = text.lstrip()
    parts = stripped.split("---", 2)
    # parts[0] is empty (before first ---), parts[1] is YAML, parts[2] is body.
    if len(parts) < 3:
        sys.exit("Error: could not find a closing --- for the front matter.")
    meta = yaml.safe_load(parts[1]) or {}
    body = parts[2]
    missing = [k for k in REQUIRED_KEYS if k not in meta]
    if missing:
        sys.exit(f"Error: front matter is missing required keys: {', '.join(missing)}")
    return meta, body


def build_html(meta, body_md):
    """Assemble the full HTML document string."""
    # Convert the markdown body to HTML. md_in_html lets the parser handle the
    # embedded <figure> blocks correctly; tables and attr_list support the
    # series table styling and the "{.abstract}" class on the abstract heading.
    body_html = markdown.markdown(
        body_md,
        extensions=["tables", "sane_lists", "attr_list", "md_in_html"],
    )

    # The .docmeta block feeds the footer-left document id, and .docseries feeds
    # the header-right label, both via CSS running elements (position: running +
    # element()). "Liscere" and "liscere.com" stay fixed literals in the
    # stylesheet; the id and the series label travel per report. The header label
    # is the series with the leading "Liscere " dropped: "Technical Report" for
    # the LTR series, "Briefing" for the LB series.
    docmeta = f'<div class="docmeta">{meta["id"]}</div>'
    header_label = meta["series"].replace("Liscere ", "", 1)
    docseries = f'<div class="docseries">{header_label}</div>'

    dateline = (
        f'{meta["date"]} · {meta["series"]} · '
        f'{meta["id"]} · {meta["version"]}'
    )

    title_block = (
        '<div class="title-block">'
        f'<h1>{meta["title"]}</h1>'
        f'<div class="author">{meta["author"]}</div>'
        f'<div class="affil">{meta.get("affiliation", "Liscere")}</div>'
        f'<div class="dateline">{dateline}</div>'
        "</div>"
    )

    return (
        "<!DOCTYPE html>\n<html>\n<head>\n<meta charset=\"utf-8\">\n"
        "</head>\n<body>\n"
        f"{docmeta}\n{docseries}\n{title_block}\n{body_html}\n"
        "</body>\n</html>"
    )


def main():
    ap = argparse.ArgumentParser(description="Build a Liscere Technical Report PDF.")
    ap.add_argument("markdown", help="path to the LTR markdown file")
    ap.add_argument("-o", "--output", help="output PDF path (default: <id>.pdf)")
    args = ap.parse_args()

    md_path = os.path.abspath(args.markdown)
    if not os.path.isfile(md_path):
        sys.exit(f"Error: file not found: {md_path}")
    md_dir = os.path.dirname(md_path)

    with open(md_path, encoding="utf-8") as f:
        text = f.read()

    meta, body_md = split_front_matter(text)
    html_str = build_html(meta, body_md)

    out_path = args.output or os.path.join(os.getcwd(), f"{meta['id']}.pdf")

    # base_url = the markdown file's directory, so relative figure paths
    # (figures/figN.png) resolve correctly.
    HTML(string=html_str, base_url=md_dir).write_pdf(out_path, stylesheets=[CSS_PATH])

    print(f"Built: {out_path}")
    try:
        from pypdf import PdfReader
        print(f"Pages: {len(PdfReader(out_path).pages)}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
