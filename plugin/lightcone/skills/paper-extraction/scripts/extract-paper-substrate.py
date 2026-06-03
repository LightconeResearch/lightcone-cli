#!/usr/bin/env python3
"""
extract-paper-substrate.py — deterministic structural extraction for the
paper-extraction skill.

Reads `work/reference/` and produces:

  - figures/                        # figure files copied from source/
  - tables/<label-slug>.tex         # one file per LaTeX table block
  - bibliography-source.bib         # copy of any .bib found in source/ (Path A only)
  - bibliography-source.bbl         # copy of any .bbl found in source/ (Path A only)
  - .doi-cache.json                 # Crossref/ADS lookup cache for re-run idempotency
  - index.json                      # single top-level index of everything extracted

Path A (arXiv LaTeX source): reads from work/reference/source/.
Path B (Docling fallback):   reads from work/reference/document.md and Docling's
                             pre-existing figures/ + tables/ + metadata.json.

The script handles only the deterministic pieces. Semantic interpretation —
"what does this figure show", "which findings are central", numerical-claim
extraction — is the agent's job after this script runs. The agent reads
index.json (specifically extraction_warnings) and fixes or surfaces gaps.

`index.json`'s `citations:` block enriches the cite-key → location mapping
with each cited paper's full text + resolved DOI, so downstream consumers
can do citation-key lookups for a paper's bibliography directly (no separate
cited-papers index file).

Usage:
    python extract-paper-substrate.py [--reference-dir work/reference]

Idempotent — skips files that already exist; cached DOI lookups don't
re-hit the network on re-runs.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from importlib.metadata import version as _pkg_version
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
# Derived from the installed astra-spec package so the stub `astra.yaml` always
# stamps the version actually present in the environment — `astra validate` will
# warn if the analysis declares a version the installed astra-spec can't honour.
# Let PackageNotFoundError propagate: this script ships with lightcone-cli, which
# depends on astra-spec, so a missing install is a real bug we want loud.
ASTRA_SCHEMA_VERSION = _pkg_version("astra-spec")

# Bump when the structural shape of `index.json` changes in a backwards-incompatible
# way (a new key added is fine; renaming/reshaping an existing value breaks consumers).
# v1: introduced explicit versioning; `citations:` value shape transitioned from
#     `key -> [locations]` to `key -> {locations, citation, doi}`.
INDEX_SCHEMA_VERSION = 1

CROSSREF_API = "https://api.crossref.org/works"
CROSSREF_USER_AGENT = (
    "paper-extraction (https://github.com/LightconeResearch/lightcone-cli; "
    "mailto:cailmdaley@gmail.com)"
)
ADS_API = "https://api.adsabs.harvard.edu/v1/search/query"
NETWORK_TIMEOUT_S = 10
# Match caption commands; the body itself is walked with balanced-brace logic so
# nested braces and escaped braces survive intact.
CAPTION = re.compile(r"\\caption\*?\s*(?:\[[^\]]*\])?\s*\{")
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
    Balanced-brace walking preserves nested LaTeX and escaped braces inside
    caption bodies.
    """
    captions = []
    for match in CAPTION.finditer(text):
        body = walk_balanced_braces(text, match.end() - 1)
        if body is not None:
            captions.append(body.strip())
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
# Bibliography resolution — shared by Path A (.bib/.bbl) and Path B (Docling)
# ---------------------------------------------------------------------------
#
# Produces a list of bibliography entries, each `{key, citation, doi}`, that
# downstream joins against `extract_citations()`'s `{key: [locations]}` to enrich
# the `citations:` block in `index.json`.
#
# Path A: parse `bibliography-source.bib` first, fall back to `.bbl`. Keys come
# from BibTeX directly (case-sensitive, unique per-paper, identical to what the
# tex source's `\cite{}` invocations reference).
#
# Path B: parse the references section at the tail of `document.md`. Docling has
# no \cite{} markers in the prose so we synthesize keys from first-author + year
# (`asgari_2017`, disambiguated with letter suffixes when needed). The synthetic
# keys carry no `locations:` entries — citation invocations from rendered prose
# are a separate extraction problem flagged in `extraction_warnings`.


