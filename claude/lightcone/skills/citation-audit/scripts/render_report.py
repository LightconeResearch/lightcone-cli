#!/usr/bin/env python3
"""render_report.py — the per-citation citation-audit HTML report.

One entry per cited use-site, on the shuttle editorial parchment palette
(Fraunces display + EB Garamond body + IBM Plex Mono labels). Reads the
ledger (every row), renders each cite as a collapsed card showing just the
manuscript's citing sentence; expanding it fades the surrounding sentences in
*around* the cite (grow-around), then lists the supporting evidence — one
verbatim quote per facet, attributed to the cited paper.

Entries sort by **severity** (worst first: wrong_paper / unsupported / weak /
unverifiable on top, supported below) or **appearance** (manuscript order),
toggled with a CSS-only radio control — no JS.

There is **one verification path** and no `identity` rendering: a cite is
`supported` / `weak` / `unsupported` / `wrong_paper` / `unverifiable`, or one of
the pre-verification ledger states (`unverifiable_no_doi`, `extraction_error`,
`pending`). Each `supported`/`weak` row carries `anchors` — a list of one quote
per facet.

Self-contained but for Google-Fonts `<link>`s (graceful serif fallback offline)
so it lands cleanly on a phone via `SendUserFile`.

Usage:

    python3 render_report.py \\
        --ledger work/citation-audit/ledger.json \\
        --reference-dir work/reference \\
        --out work/citation-audit/report.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

# Severity ordering: LOWER renders first (most urgent on top). Anything not
# listed falls back to the `unverifiable` band.
_SEV = {
    "wrong_paper": 0,
    "unsupported": 1,
    "weak": 2,
    "unverifiable": 3,
    "unverifiable_no_doi": 3,
    "extraction_error": 3,
    "pending": 4,
    "supported": 5,
}
_WORD = {
    "supported": "supported",
    "weak": "partial",
    "unsupported": "unsupported",
    "wrong_paper": "wrong paper",
    "unverifiable": "unverifiable",
    "unverifiable_no_doi": "no doi",
    "extraction_error": "extract error",
    "pending": "pending",
}
_ICON = {"supported": "●", "weak": "◑", "unsupported": "✕", "wrong_paper": "✕"}
_CLS = {
    "supported": "ok",
    "weak": "warn",
    "unsupported": "bad",
    "wrong_paper": "bad",
}

# Verdicts that warrant a second look (everything that isn't clean support and
# isn't still pending).
_FLAGGED = {
    "weak",
    "unsupported",
    "wrong_paper",
    "unverifiable",
    "unverifiable_no_doi",
    "extraction_error",
}

_SUPS = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBS = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def _sev(v: str) -> int:
    return _SEV.get(v, 3)


def _word(v: str) -> str:
    return _WORD.get(v, v.replace("_", " "))


def _icon(v: str) -> str:
    return _ICON.get(v, "○")


def _cls(v: str) -> str:
    return _CLS.get(v, "mute")


def _yr4(digits: str) -> str:
    """2- or 4-digit year string → 4-digit (`93`→`1993`, `08`→`2008`, pivot 50)."""
    if len(digits) == 4:
        return digits
    y = int(digits)
    return f"20{y:02d}" if y < 50 else f"19{y:02d}"


def author_year(key: str) -> str:
    """Best-effort `Author Year` from a bibkey, across naming conventions.

    Dotted (`asgari.etal19a` → `Asgari et al. 2019`) and concatenated
    (`Gwyn2008` → `Gwyn 2008`, `Bertin96` → `Bertin 1996`, `LandySzalay1993`
    → `Landy-Szalay 1993`, `HervasPeters2024` → `Hervas-Peters 2024`). The
    concatenated forms are what hand-built `.bib` files (and ADS exports) use;
    the original dotted-only parser mangled them into `Gwyn2008 2008`."""
    if "." in key:  # dotted convention: lastname.etalYY[a]
        parts = key.split(".")
        name = parts[0].replace("_", " ").title()
        etal = any(p.startswith("etal") for p in parts)
        m = re.search(r"(\d{2})[a-z]?$", parts[-1])
        yr = _yr4(m.group(1)) if m else ""
        return f"{name}{' et al.' if etal else ''} {yr}".strip()
    m = re.match(r"^(.*?)(\d{4}|\d{2})[a-z]?$", key)  # concatenated: Name + year
    if not m or not m.group(1):
        return key
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", "-", m.group(1).replace("_", " "))  # LandySzalay → Landy-Szalay
    return f"{name} {_yr4(m.group(2))}".strip()


def _unit(u: str) -> str:
    return u.replace(r"\square\deg", "deg²").replace(r"\deg", "deg").replace(r"\square", "").strip()


# Any natbib cite command: \citep \citet \citealt \citealp \citeauthor \cite … ,
# with an optional `*`, 0–2 optional `[pre][post]` note groups, and a comma-list
# of keys. Citations name *what is cited*; the citing sentence must show them.
_CITE_CMD = re.compile(r"\\(cite[a-zA-Z]*)\*?((?:\[[^\]]*\])*)\{([^}]*)\}")
_CITE_TEXTUAL = ("citet", "citealt", "citeauthor", "citetalias")  # rendered inline, no parens


def _stash_citations(s: str, highlight: str | None, sink: list[str]) -> str:
    """Replace each cite command with an indexed sentinel, pushing pre-built HTML
    into `sink`. The \\citep family renders parenthetically, the \\citet family
    inline; every key becomes an author-year, and the audited key (`highlight`) is
    marked so each card visibly declares which cite it audits. natbib note
    semantics: one bracket = post-note, two = [pre][post]."""
    def repl(m: "re.Match[str]") -> str:
        cmd = m.group(1).lower()
        brackets = re.findall(r"\[([^\]]*)\]", m.group(2) or "")
        pre = brackets[0].strip() if len(brackets) >= 2 else ""
        post = (brackets[1] if len(brackets) >= 2 else brackets[0]).strip() if brackets else ""
        keys = [k.strip() for k in m.group(3).split(",") if k.strip()]

        def name(k: str) -> str:
            ay = html.escape(author_year(k))
            return f"<mark class='auditcite'>{ay}</mark>" if highlight and k == highlight else ay

        body = ", ".join(name(k) for k in keys)
        pre_s = f"{html.escape(pre)} " if pre else ""
        post_s = f", {html.escape(post)}" if post else ""
        inner = f"{pre_s}{body}{post_s}"
        if not any(cmd.startswith(t) for t in _CITE_TEXTUAL):
            inner = f"({inner})"
        idx = len(sink)
        sink.append(f"<span class='cit'>{inner}</span>")
        return f"§§H§§{idx}§§/H§§"

    return _CITE_CMD.sub(repl, s)


def latex_clean(s: str, keep_citet: bool = True, highlight: str | None = None) -> str:
    """Render a LaTeX prose fragment to safe display HTML.

    Renders every natbib cite to visible author-year (parenthetical for the
    \\citep family, inline for \\citet), highlighting the audited key so the
    citing sentence shows *what is cited* — never silently dropped. Lifts
    \\texttt to <code> and converts inline math to unicode super/subscripts.
    Lossy by design — this is for human reading, not re-verification.
    `keep_citet=False` (titles, notes) strips cites instead.
    """
    if not s:
        return ""
    cite_html: list[str] = []
    if keep_citet:
        s = _stash_citations(s, highlight, cite_html)
    s = re.sub(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{[^}]*\}", "", s)  # drop any leftover / keep_citet=False
    s = re.sub(r"\\(cref|ref|label|eqref)\{[^}]*\}", "", s)
    s = re.sub(r"\\paper[a-z]*\{?\}?", "", s)
    s = re.sub(r"\\texttt\{([^}]*)\}", r"⟦\1⟧", s)
    s = re.sub(r"\\SI\{([^}]*)\}\{([^}]*)\}", lambda m: m.group(1) + " " + _unit(m.group(2)), s)
    s = re.sub(r"\\num\{([^}]*)\}", r"\1", s)

    def math(m: "re.Match[str]") -> str:
        x = m.group(1)
        for a, b in [
            (r"\lcdm", "ΛCDM"), (r"\Lambda", "Λ"), (r"\Omega_{\rm m}", "Ωₘ"), (r"\Om", "Ωₘ"),
            (r"\Omega", "Ω"), (r"\sigma_8", "σ₈"), (r"\sigma", "σ"), (r"\sim", "∼"), (r"\equiv", "≡"),
            (r"\sqrt", "√"), (r"\%", "%"), (r"\,", ""), (r"\rm", ""), (r"\ell", "ℓ"), (r"\times", "×"),
        ]:
            x = x.replace(a, b)
        x = x.replace("~", " ")
        x = re.sub(r"\^\{([0-9+\-=()n]+)\}", lambda g: g.group(1).translate(_SUPS), x)
        x = re.sub(r"_\{([0-9+\-=()]+)\}", lambda g: g.group(1).translate(_SUBS), x)
        x = re.sub(r"\^([0-9])", lambda g: g.group(1).translate(_SUPS), x)
        x = re.sub(r"_([0-9])", lambda g: g.group(1).translate(_SUBS), x)
        return f"§§M§§{re.sub(r'[{}]', '', x)}§§/M§§"

    s = re.sub(r"\$([^$]*)\$", math, s)
    s = s.replace(r"\%", "%").replace(r"\,", "").replace("~", " ").replace(r"\&", "&")
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"\s+([.,;])", r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    out = []
    for t in re.split(r"(§§M§§.*?§§/M§§|⟦[^⟧]*⟧|§§H§§\d+§§/H§§)", s):
        if t.startswith("§§M§§"):
            out.append(f"<span class='m'>{html.escape(t[5:-6])}</span>")
        elif t.startswith("§§H§§"):
            out.append(cite_html[int(t[5:-6])])  # pre-built, already-safe citation HTML
        elif t.startswith("⟦"):
            out.append(f"<code>{html.escape(t[1:-1])}</code>")
        else:
            out.append(html.escape(t))
    return "".join(out)


def _anchors(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The row's anchors, normalizing a legacy single `quote` to a 1-list."""
    anchors = row.get("anchors")
    if anchors:
        return anchors
    q = row.get("quote")
    if isinstance(q, dict) and q.get("exact"):
        loc = row.get("location") or {}
        return [{
            "exact": q.get("exact"),
            "section": loc.get("section") or loc.get("value"),
            "substrate": "tex",
        }]
    return []


