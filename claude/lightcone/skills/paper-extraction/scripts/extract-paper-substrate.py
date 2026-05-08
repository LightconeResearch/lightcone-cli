#!/usr/bin/env python3
"""
extract-paper-substrate.py — deterministic structural extraction for the
paper-extraction skill.

Reads `work/reference/` and produces:

  - figures/                        # figure files copied from source/
  - tables/<label-slug>.tex         # one file per LaTeX table block
  - bibliography-source.bib         # copy of any .bib found in source/ (Path A only)
  - bibliography-source.bbl         # copy of any .bbl found in source/ (Path A only)
  - index.json                      # single top-level index of everything extracted

Path A (arXiv LaTeX source): reads from work/reference/source/.
Path B (Docling fallback):   reads from work/reference/document.md and Docling's
                             pre-existing figures/ + tables/ + metadata.json.

The script handles only the deterministic pieces. Semantic interpretation —
"what does this figure show", "which findings are central", numerical-claim
extraction — is the agent's job after this script runs. The agent reads
index.json (specifically extraction_warnings) and fixes or surfaces gaps.

Usage:
    python extract-paper-substrate.py [--reference-dir work/reference]

Idempotent — skips files that already exist.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

FIGURE_BLOCK = re.compile(r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", re.DOTALL)
# Tables: include AAS-specific `deluxetable` (ApJ, ApJL, ApJS) alongside the standard `table`.
TABLE_BLOCK = re.compile(
    r"\\begin\{(?:table|deluxetable)\*?\}(.*?)\\end\{(?:table|deluxetable)\*?\}",
    re.DOTALL,
)
ABSTRACT_BLOCK = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)
TITLE_CMD = re.compile(r"\\title\*?\s*(?:\[[^\]]*\])?\s*\{")
# Citations: natbib family + biblatex (autocite, textcite, parencite, footcite, smartcite).
CITE = re.compile(
    r"\\(?:cite|citep|citet|citealp|citealt|citeauthor|citeyear|citeyearpar|"
    r"autocite|textcite|parencite|footcite|smartcite)\*?"
    r"(?:\[[^\]]*\]){0,2}\{([^}]+)\}"
)
ASTRA_SCHEMA_VERSION = "0.0.7"  # bump when the ASTRA spec version we target changes
CAPTION = re.compile(r"\\caption\{((?:[^{}]|\{[^}]*\})*)\}", re.DOTALL)
LABEL = re.compile(r"\\label\{([^}]+)\}")
INCLUDEGRAPHICS = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
PLOTONE = re.compile(r"\\plotone\{([^}]+)\}")
PLOTTWO = re.compile(r"\\plottwo\{([^}]+)\}\{([^}]+)\}")
FIGURE_INPUT = re.compile(r"\\input\{([^}]+\.(?:pgf|tex|tikz))\}")
SECTION = re.compile(r"\\(section|subsection|subsubsection)\*?\{((?:[^{}]|\{[^}]*\})*)\}")


def line_at(content: str, offset: int) -> int:
    """1-indexed line number of `offset` within `content`."""
    return content.count("\n", 0, offset) + 1


def first_match(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def extract_caption(text: str, macros: dict[str, str]) -> str:
    """Return the last non-empty caption in a block.

    Composite figures often have empty subfigure captions before the real
    top-level caption; taking the first caption produces a false warning.
    """
    captions = [m.group(1).strip() for m in CAPTION.finditer(text)]
    nonempty = [caption for caption in captions if caption]
    return expand_macros(nonempty[-1], macros) if nonempty else ""


# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------


def detect_path(reference_dir: Path) -> str:
    if (reference_dir / "source").is_dir():
        return "A"
    if (reference_dir / "document.md").is_file():
        return "B"
    sys.exit(
        f"error: neither {reference_dir}/source/ nor {reference_dir}/document.md exists "
        f"— run paper-extraction Step 1 (substrate acquisition) first"
    )


# ---------------------------------------------------------------------------
# Path A — LaTeX source
# ---------------------------------------------------------------------------


def list_tex_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.rglob("*.tex"))


# A `%` not preceded by `\\` starts a LaTeX comment running to end-of-line.
# We strip comment *content* but keep the `\n` so line numbers are preserved.
COMMENT = re.compile(r"(?<!\\)%[^\n]*")


def strip_comments(content: str) -> str:
    """Strip LaTeX comments (line content after unescaped `%`), preserving newlines."""
    return COMMENT.sub("", content)


# Match `\newcommand[*]{\name}{body}` — no-args form only. Args (`[2]`) are skipped.
NEWCOMMAND = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand)\*?\s*\{?\s*\\([A-Za-z]+)\s*\}?\s*\{",
)


def collect_simple_macros(tex_files: list[tuple[Path, str]]) -> dict[str, str]:
    """Build a `\\name -> body` dict for no-arg `\\newcommand` macros across the source.

    Skips macros with arguments (e.g. `\\newcommand{\\foo}[2]{...}`) — handling those
    requires expansion, which is out of scope. Skips macros whose body is the same as
    their name (e.g. `\\newcommand{\\foo}{\\foo}`) which would loop.
    """
    macros: dict[str, str] = {}
    for _, content in tex_files:
        for match in NEWCOMMAND.finditer(content):
            name = match.group(1)
            # Walk balanced braces to find the body.
            body = walk_balanced_braces(content, match.end() - 1)
            if body is None:
                continue
            # Skip if there's an arg-count specifier between name and body:
            # we already consumed up to the body's opening `{`, so this regex
            # can match args-form too. Detect by checking if body looks like
            # an args spec — actually simpler: check if `[N]` lies between
            # name end and body start in the original source.
            between_start = match.end(1)
            between_end = match.end() - 1
            between = content[between_start:between_end]
            if re.search(r"\[\s*\d+\s*\]", between):
                continue  # args-form, skip
            if body.strip() == f"\\{name}":
                continue  # self-referential
            macros[name] = body
    return macros


def expand_macros(text: str, macros: dict[str, str], max_iterations: int = 5) -> str:
    """Substitute `\\name` (where name is in `macros`) iteratively. Stops at fixed point or
    `max_iterations` (handles nested macros, prevents infinite loops on pathological input).
    """
    if not text or not macros:
        return text
    # Match `\name` where name is in our table. Order longest-first so `\desidrone`
    # wins over `\desi` if both exist.
    names = sorted(macros.keys(), key=len, reverse=True)
    pattern = re.compile(r"\\(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z])")
    out = text
    for _ in range(max_iterations):
        new = pattern.sub(lambda m: macros[m.group(1)], out)
        if new == out:
            return out
        out = new
    return out


def read_tex_with_origin(source_dir: Path) -> list[tuple[Path, str]]:
    """Read each .tex file (stripped of comments) in *paper-reading order*.

    Order is determined by walking the main file's `\\input{}` / `\\include{}` chain.
    The main file is the one containing `\\documentclass`. Files not reachable from
    the input chain are appended at the end (alphabetical) as orphans.

    Comments are stripped at read time to prevent commented-out LaTeX from leaking
    into figure / table / section / citation extraction. Newlines are preserved so
    line numbers are still meaningful.
    """
    paths = list_tex_files(source_dir)
    if not paths:
        return []

    contents: dict[Path, str] = {}
    for p in paths:
        try:
            contents[p] = strip_comments(p.read_text(errors="replace"))
        except OSError as e:
            print(f"warn: could not read {p}: {e}", file=sys.stderr)

    # Find the main file (contains \documentclass, after comment stripping).
    main = next((p for p in paths if r"\documentclass" in contents.get(p, "")), None)
    if main is None:
        # No main file detected — fall back to alphabetical order.
        return [(p, contents[p]) for p in paths if p in contents]

    # Map basename (without extension) → path, for resolving \input{name} or \input{path/name}.
    by_stem: dict[str, Path] = {}
    for p in paths:
        by_stem.setdefault(p.stem, p)

    INPUT_CMD = re.compile(r"\\(?:input|include)\{([^}]+)\}")
    ordered: list[Path] = []
    seen: set[Path] = set()

    def walk(p: Path) -> None:
        if p in seen or p not in contents:
            return
        seen.add(p)
        ordered.append(p)
        for match in INPUT_CMD.finditer(contents[p]):
            target = match.group(1).strip()
            target = target.removesuffix(".tex")
            stem = Path(target).stem  # last path component, no extension
            sub = by_stem.get(stem)
            if sub is not None:
                walk(sub)

    walk(main)
    # Append unreached files (orphans — supplementary, unused, etc.) at the end.
    for p in paths:
        if p not in seen and p in contents:
            ordered.append(p)

    return [(p, contents[p]) for p in ordered]


def join_tex(tex_files: list[tuple[Path, str]]) -> str:
    return "\n".join(content for _, content in tex_files)


def extract_figures(
    reference_dir: Path,
    source_dir: Path,
    tex_files: list[tuple[Path, str]],
    macros: dict[str, str],
) -> tuple[list[dict], list[str]]:
    """Walk every figure block; copy resolved figure files; return (entries, warnings)."""
    fig_dir = reference_dir / "figures"
    fig_dir.mkdir(exist_ok=True)
    entries: list[dict] = []
    warnings: list[str] = []
    counter = 0

    for tex_path, content in tex_files:
        for match in FIGURE_BLOCK.finditer(content):
            counter += 1
            block = match.group(1)
            caption = extract_caption(block, macros)
            label = first_match(LABEL, block)

            # Capture every external figure reference in the block. Besides
            # \includegraphics, AASTeX/emulateapj papers often use \plotone /
            # \plottwo, while ML papers often \input Matplotlib/PGF exports.
            # Multi-panel / subfloat figures routinely have several.
            graphic_matches = external_figure_refs(block)
            files_rel: list[str] = []
            for graphic in graphic_matches:
                resolved = resolve_graphic(source_dir, graphic)
                if resolved:
                    dest = fig_dir / resolved.name
                    if not dest.exists():
                        shutil.copy2(resolved, dest)
                    files_rel.append(f"figures/{resolved.name}")
                else:
                    warnings.append(
                        f"figure fig{counter}: \\includegraphics{{{graphic}}} could not resolve to a file in source/"
                    )

            inline_figure = bool(re.search(r"\\begin\{(?:tikzpicture|picture|pspicture)\}", block))
            if not graphic_matches and not inline_figure:
                warnings.append(f"figure fig{counter}: no external figure file found in block")
            if not caption:
                warnings.append(f"figure fig{counter}: no \\caption found")

            entries.append(
                {
                    "id": f"fig{counter}",
                    "label": label,
                    "caption": caption,
                    # Single-graphic figures keep the simple shape (the common case);
                    # multi-graphic figures expose all panels under "files".
                    "source_path": graphic_matches[0] if graphic_matches else None,
                    "file": files_rel[0] if files_rel else None,
                    "files": files_rel if len(files_rel) > 1 else None,
                    "block_origin": str(tex_path.relative_to(source_dir)),
                    "line": line_at(content, match.start()),
                }
            )

    return entries, warnings


def external_figure_refs(block: str) -> list[str]:
    """Return external figure-like files referenced inside a figure block."""
    refs: list[str] = []
    refs.extend(INCLUDEGRAPHICS.findall(block))
    refs.extend(PLOTONE.findall(block))
    for first, second in PLOTTWO.findall(block):
        refs.extend([first, second])
    refs.extend(FIGURE_INPUT.findall(block))
    # Preserve order while de-duplicating repeated panels.
    seen: set[str] = set()
    out = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def resolve_graphic(source_dir: Path, graphic: str) -> Path | None:
    """LaTeX \\includegraphics filenames can omit the extension; try common ones."""
    base = source_dir / graphic
    if base.exists():
        return base
    for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps"):
        candidate = base.with_suffix(ext)
        if candidate.exists():
            return candidate
    matches = list(source_dir.rglob(f"{Path(graphic).stem}.*"))
    return matches[0] if matches else None


def extract_tables(
    reference_dir: Path,
    tex_files: list[tuple[Path, str]],
    source_dir: Path,
    macros: dict[str, str],
) -> tuple[list[dict], list[str]]:
    tab_dir = reference_dir / "tables"
    tab_dir.mkdir(exist_ok=True)
    entries: list[dict] = []
    warnings: list[str] = []
    counter = 0

    for tex_path, content in tex_files:
        for match in TABLE_BLOCK.finditer(content):
            counter += 1
            block = match.group(0)  # full \begin{table}...\end{table}
            body = match.group(1)
            label = first_match(LABEL, body)
            caption = extract_caption(body, macros)
            slug = label.replace(":", "-").replace(" ", "_") if label else f"tab{counter}"
            out = tab_dir / f"{slug}.tex"
            if not out.exists():
                out.write_text(block)
            if not caption:
                warnings.append(f"table tab{counter}: no \\caption found")
            if not label:
                warnings.append(f"table tab{counter}: no \\label — wrote as {slug}.tex")
            entries.append(
                {
                    "id": f"tab{counter}",
                    "label": label,
                    "caption": caption,
                    "file": f"tables/{slug}.tex",
                    "block_origin": str(tex_path.relative_to(source_dir)),
                    "line": line_at(content, match.start()),
                }
            )

    return entries, warnings


def extract_outline(
    tex_files: list[tuple[Path, str]], source_dir: Path, macros: dict[str, str]
) -> list[dict]:
    """Walk \\section{}, \\subsection{}, \\subsubsection{} in source order.

    Attach a \\label{} only when it directly follows the section command (whitespace
    between is fine, but no other content). The convention is `\\section{Foo}\\label{sec:foo}`
    or with one newline between — anything more, and the label belongs elsewhere.
    """
    level_map = {"section": 1, "subsection": 2, "subsubsection": 3}
    immediate_label = re.compile(r"\A\s*\\label\{([^}]+)\}")
    out = []
    for tex_path, content in tex_files:
        for match in SECTION.finditer(content):
            kind, title = match.group(1), expand_macros(match.group(2).strip(), macros)
            tail = content[match.end() : match.end() + 200]
            label_match = immediate_label.match(tail)
            label = label_match.group(1) if label_match else None
            out.append(
                {
                    "level": level_map[kind],
                    "title": title,
                    "label": label,
                    "source_file": str(tex_path.relative_to(source_dir)),
                    "line": line_at(content, match.start()),
                }
            )
    return out


def extract_citations(
    tex_files: list[tuple[Path, str]], source_dir: Path
) -> dict[str, list[dict]]:
    """Map each citation key to every (file, line) location it's cited.

    Shape: {"smith24": [{"file": "main.tex", "line": 42}, {"file": "main.tex", "line": 89}], ...}
    """
    out: dict[str, list[dict]] = {}
    for tex_path, content in tex_files:
        rel_file = str(tex_path.relative_to(source_dir))
        for match in CITE.finditer(content):
            line = line_at(content, match.start())
            for key in match.group(1).split(","):
                k = key.strip()
                if not k:
                    continue
                out.setdefault(k, []).append({"file": rel_file, "line": line})
    # Sort keys for stable output
    return {k: out[k] for k in sorted(out)}


def walk_balanced_braces(content: str, start: int) -> str | None:
    """Given the index of the opening `{`, return the content between matched
    braces (exclusive of the braces themselves), or None if unbalanced.
    Honors escaped braces (`\\{`, `\\}`).
    """
    depth = 1
    i = start + 1
    while i < len(content) and depth > 0:
        c = content[i]
        if c == "\\" and i + 1 < len(content):
            i += 2  # skip escaped char
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    if depth == 0:
        return content[start + 1 : i - 1]
    return None


def extract_abstract(tex_files: list[tuple[Path, str]], macros: dict[str, str]) -> str | None:
    """Extract abstract content. Supports two LaTeX forms:

    - environment: `\\begin{abstract}...\\end{abstract}` (most journals)
    - command:    `\\abstract{...}` (A&A's aa.cls and similar)
    """
    for _, content in tex_files:
        # Form 1: environment
        match = ABSTRACT_BLOCK.search(content)
        if match:
            return expand_macros(match.group(1).strip(), macros)

        # Form 2: command — balanced-brace walk
        cmd = re.search(r"\\abstract\s*\{", content)
        if cmd:
            body = walk_balanced_braces(content, cmd.end() - 1)
            if body is not None:
                return expand_macros(body.strip(), macros)
    return None


def extract_title(tex_files: list[tuple[Path, str]], macros: dict[str, str]) -> str | None:
    """Extract \\title{...} (or \\title[short]{full}) content with balanced braces."""
    for _, content in tex_files:
        match = TITLE_CMD.search(content)
        if match:
            body = walk_balanced_braces(content, match.end() - 1)
            if body is not None:
                expanded = expand_macros(" ".join(body.split()), macros)
                # Strip common font-style wrappers that a `\\boldmath`-prefixed title
                # leaves behind after macro expansion (no-op if not present).
                expanded = re.sub(r"^\\boldmath\s*", "", expanded)
                return expanded
    return None


def derive_astra_id(arxiv_id: str | None, doi: str | None) -> str:
    """Stable ASTRA id from arXiv ID or DOI. Lowercase, [a-z0-9_]+, leading letter."""
    if arxiv_id:
        slug = "arxiv_" + arxiv_id.replace(".", "_").replace("/", "_").lower()
    elif doi:
        slug = "doi_" + re.sub(r"[^a-z0-9]+", "_", doi.lower()).strip("_")
    else:
        slug = "paper_unknown"
    # Ensure leading letter, only [a-z0-9_]
    slug = re.sub(r"[^a-z0-9_]+", "_", slug)
    if not slug or not slug[0].isalpha():
        slug = "paper_" + slug
    return slug


def write_astra_yaml_stub(
    reference_dir: Path,
    arxiv_id: str | None,
    doi: str | None,
    title: str | None,
    abstract: str | None,
) -> str:
    """Emit a stub `work/reference/astra.yaml` that the agent fills in.

    The script populates: id, version, name, narrative.summary (from abstract),
    inputs/outputs as empty lists, and an empty findings map. The agent's job
    (Step 5 in SKILL.md) is to walk the paper and append findings entries with
    quote evidence, plus a `narrative.findings:` cross-link. Once that's in,
    `astra validate work/reference/astra.yaml` should pass.

    If the file already exists, leave it alone — it may have agent edits.
    """
    out = reference_dir / "astra.yaml"
    if out.exists():
        return "astra.yaml"

    astra_id = derive_astra_id(arxiv_id, doi)
    title_str = title or "TODO: paper title (script could not extract \\title{})"
    summary_str = abstract or "TODO: one-paragraph summary of the paper (no abstract extracted)"

    # Indent the summary as a block scalar so multi-line abstracts round-trip
    summary_indented = "\n".join("    " + line for line in summary_str.splitlines())

    content = f"""# Stub ASTRA representation of the source paper.
#
# Populated by paper-extraction's script: id, version, name, narrative.summary.
# The agent (paper-extraction Step 5) fills in `findings:` with the paper's
# claimed numerical results plus a `narrative.findings:` cross-link, then runs
# `astra validate astra.yaml` to confirm.

id: {astra_id}
version: "{ASTRA_SCHEMA_VERSION}"
name: {json.dumps(title_str)}

narrative:
  summary: |
{summary_indented}

inputs: []
outputs: []

# Agent: append entries here, one per central numerical claim the paper makes.
# Shape: see https://w3id.org/ASTRA/insight (Insight + Evidence). Minimal entry:
#
#   <id>:
#     id: <id>
#     claim: "<1-2 sentences capturing the result>"
#     created_at: "<ISO 8601 datetime>"
#     evidence:
#       - id: <evidence_id>
#         doi: "<paper DOI>"
#         version: <paper version, integer>
#         quote:
#           exact: "<exact text from the paper that supports the claim>"
findings: {{}}
"""
    out.write_text(content)
    return "astra.yaml"


def copy_embedded_bibliography(reference_dir: Path, source_dir: Path) -> tuple[str | None, str | None]:
    """Copy any .bib / .bbl files from source/ into work/reference/."""
    bib_src = next(iter(source_dir.rglob("*.bib")), None)
    bbl_src = next(iter(source_dir.rglob("*.bbl")), None)

    bib_rel = None
    bbl_rel = None
    if bib_src:
        dest = reference_dir / "bibliography-source.bib"
        if not dest.exists():
            shutil.copy2(bib_src, dest)
        bib_rel = "bibliography-source.bib"
    if bbl_src:
        dest = reference_dir / "bibliography-source.bbl"
        if not dest.exists():
            shutil.copy2(bbl_src, dest)
        bbl_rel = "bibliography-source.bbl"
    return bib_rel, bbl_rel


# ---------------------------------------------------------------------------
# Path B — Docling fallback
# ---------------------------------------------------------------------------


def extract_path_b(reference_dir: Path) -> dict:
    """Path B: Docling already produced figures/ + tables/ + metadata.json. Build index from those."""
    metadata_path = reference_dir / "metadata.json"
    if not metadata_path.exists():
        sys.exit(
            f"error: {metadata_path} not found — Path B requires Docling output. Re-run substrate acquisition."
        )
    docling = json.loads(metadata_path.read_text())

    astra_rel = write_astra_yaml_stub(
        reference_dir, arxiv_id=None, doi=None, title=None, abstract=None
    )
    index = {
        "path": "B",
        "paper_pdf": "paper.pdf" if (reference_dir / "paper.pdf").exists() else None,
        "paper_tex": None,
        "source_dir": None,
        "document_md": "document.md" if (reference_dir / "document.md").exists() else None,
        "bibliography_source_bib": None,
        "bibliography_source_bbl": None,
        "astra_yaml": astra_rel,
        "title": None,  # Future refinement: parse from Docling's markdown
        "abstract": None,  # Future refinement: parse from Docling's markdown
        "figures": docling.get("figures", []),
        "tables": docling.get("tables", []),
        "outline": [],  # Future refinement: parse Docling's markdown headings
        "citations": {},  # Future refinement: extract citation markers from document.md
        "extraction_warnings": [
            "Path B (Docling fallback): title + abstract + outline + citations not yet extracted from document.md; that's a future refinement."
        ],
    }
    return index


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--reference-dir", type=Path, default=Path("work/reference"))
    p.add_argument("--arxiv-id", help="arXiv ID, used to populate astra.yaml id and evidence.doi")
    p.add_argument("--doi", help="paper DOI (used when arXiv ID is unavailable)")
    args = p.parse_args()

    reference_dir = args.reference_dir
    if not reference_dir.is_dir():
        sys.exit(f"error: {reference_dir} not found — run paper-extraction Step 1 first")

    path = detect_path(reference_dir)
    print(f"detected path: {path}")

    if path == "A":
        source_dir = reference_dir / "source"
        tex_files = read_tex_with_origin(source_dir)
        if not tex_files:
            sys.exit(f"error: no .tex content found in {source_dir}")

        macros = collect_simple_macros(tex_files)
        figures, fig_warnings = extract_figures(reference_dir, source_dir, tex_files, macros)
        tables, tab_warnings = extract_tables(reference_dir, tex_files, source_dir, macros)
        outline = extract_outline(tex_files, source_dir, macros)
        citations = extract_citations(tex_files, source_dir)
        abstract = extract_abstract(tex_files, macros)
        title = extract_title(tex_files, macros)
        bib_rel, bbl_rel = copy_embedded_bibliography(reference_dir, source_dir)
        astra_rel = write_astra_yaml_stub(
            reference_dir, args.arxiv_id, args.doi, title, abstract
        )

        paper_tex = reference_dir / "paper.tex"
        index = {
            "path": "A",
            "paper_pdf": "paper.pdf" if (reference_dir / "paper.pdf").exists() else None,
            "paper_tex": "paper.tex" if paper_tex.exists() or paper_tex.is_symlink() else None,
            "source_dir": "source",
            "document_md": None,
            "bibliography_source_bib": bib_rel,
            "bibliography_source_bbl": bbl_rel,
            "astra_yaml": astra_rel,
            "title": title,
            "abstract": abstract,
            "figures": figures,
            "tables": tables,
            "outline": outline,
            "citations": citations,
            "extraction_warnings": fig_warnings + tab_warnings,
        }

        print(
            f"  figures: {len(figures)}, tables: {len(tables)}, "
            f"sections: {len(outline)}, citation-keys: {len(citations)}, "
            f"title: {'yes' if title else 'no'}, abstract: {'yes' if abstract else 'no'}, "
            f"warnings: {len(index['extraction_warnings'])}"
        )
    else:
        index = extract_path_b(reference_dir)
        print(
            f"  figures: {len(index['figures'])}, tables: {len(index['tables'])} (from Docling), "
            f"warnings: {len(index['extraction_warnings'])}"
        )

    index_path = reference_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2))
    print(f"wrote {index_path}")


if __name__ == "__main__":
    main()