# Parse @type{key, field = value, ...} entries. Skip @comment, @preamble, @string.
BIB_ENTRY_HEAD = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.IGNORECASE)
DOI_IN_TEXT = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
ARXIV_ID_IN_TEXT = re.compile(
    r"(?:arXiv:|astro-ph/|hep-(?:th|ph)/|gr-qc/|cond-mat/|math/|cs\.[A-Z]{2}/)"
    r"\s*([a-zA-Z0-9\-./]+)",
    re.IGNORECASE,
)
# Newer-format arXiv IDs without prefix: 4 digits, dot, 4-5 digits, optional vN
ARXIV_BARE = re.compile(r"\b(\d{4}\.\d{4,5})(?:v\d+)?\b")


def parse_bib(content: str) -> list[dict]:
    """Parse BibTeX content into a list of entries.

    Each entry: `{"type": str, "key": str, "fields": {<lowercased-field>: <stripped-value>}}`.
    Skips `@comment`, `@preamble`, `@string` (handling string macros properly would require
    substitution; for our enrichment purposes we can live without it).
    Field values are unwrapped from `{...}` or `"..."` and have surrounding whitespace stripped.
    Doesn't try to interpret LaTeX accents or commands — keeps them verbatim so re-running
    on the same input is stable.
    """
    entries: list[dict] = []
    i = 0
    while i < len(content):
        match = BIB_ENTRY_HEAD.search(content, i)
        if not match:
            break
        entry_type = match.group(1).lower()
        key = match.group(2)
        cursor = match.end()
        if entry_type in ("comment", "preamble", "string"):
            # Skip to matching closing brace
            depth = 1
            while cursor < len(content) and depth > 0:
                if content[cursor] == "{":
                    depth += 1
                elif content[cursor] == "}":
                    depth -= 1
                cursor += 1
            i = cursor
            continue
        fields, cursor = _parse_bib_fields(content, cursor)
        entries.append({"type": entry_type, "key": key, "fields": fields})
        i = cursor
    return entries


def _parse_bib_fields(content: str, start: int) -> tuple[dict[str, str], int]:
    """Parse `field = value, field = value, ...}` starting at `start`.

    Returns the field dict plus the offset just after the closing entry brace.
    """
    fields: dict[str, str] = {}
    i = start
    while i < len(content):
        # Skip whitespace + commas between fields
        while i < len(content) and content[i] in " \t\n\r,":
            i += 1
        if i >= len(content) or content[i] == "}":
            return fields, i + 1
        # Field name
        name_start = i
        while i < len(content) and content[i] not in " \t\n\r=":
            i += 1
        name = content[name_start:i].strip().lower()
        # Skip whitespace + `=`
        while i < len(content) and content[i] in " \t\n\r":
            i += 1
        if i >= len(content) or content[i] != "=":
            # Malformed entry — bail
            return fields, _skip_to_entry_end(content, i)
        i += 1
        while i < len(content) and content[i] in " \t\n\r":
            i += 1
        # Field value: `{...}` (balanced), `"..."`, or bare token
        value, i = _read_bib_value(content, i)
        if name:
            fields[name] = value
    return fields, i


def _read_bib_value(content: str, i: int) -> tuple[str, int]:
    if i >= len(content):
        return "", i
    if content[i] == "{":
        depth = 1
        i += 1
        start = i
        while i < len(content) and depth > 0:
            if content[i] == "\\" and i + 1 < len(content):
                i += 2
                continue
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        return content[start:i].strip(), i + 1
    if content[i] == '"':
        i += 1
        start = i
        while i < len(content) and content[i] != '"':
            if content[i] == "\\" and i + 1 < len(content):
                i += 2
                continue
            i += 1
        return content[start:i].strip(), i + 1
    # Bare token (number, string macro reference)
    start = i
    while i < len(content) and content[i] not in " \t\n\r,}":
        i += 1
    return content[start:i].strip(), i