def _paper_title(reference_dir: Path) -> str:
    idx = reference_dir / "index.json"
    if idx.exists():
        try:
            meta = json.loads(idx.read_text())
        except (json.JSONDecodeError, OSError):
            meta = {}
        title = meta.get("title")
        if title:
            return latex_clean(str(title), keep_citet=False)
    return ""


def _vbadge(verdict: str, n_anchors: int) -> str:
    if verdict in {"supported", "weak"}:
        return f"{n_anchors} source" + ("" if n_anchors == 1 else "s")
    if verdict in {"unsupported", "wrong_paper"}:
        return "0 backed"
    return "—"


def _entry_html(row: dict[str, Any], app_index: int) -> str:
    verdict = row.get("verdict") or "pending"
    cls = _cls(verdict)
    ck = row.get("citation_key") or ""
    line = row.get("line", "?")
    anchors = _anchors(row)

    cur = latex_clean(row.get("claim") or "", highlight=ck)
    prev = latex_clean(row.get("manuscript_prefix") or "", highlight=ck)
    nxt = latex_clean(row.get("manuscript_suffix") or "", highlight=ck)

    facets = []
    lede_html = ""  # first supporting quote, shown collapsed so the evidence is visible up front
    for a in anchors:
        q = latex_clean(a.get("exact", ""))
        if not q:
            continue
        sec = a.get("section") or ""
        facet = a.get("facet")
        pdf = " · pdf" if a.get("substrate") == "pdf" else ""
        attrib = author_year(ck) + (f" · §&#8202;{html.escape(str(sec))}" if sec else "") + pdf
        flh = f"<span class='fl'>{html.escape(str(facet))}</span>" if facet else ""
        facets.append(f"<blockquote>{flh}“{q}”<cite>{attrib}</cite></blockquote>")
        if not lede_html:
            lede_html = f"<blockquote class='lede'>“{q}”<cite>{html.escape(author_year(ck))}</cite></blockquote>"
    if facets:
        ev = "\n".join(facets)
    elif verdict in {"supported", "weak"}:
        ev = "<p class='none'>No quotable support survived the source gate.</p>"
    elif verdict == "wrong_paper":
        ev = "<p class='none'>The cited source is a different paper than the cite intends.</p>"
    else:
        ev = "<p class='none'>No quotable support in the cited source.</p>"

    notes = row.get("verdict_notes")
    notes_html = f"<div class='notes'>{latex_clean(str(notes), keep_citet=False)}</div>" if notes else ""
    rew = row.get("suggested_rewording")
    rew_html = f"<div class='rew'>{latex_clean(str(rew), keep_citet=False)}</div>" if rew else ""
    flag = row.get("doi_flag")
    flag_html = f"<div class='flag'>{latex_clean(str(flag), keep_citet=False)}</div>" if flag else ""

    before = f"<span class='ctx'>{prev} </span>" if prev else ""
    after = f"<span class='ctx'> {nxt}</span>" if nxt else ""
    pdfb = "<span class='pdf'>via&#8202;PDF</span>" if any(a.get("substrate") == "pdf" for a in anchors) else ""

    sentence = cur or f"<em class='ctx'>(no manuscript sentence captured for {html.escape(ck)})</em>"

    return f"""
<details class="entry {cls}" style="--sev:{_sev(verdict)};--app:{app_index}">
  <summary>
    <div class="meta">
      <span class="left"><span class="ic">{_icon(verdict)}</span>
        <span class="vw">{html.escape(_word(verdict))}</span>
        <span class="src">{_vbadge(verdict, len(facets))}</span>{pdfb}</span>
      <span class="right"><span class="citekey">{html.escape(author_year(ck))}</span>
        <span class="loc">{html.escape(ck)} · L{html.escape(str(line))}</span>
        <span class="chev">›</span></span>
    </div>
    <p class="sentence">{before}<span class="cited">{sentence}</span>{after}</p>
    {lede_html}
  </summary>
  <div class="body"><div class="ev">{flag_html}{ev}{notes_html}{rew_html}</div></div>
</details>"""


