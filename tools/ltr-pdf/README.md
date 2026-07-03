# LTR PDF builder

Renders a Liscere Technical Report from a single markdown source into a
series-styled A4 PDF, using [WeasyPrint](https://weasyprint.org/) (HTML/CSS
rendering). This is the toolchain behind `liscere-evaluation-hardware.pdf` and
the intended toolchain for LTR-2026-04 onward.

## Files

- `build_ltr.py` — the builder. Reads one LTR markdown file, converts the body
  to HTML, wraps it in the series title block + running header/footer metadata,
  and writes a PDF.
- `ltr-series.css` — the series stylesheet: `@page` header/footer, Georgia
  typography, tables, abstract, and figure blocks (including `figure.stacked`
  for two stacked images under a single caption).

## Requirements

```
pip install weasyprint markdown pyyaml pypdf
```

WeasyPrint also needs its native libraries (pango, cairo, gdk-pixbuf); on macOS
install them with `brew install pango`.

## Markdown source format

The file must begin with a YAML front-matter block:

```yaml
---
title: "Report title, may use <br/> for a line break"
id: "LTR-2026-04"
version: "v1"
date: "July 2026"
author: "Bruno Salmazo"
series: "Liscere Technical Report"
url: "liscere.com"
---
```

- Mark the abstract heading with `## Abstract {.abstract}`.
- Author figures as HTML `<figure>` blocks with image `src` relative to the
  markdown file's directory (e.g. `figures/fig1.png`). Use
  `<figure class="stacked">` with two `<img>` elements for a stacked pair.

## Usage

```
python build_ltr.py path/to/LTR-2026-04.md -o LTR-2026-04.pdf
```

Output defaults to `<id>.pdf` in the current directory if `-o` is omitted.