def _skip_to_entry_end(content: str, i: int) -> int:
    depth = 1
    while i < len(content) and depth > 0:
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
        i += 1
    return i


def parse_bbl(content: str) -> list[dict]:
    """Parse a rendered `.bbl` into bibitem records.

    Each `\\bibitem[label]{key}` introduces an entry whose rendered text runs
    until the next `\\bibitem` or end-of-file. `.bbl` has no field structure,
    so we return `{key, raw}` — the resolver mines DOI/arXiv-ID hints from `raw`.
    """
    bibitem = re.compile(r"\\bibitem(?:\[[^\]]*\])?\s*\{([^}]+)\}", re.DOTALL)
    matches = list(bibitem.finditer(content))
    out: list[dict] = []
    for idx, match in enumerate(matches):
        key = match.group(1).strip()
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        raw = content[start:end].strip()
        out.append({"key": key, "raw": raw})
    return out


def parse_doc_references(document_md: str) -> list[str]:
    """Parse the references section out of Docling-rendered markdown.

    Heuristic: find a heading whose text matches `References` / `Bibliography`
    / `Citations` (case-insensitive, optional numeric prefix), take everything
    after it, split on blank lines, drop empty paragraphs.
    """
    heading_re = re.compile(
        r"^\s*#{1,6}\s+(?:\d+\.?\s+)?(?:references|bibliography|citations)\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = heading_re.search(document_md)
    if not match:
        return []
    body = document_md[match.end():]
    # If a subsequent top-level heading appears, stop there (acknowledgments,
    # appendices, supplementary). Stop at the first heading at the same level
    # or shallower than the references heading.
    next_section = re.search(r"^\s*#{1,6}\s+\S", body, re.MULTILINE)
    if next_section:
        body = body[: next_section.start()]
    # Split on blank lines into paragraphs; trim and drop empties.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body)]
    return [p for p in paragraphs if p]


def format_bib_citation(fields: dict[str, str]) -> str:
    """Build a one-line human-readable citation from a parsed `.bib` entry.

    Best-effort — uses what's present (author, year, title, journal, volume, page).
    Not a publication-quality formatter; just enough to be useful as a reading
    aid when a downstream agent sees `index.json`'s `citations:` block.
    """
    author = _first_author_from_bib_field(fields.get("author", ""))
    others = ", et al." if " and " in fields.get("author", "") else ""
    year = fields.get("year", "").strip()
    title = _clean_bib_text(fields.get("title", "")).strip().rstrip(".")
    journal = _clean_bib_text(
        fields.get("journal", "") or fields.get("booktitle", "") or fields.get("howpublished", "")
    ).strip()
    volume = fields.get("volume", "").strip()
    pages = fields.get("pages", "").strip().replace("--", "-")
    parts = []
    if author:
        parts.append(f"{author}{others}")
    if year:
        parts.append(f"({year})")
    if title:
        parts.append(f"{title}.")
    if journal:
        tail = journal
        if volume:
            tail += f" {volume}"
        if pages:
            tail += f", {pages}"
        parts.append(tail)
    return " ".join(parts).strip() or _clean_bib_text(fields.get("note", "")).strip()


def _first_author_from_bib_field(author_field: str) -> str:
    if not author_field:
        return ""
    # Split on ' and ' but respect outer braces — `{Planck Collaboration}` stays one author.
    first = _split_first_author(author_field).strip()
    # Brace-wrapped single name w/o internal comma: `{Planck Collaboration}` -> "Planck Collaboration".
    if first.startswith("{") and "," not in _strip_outer_braces(first):
        return _clean_bib_text(first.strip("{}"))
    # BibTeX comma form: "Last, First" (incl. `{Abdalla}, Elcio` which IS comma-form
    # with brace-protected lastname) -> "Last, F."
    if _has_top_level_comma(first):
        last, _, rest = _split_at_top_level_comma(first)
        initials = " ".join(part[0] + "." for part in _clean_bib_text(rest).split() if part)
        return f"{_clean_bib_text(last).strip()}, {initials}".strip(", ")
    # "First Last" -> "Last, F."
    parts = _clean_bib_text(first).split()
    if len(parts) == 1:
        return parts[0]
    last = parts[-1]
    initials = " ".join(p[0] + "." for p in parts[:-1] if p)
    return f"{last}, {initials}"


def _strip_outer_braces(s: str) -> str:
    s = s.strip()
    while s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    return s


def _has_top_level_comma(s: str) -> bool:
    depth = 0
    for c in s:
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "," and depth == 0:
            return True
    return False


def _split_at_top_level_comma(s: str) -> tuple[str, str, str]:
    depth = 0
    for i, c in enumerate(s):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "," and depth == 0:
            return s[:i], ",", s[i + 1:]
    return s, "", ""


def _split_first_author(author_field: str) -> str:
    """Return the substring up to the first top-level ` and ` separator."""
    depth = 0
    i = 0
    while i < len(author_field):
        if author_field[i] == "{":
            depth += 1
        elif author_field[i] == "}":
            depth -= 1
        elif depth == 0 and author_field[i:i + 5].lower() == " and ":
            return author_field[:i]
        i += 1
    return author_field


def _clean_bib_text(text: str) -> str:
    """Strip the most common BibTeX/LaTeX wrappers so citations read cleanly.

    Not exhaustive — anything we don't recognize passes through verbatim.
    """
    if not text:
        return ""
    text = re.sub(r"\\(?:textit|textbf|emph|texttt|mbox|protect)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:url|href)\s*\{[^}]*\}\s*\{?([^{}]*)\}?", r"\1", text)
    text = text.replace("{\\&}", "&").replace("\\&", "&")
    text = re.sub(r"\{\\['\"`^~]([a-zA-Z])\}", r"\1", text)  # {\'e} -> e (lossy but readable)
    text = re.sub(r"\\['\"`^~]\{([a-zA-Z])\}", r"\1", text)
    text = re.sub(r"[{}]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_arxiv_id(raw: str) -> str | None:
    """Turn an `eprint` field or in-text arXiv id into a clean `YYMM.NNNNN`
    (new-style) or `field/YYMMNNN` (pre-2007) form. Returns None if no
    recognizable ID."""
    raw = raw.strip().lower()
    raw = re.sub(r"^arxiv:", "", raw)
    raw = re.sub(r"v\d+$", "", raw)  # drop version
    if re.match(r"^\d{4}\.\d{4,5}$", raw):
        return raw
    if re.match(r"^[a-z\-]+(?:\.[a-z]{2})?/\d{7}$", raw):
        return raw
    return None


def _doi_from_arxiv(arxiv_id: str) -> str:
    return f"10.48550/arXiv.{arxiv_id}"


def _extract_doi_hints_from_text(text: str) -> tuple[str | None, str | None]:
    """Mine free-text bibliography rendering for DOI and arXiv ID.

    Returns (doi, arxiv_id), each None when not found. DOI parsing strips trailing
    punctuation that often follows DOIs in rendered text.
    """
    doi = None
    match = DOI_IN_TEXT.search(text)
    if match:
        doi = match.group(0).rstrip(".,;)\"'>")
    arxiv = None
    match = ARXIV_ID_IN_TEXT.search(text)
    if match:
        arxiv = _normalize_arxiv_id(match.group(1))
    if not arxiv:
        match = ARXIV_BARE.search(text)
        if match:
            arxiv = match.group(1)
    return doi, arxiv


# Bibitem rendering tends to start with the authors (e.g. "Asgari, M., Lin, C.-A.,...").
# Year usually follows in `\d{4}` form. This regex is sloppy on purpose — we just want
# `first-author` for a synthetic key or a Crossref query, not a parser.
LASTNAME_YEAR = re.compile(r"^([A-Z][A-Za-zÀ-ÿ\-']+).*?\b(19\d{2}|20\d{2})\b", re.DOTALL)


def parse_rendered_entry(raw: str) -> dict:
    """Extract first-author lastname + year from a rendered citation paragraph.

    Returns `{"first_author": str, "year": str, "title_guess": str}` — best effort.
    Used to build synthetic keys (Path B) and Crossref title queries.
    """
    cleaned = _clean_bib_text(raw)
    match = LASTNAME_YEAR.match(cleaned)
    first = match.group(1) if match else ""
    year = match.group(2) if match else ""
    # Title guess: take the chunk after the year up to next period that isn't an initial.
    title_guess = ""
    if year:
        tail = cleaned.split(year, 1)[1]
        # Drop a leading delimiter (.,) plus whitespace
        tail = tail.lstrip(",.: ")
        # Title ends at the first period followed by a space + capital letter that introduces
        # journal/volume metadata. Heuristic — good enough for Crossref queries.
        sentence_end = re.search(r"\.\s+[A-Z]", tail)
        title_guess = tail[: sentence_end.start()] if sentence_end else tail
    return {
        "first_author": first.strip(),
        "year": year,
        "title_guess": title_guess.strip().rstrip(".").strip(),
        "raw_clean": cleaned,
    }


def synth_key(first_author: str, year: str, taken: set[str]) -> str:
    """Build a unique synthetic key for a Path B entry.

    `<lastname>_<year>`, lowercased. If the name+year pair already exists in
    `taken`, append a letter suffix (`a`, `b`, ...).
    """
    base = re.sub(r"[^a-z0-9]+", "_", (first_author or "anon").lower()).strip("_")
    if not base:
        base = "anon"
    year = year or "ny"
    candidate = f"{base}_{year}"
    if candidate not in taken:
        return candidate
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        if f"{candidate}{suffix}" not in taken:
            return f"{candidate}{suffix}"
    # 26 collisions is absurd but be safe.
    counter = 1
    while f"{candidate}_{counter}" in taken:
        counter += 1
    return f"{candidate}_{counter}"


# ---------------------------------------------------------------------------
# DOI resolution


class DOIResolver:
    """Resolve a bibliography entry to a DOI string, with on-disk caching.

    Resolution order, returning the first hit:
      1. `doi:` field if present in the parsed entry.
      2. `eprint:` field (or in-text arXiv ID) -> `10.48550/arXiv.<id>`.
      3. Crossref bibliographic query against the cleaned title + first-author.
      4. ADS title search (only if `ADS_API_TOKEN` env var or `~/.ads/dev_key` is present).

    Caches `(title, first_author) -> doi` to `cache_path` so re-runs don't re-hit
    the network. Unresolvable entries cache `None` too — re-running won't retry
    a known miss (delete the cache to force re-resolution).
    """

    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.cache: dict[str, dict] = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                self.cache = {}
        self.ads_key = self._load_ads_key()
        self.network_failures = 0

    @staticmethod
    def _load_ads_key() -> str | None:
        env = os.environ.get("ADS_API_TOKEN") or os.environ.get("ADS_DEV_KEY")
        if env:
            return env.strip()
        for path in (Path.home() / ".ads" / "dev_key", Path.home() / ".config" / "ads" / "dev_key"):
            if path.is_file():
                try:
                    return path.read_text().strip() or None
                except OSError:
                    pass
        return None

    def resolve(
        self,
        title: str,
        first_author: str,
        explicit_doi: str | None = None,
        arxiv_id: str | None = None,
    ) -> tuple[str | None, str]:
        """Resolve to a DOI. Returns `(doi-or-None, source-tag)`.

        `source-tag` is one of `doi-field`, `arxiv-eprint`, `crossref`, `ads`, `unresolved`.
        """
        if explicit_doi:
            return self._normalize_doi(explicit_doi), "doi-field"
        if arxiv_id:
            return _doi_from_arxiv(arxiv_id), "arxiv-eprint"
        cache_key = self._cache_key(title, first_author)
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            return entry.get("doi"), entry.get("source", "unresolved")
        # Network resolution
        doi, source = self._resolve_via_crossref(title, first_author)
        if not doi and self.ads_key:
            doi, source = self._resolve_via_ads(title, first_author)
        self.cache[cache_key] = {"doi": doi, "source": source, "title": title, "first_author": first_author}
        return doi, source

    @staticmethod
    def _normalize_doi(doi: str) -> str:
        doi = doi.strip()
        # Strip URL prefix variants
        doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/)", "", doi, flags=re.IGNORECASE)
        return doi.rstrip(".,;)\"'>")

    @staticmethod
    def _cache_key(title: str, first_author: str) -> str:
        digest = hashlib.sha256(
            f"{title.lower().strip()}||{first_author.lower().strip()}".encode("utf-8")
        ).hexdigest()
        return digest[:24]

    def _resolve_via_crossref(self, title: str, first_author: str) -> tuple[str | None, str]:
        if not title:
            return None, "unresolved"
        query = f"{title} {first_author}".strip()
        url = f"{CROSSREF_API}?query.bibliographic={urllib.parse.quote(query)}&rows=1"
        try:
            data = self._http_get_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            self.network_failures += 1
            return None, "unresolved"
        items = ((data or {}).get("message", {}) or {}).get("items", []) or []
        if not items:
            return None, "unresolved"
        top = items[0]
        candidate_doi = top.get("DOI")
        candidate_titles = top.get("title") or []
        if not candidate_doi:
            return None, "unresolved"
        # Title-similarity gate: drop noisy hits where the top result clearly isn't
        # the paper we asked about.
        if candidate_titles and _title_similarity(title, candidate_titles[0]) < 0.55:
            return None, "unresolved"
        return self._normalize_doi(candidate_doi), "crossref"

    def _resolve_via_ads(self, title: str, first_author: str) -> tuple[str | None, str]:
        if not title:
            return None, "unresolved"
        q = f'title:"{title}"'
        if first_author:
            q += f' author:"{first_author}"'
        params = {"q": q, "fl": "doi,title", "rows": "1"}
        url = f"{ADS_API}?{urllib.parse.urlencode(params)}"
        try:
            data = self._http_get_json(
                url, headers={"Authorization": f"Bearer {self.ads_key}"}
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            self.network_failures += 1
            return None, "unresolved"
        docs = ((data or {}).get("response", {}) or {}).get("docs", []) or []
        if not docs:
            return None, "unresolved"
        doi_list = docs[0].get("doi") or []
        if not doi_list:
            return None, "unresolved"
        return self._normalize_doi(doi_list[0]), "ads"

    @staticmethod
    def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", CROSSREF_USER_AGENT)
        req.add_header("Accept", "application/json")
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        with urllib.request.urlopen(req, timeout=NETWORK_TIMEOUT_S) as resp:
            payload = resp.read()
        return json.loads(payload.decode("utf-8", errors="replace"))

    def save(self) -> None:
        try:
            self.cache_path.write_text(json.dumps(self.cache, indent=2, sort_keys=True))
        except OSError as e:
            print(f"warn: could not write DOI cache: {e}", file=sys.stderr)


def _title_similarity(a: str, b: str) -> float:
    """Stdlib fuzzy ratio in [0, 1]. Used to filter Crossref hits whose top result
    isn't actually the queried paper."""
    a_norm = re.sub(r"\s+", " ", a.lower()).strip()
    b_norm = re.sub(r"\s+", " ", b.lower()).strip()
    if not a_norm or not b_norm:
        return 0.0
    return SequenceMatcher(None, a_norm, b_norm).ratio()


# ---------------------------------------------------------------------------
# Top-level bibliography pipeline


def resolve_bibliography(
    reference_dir: Path,
    bib_path: Path | None,
    bbl_path: Path | None,
    document_md: Path | None,
    extracted_citations: dict[str, list[dict]],
) -> tuple[dict[str, dict], list[str]]:
    """Build the enriched `citations:` block for `index.json`.

    Joins parsed bibliography entries (from `.bib`, then `.bbl`, then `document.md`)
    against `extracted_citations` (from `extract_citations()`). Each key maps to
    `{locations, citation, doi}`; entries the bibliography has but the source
    never cited are dropped (would otherwise be noise); entries cited but missing
    from the bibliography keep `citation: null` and `doi: null` and a warning.
    """
    warnings: list[str] = []
    bib_entries: dict[str, dict] = {}  # key -> {citation, fields, raw, doi_hint, arxiv_hint}

    if bib_path and bib_path.is_file():
        try:
            parsed = parse_bib(bib_path.read_text(errors="replace"))
        except OSError as e:
            warnings.append(f"bibliography: could not read {bib_path.name}: {e}")
            parsed = []
        for entry in parsed:
            fields = entry["fields"]
            citation = format_bib_citation(fields)
            doi_hint = fields.get("doi") or fields.get("DOI".lower())
            arxiv_hint = _normalize_arxiv_id(fields.get("eprint", "") or "") if fields.get("eprint") else None
            bib_entries[entry["key"]] = {
                "citation": citation or None,
                "doi_hint": doi_hint,
                "arxiv_hint": arxiv_hint,
                "title": _clean_bib_text(fields.get("title", "")).strip(),
                "first_author": _first_author_from_bib_field(fields.get("author", "")).split(",")[0],
                "source": "bib",
            }

    if bbl_path and bbl_path.is_file() and not bib_entries:
        # Only fall back to .bbl if no .bib gave us anything.
        try:
            parsed_bbl = parse_bbl(bbl_path.read_text(errors="replace"))
        except OSError as e:
            warnings.append(f"bibliography: could not read {bbl_path.name}: {e}")
            parsed_bbl = []
        for entry in parsed_bbl:
            cleaned = _clean_bib_text(entry["raw"])
            doi_hint, arxiv_hint = _extract_doi_hints_from_text(entry["raw"])
            parsed_rendering = parse_rendered_entry(entry["raw"])
            bib_entries[entry["key"]] = {
                "citation": cleaned or None,
                "doi_hint": doi_hint,
                "arxiv_hint": arxiv_hint,
                "title": parsed_rendering["title_guess"],
                "first_author": parsed_rendering["first_author"],
                "source": "bbl",
            }

    # Path B (document.md): synthetic keys.
    path_b_entries: list[tuple[str, dict]] = []
    if document_md and document_md.is_file():
        paragraphs = parse_doc_references(document_md.read_text(errors="replace"))
        taken: set[str] = set(bib_entries)
        for raw in paragraphs:
            parsed_rendering = parse_rendered_entry(raw)
            doi_hint, arxiv_hint = _extract_doi_hints_from_text(raw)
            key = synth_key(parsed_rendering["first_author"], parsed_rendering["year"], taken)
            taken.add(key)
            path_b_entries.append(
                (
                    key,
                    {
                        "citation": parsed_rendering["raw_clean"] or None,
                        "doi_hint": doi_hint,
                        "arxiv_hint": arxiv_hint,
                        "title": parsed_rendering["title_guess"],
                        "first_author": parsed_rendering["first_author"],
                        "source": "document_md",
                    },
                )
            )

    resolver = DOIResolver(reference_dir / ".doi-cache.json")
    enriched: dict[str, dict] = {}

    # Path A: enrich entries cited at least once in the source.
    for key, locations in extracted_citations.items():
        entry = bib_entries.get(key)
        if entry is None:
            warnings.append(
                f"citation {key}: cited in source but no matching entry in bibliography-source.{{bib,bbl}}"
            )
            enriched[key] = {"locations": locations, "citation": None, "doi": None}
            continue
        doi, _source = resolver.resolve(
            entry["title"], entry["first_author"], entry["doi_hint"], entry["arxiv_hint"]
        )
        if doi is None:
            warnings.append(
                f"citation {key}: could not resolve DOI; tried doi-field, eprint-field, "
                f"Crossref{', ADS' if resolver.ads_key else ''}"
            )
        enriched[key] = {
            "locations": locations,
            "citation": entry["citation"],
            "doi": doi,
        }

    # Path B: every parsed entry lands in the citations block with empty locations.
    # (Citation invocations from rendered prose are a separate substrate-extraction
    # problem we surface in extraction_warnings rather than solve here.)
    for key, entry in path_b_entries:
        doi, _source = resolver.resolve(
            entry["title"], entry["first_author"], entry["doi_hint"], entry["arxiv_hint"]
        )
        if doi is None:
            warnings.append(
                f"citation {key}: could not resolve DOI; tried doi-field, eprint-field, "
                f"Crossref{', ADS' if resolver.ads_key else ''}"
            )
        enriched[key] = {
            "locations": [],
            "citation": entry["citation"],
            "doi": doi,
        }

    if path_b_entries:
        warnings.append(
            "Path B (Docling fallback): citation invocations in rendered prose are not yet "
            "extracted; `locations:` is empty for every Path B citation. Bibliography "
            "entries are still resolved by DOI."
        )

    resolver.save()
    if resolver.network_failures:
        warnings.append(
            f"bibliography: {resolver.network_failures} network failure(s) during DOI "
            "resolution; affected entries cached as unresolved — delete .doi-cache.json "
            "to retry."
        )
    return enriched, warnings


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

    document_md = reference_dir / "document.md"
    citations, bib_warnings = resolve_bibliography(
        reference_dir,
        bib_path=None,
        bbl_path=None,
        document_md=document_md if document_md.is_file() else None,
        extracted_citations={},
    )

    extraction_warnings = [
        "Path B (Docling fallback): title + abstract + outline not yet extracted from "
        "document.md; that's a future refinement."
    ]
    extraction_warnings.extend(bib_warnings)

    index = {
        "schema_version": INDEX_SCHEMA_VERSION,
        "path": "B",
        "paper_pdf": "paper.pdf" if (reference_dir / "paper.pdf").exists() else None,
        "paper_tex": None,
        "source_dir": None,
        "document_md": "document.md" if document_md.is_file() else None,
        "bibliography_source_bib": None,
        "bibliography_source_bbl": None,
        "astra_yaml": astra_rel,
        "title": None,  # Future refinement: parse from Docling's markdown
        "abstract": None,  # Future refinement: parse from Docling's markdown
        "figures": docling.get("figures", []),
        "tables": docling.get("tables", []),
        "outline": [],  # Future refinement: parse Docling's markdown headings
        "citations": citations,
        "extraction_warnings": extraction_warnings,
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
        raw_citations = extract_citations(tex_files, source_dir)
        abstract = extract_abstract(tex_files, macros)
        title = extract_title(tex_files, macros)
        bib_rel, bbl_rel = copy_embedded_bibliography(reference_dir, source_dir)
        citations, bib_warnings = resolve_bibliography(
            reference_dir,
            bib_path=reference_dir / bib_rel if bib_rel else None,
            bbl_path=reference_dir / bbl_rel if bbl_rel else None,
            document_md=None,
            extracted_citations=raw_citations,
        )
        astra_rel = write_astra_yaml_stub(
            reference_dir, args.arxiv_id, args.doi, title, abstract
        )

        paper_tex = reference_dir / "paper.tex"
        index = {
            "schema_version": INDEX_SCHEMA_VERSION,
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
            "extraction_warnings": fig_warnings + tab_warnings + bib_warnings,
        }

        resolved_dois = sum(1 for entry in citations.values() if entry.get("doi"))
        print(
            f"  figures: {len(figures)}, tables: {len(tables)}, "
            f"sections: {len(outline)}, citation-keys: {len(citations)} "
            f"({resolved_dois} with DOI), "
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