_CSS = """
:root{
  --parchment:#E9DDC7; --rag:#FBF6EA; --ink:#211B13; --muted:#8A7C63;
  --cinnabar:#BC4538; --teal:#3F8278; --ochre:#B9842B;
  --rule:#CDBD9F; --display:'Fraunces',Georgia,serif; --serif:'EB Garamond',Georgia,serif;
  --mono:'IBM Plex Mono',ui-monospace,monospace;
}
*{box-sizing:border-box} html,body{margin:0;padding:0}
body{background:var(--parchment);color:var(--ink);font-family:var(--serif);font-size:21px;line-height:1.55;-webkit-font-smoothing:antialiased}
.page{max-width:740px;margin:0 auto;padding:58px 26px 90px}
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:var(--muted);margin-bottom:14px}
h1{font-family:var(--display);font-weight:500;font-size:46px;line-height:1.04;letter-spacing:-.015em;margin:0 0 6px}
.subtitle{color:var(--muted);font-style:italic;font-size:18px;margin:0 0 22px}
.summary{font-size:20px;margin:0 0 18px;max-width:33em}
.summary b{font-weight:500}
.chips{display:flex;flex-wrap:wrap;gap:9px;margin:0 0 2px}
.chip{font-family:var(--mono);font-size:11px;letter-spacing:.03em;padding:4px 11px;border-radius:20px;background:var(--rag);border:1px solid var(--rule);color:var(--muted)}
.chip.ok{color:var(--teal)} .chip.warn{color:var(--ochre)} .chip.bad{color:var(--cinnabar)}

.sortrow{position:absolute;opacity:0;pointer-events:none}
.sortbar{display:flex;align-items:center;font-family:var(--mono);font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:28px 0 14px}
.sortbar .lbl{margin-right:12px}
.sortbar label{cursor:pointer;padding:5px 13px;border:1px solid var(--rule);color:var(--muted)}
.sortbar label[for=sortSev]{border-radius:5px 0 0 5px;border-right:none}
.sortbar label[for=sortApp]{border-radius:0 5px 5px 0}
#sortSev:checked~.sortbar label[for=sortSev],#sortApp:checked~.sortbar label[for=sortApp]{background:var(--ink);color:var(--rag);border-color:var(--ink)}

#list{display:flex;flex-direction:column;gap:10px}
.entry{order:var(--sev);background:var(--rag);border:1px solid var(--rule);border-radius:8px;animation:rise .5s both}
#sortApp:checked~#list .entry{order:var(--app)}
@keyframes rise{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.entry summary{list-style:none;cursor:pointer;padding:16px 22px;border-radius:8px;transition:background .15s}
.entry summary::-webkit-details-marker{display:none}
.entry summary:hover{background:#fff}
.meta{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:10px;font-family:var(--mono);font-size:11px;letter-spacing:.03em;color:var(--muted)}
.meta .left{display:flex;align-items:center;gap:9px}
.meta .right{display:flex;align-items:center;gap:11px;white-space:nowrap}
.ic{font-size:9px}
.ok .ic{color:var(--teal)} .warn .ic{color:var(--ochre)} .bad .ic{color:var(--cinnabar)} .mute .ic{color:var(--muted)}
.vw{text-transform:uppercase;letter-spacing:.1em;font-weight:500}
.ok .vw{color:var(--teal)} .warn .vw{color:var(--ochre)} .bad .vw{color:var(--cinnabar)} .mute .vw{color:var(--muted)}
.src{color:var(--muted)}
.pdf{font-size:9px;letter-spacing:.05em;text-transform:uppercase;border:1px solid var(--rule);border-radius:3px;padding:1px 5px}
.citekey{color:var(--ink);font-weight:600;letter-spacing:.02em}
.loc{color:var(--muted)}
.chev{font-size:18px;line-height:1;color:var(--muted);transform:rotate(90deg);transition:transform .2s ease}
.entry[open] .chev{transform:rotate(-90deg)}
.sentence{margin:0;font-size:21px;line-height:1.5;color:var(--ink)}
.ctx{display:none;color:var(--muted)}
.entry[open] .ctx{display:inline;animation:ctxin .4s both}
@keyframes ctxin{from{opacity:0}to{opacity:1}}
.cited{text-decoration:none}
.entry[open] .cited{text-decoration:underline;text-decoration-color:rgba(188,69,56,.4);text-decoration-thickness:1.5px;text-underline-offset:3px}

.body{padding:0 22px 6px}
.ev{padding:16px 0 18px;margin-top:6px;border-top:1px solid var(--rule)}
.ev blockquote{margin:0 0 15px;font-size:18px;line-height:1.5;color:var(--ink);padding-left:15px;border-left:2px solid var(--ochre)}
.bad .ev blockquote,.warn .ev blockquote{border-left-color:var(--rule)}
.ev blockquote:last-child{margin-bottom:0}
.ev cite,.lede cite{display:block;font-family:var(--mono);font-style:normal;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin-top:5px}
.fl{display:block;font-family:var(--mono);font-style:normal;font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--teal);margin-bottom:3px}
.bad .fl,.warn .fl{color:var(--muted)}
/* lede: the first supporting quote, shown collapsed so evidence is visible up front; hidden when expanded (the full facet list takes over) */
.lede{margin:11px 0 0;font-size:18px;line-height:1.45;color:var(--ink);padding-left:15px;border-left:2px solid var(--ochre)}
.bad .lede,.warn .lede{border-left-color:var(--rule)}
.entry[open] .lede{display:none}
.ev cite{display:block;font-style:normal;font-family:var(--mono);font-size:10.5px;color:var(--muted);margin-top:5px;letter-spacing:.02em}
.none{font-style:italic;color:var(--cinnabar);margin:0}
.notes{margin-top:10px;font-size:16px;color:var(--muted);white-space:pre-line}
.rew{margin-top:8px;font-size:17px;background:rgba(185,132,43,.12);border-radius:5px;padding:11px 14px}
.rew::before{content:'suggested rewording';display:block;font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--ochre);margin-bottom:4px}
.flag{margin-bottom:12px;font-size:16px;background:rgba(188,69,56,.1);border-radius:5px;padding:11px 14px;color:var(--cinnabar)}
.flag::before{content:'resolver flag';display:block;font-family:var(--mono);font-size:9px;letter-spacing:.12em;text-transform:uppercase;margin-bottom:4px}
.m{font-variant-numeric:tabular-nums}
.cit{color:var(--muted);font-size:.92em}
mark.auditcite{background:rgba(188,69,56,.13);color:var(--cinnabar);font-weight:600;padding:0 2px;border-radius:2px}
code{font-family:var(--mono);font-size:.82em;background:rgba(185,132,43,.15);padding:1px 4px;border-radius:2px}
"""


def render(ledger_path: Path, reference_dir: Path, out_path: Path) -> int:
    ledger = json.loads(ledger_path.read_text())
    rows: list[dict[str, Any]] = ledger.get("rows", [])
    # Manuscript order: by (file, line). Stable index used by the "appearance" sort.
    rows = sorted(rows, key=lambda r: (r.get("file") or "", r.get("line") or 0))

    counts: dict[str, int] = {}
    for r in rows:
        v = r.get("verdict") or "pending"
        counts[v] = counts.get(v, 0) + 1
    total = len(rows)
    supported = counts.get("supported", 0)
    flagged = sum(n for v, n in counts.items() if v in _FLAGGED)

    chips = " ".join(
        f"<span class='chip {_cls(v)}'>{counts[v]} {html.escape(_word(v))}</span>"
        for v in sorted(counts, key=_sev)
    )
    entries = "".join(_entry_html(r, i) for i, r in enumerate(rows))
    title = _paper_title(reference_dir)
    subtitle = f"<p class='subtitle'>{title}</p>" if title else ""

    doc = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Citation audit</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;0,9..144,500;1,9..144,400&family=EB+Garamond:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>{_CSS}</style></head>
<body><main class="page">
  <input type="radio" name="sort" id="sortSev" class="sortrow" checked>
  <input type="radio" name="sort" id="sortApp" class="sortrow">
  <div class="kicker">Citation audit</div>
  <h1>Every cite, checked.</h1>
  {subtitle}
  <p class="summary"><b>{supported} of {total}</b> citations are backed by the paper they point to. <b>{flagged}</b> want a second look — shown first below. Click any citation to read the evidence, quoted from the source.</p>
  <div class="chips">{chips}</div>
  <div class="sortbar"><span class="lbl">sort</span>
    <label for="sortSev">severity</label><label for="sortApp">appearance</label></div>
  <div id="list">
{entries}
  </div>
</main></body></html>"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc)
    print(f"wrote {out_path} ({len(doc)} bytes, {total} entries; {flagged} flagged)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ledger", type=Path, default=Path("work/citation-audit/ledger.json"))
    parser.add_argument("--reference-dir", type=Path, default=Path("work/reference"))
    parser.add_argument("--out", type=Path, default=Path("work/citation-audit/report.html"))
    args = parser.parse_args()

    if not args.ledger.exists():
        print(f"error: ledger not found at {args.ledger}", file=sys.stderr)
        return 2
    return render(args.ledger, args.reference_dir, args.out)


if __name__ == "__main__":
    sys.exit(main())
