"""Build LayerForge_Technical_Architecture_Explained.pdf (Option B scope).

Read-only with respect to the project: it reads chapter fragments from
``chapters/``, source files (for verified references and code excerpts) and
``examples.json``; it writes only ``build/`` and the final PDF.

Markup used inside chapter fragments:
  <span class="m">TeX</span>             inline math (KaTeX -> MathML in Chrome)
  <div class="mb">TeX</div>              display math
  <span class="ref" data-sym="S">path:N</span>
                                         source reference; line N of path must contain S
  <pre class="code" data-file="path" data-lines="a-b"></pre>
                                         code excerpt copied from the real file
  <div class="bits" data-src="key.sub"></div>
                                         0/1 grid from examples.json
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = HERE / "build"
OUT = ROOT / "output" / "pdf" / "optB" / "LayerForge_Technical_Architecture_Explained.pdf"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
EXAMPLES = json.loads((HERE / "examples.json").read_text(encoding="utf-8"))

HEAD = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<title>LayerForge Technical Architecture</title><link rel="stylesheet" href="style.css"></head><body>"""
TAIL = """<script src="vendor/katex.min.js"></script><script>
for (const [sel, display] of [['.m', false], ['.mb', true]])
  for (const e of document.querySelectorAll(sel)) {
    const tex = e.textContent;
    try { katex.render(tex, e, {output: 'mathml', displayMode: display, throwOnError: true}); }
    catch (err) { e.classList.add('texerr'); e.textContent = 'TeX error: ' + err.message; }
  }
</script></body></html>"""


def verify_refs(text: str, errors: list[str]) -> int:
    count = 0
    for sym, ref in re.findall(r'<span class="ref" data-sym="([^"]+)">([^<]+)</span>', text):
        count += 1
        path, _, line = ref.rpartition(":")
        file = ROOT / path
        if not file.exists():
            errors.append(f"missing file {path}")
            continue
        lines = file.read_text(encoding="utf-8").splitlines()
        n = int(line)
        if n > len(lines) or html.unescape(sym) not in lines[n - 1]:
            errors.append(f"{ref}: '{sym}' not on that line: {lines[n - 1] if n <= len(lines) else 'EOF'}")
    return count


def fill_code(text: str, errors: list[str]) -> str:
    def repl(match: re.Match) -> str:
        path, a, b = match.group(1), int(match.group(2)), int(match.group(3))
        file = ROOT / path
        if not file.exists():
            errors.append(f"code excerpt: missing {path}")
            return ""
        lines = file.read_text(encoding="utf-8").splitlines()[a - 1 : b]
        body = "\n".join(f'<span class="ln">{a + i:>4}</span>  {html.escape(l)}' for i, l in enumerate(lines))
        return f'<pre class="code" dir="ltr"><span class="src">{path}:{a}-{b}</span>\n{body}</pre>'

    return re.sub(r'<pre class="code" data-file="([^"]+)" data-lines="(\d+)-(\d+)"></pre>', repl, text)


def fill_bits(text: str) -> str:
    def repl(match: re.Match) -> str:
        value = EXAMPLES
        for part in match.group(1).split("."):
            value = value[part]
        rows = "".join(
            "<tr>" + "".join(f'<td class="b{int(v) if int(v) < 3 else 3}">{int(v) if match.group(2) else ""}</td>' for v in row) + "</tr>"
            for row in value
        )
        return f'<table class="bits" dir="ltr">{rows}</table>'

    return re.sub(r'<div class="bits" data-src="([^"]+)"( data-num="1")?></div>', repl, text)


def chapters() -> list[tuple[str, str]]:
    return [(p.stem, p.read_text(encoding="utf-8")) for p in sorted((HERE / "chapters").glob("*.html"))]


def toc_html(entries: list[tuple[str, str, int, int | None]]) -> str:
    rows = []
    for anchor, title, level, page in entries:
        number = str(page) if page else ""
        rows.append(
            f'<div class="toc{level}"><a href="#{anchor}">{title}</a><span class="dots"></span><span class="pg">{number}</span></div>'
        )
    return '<section class="chapter toc" id="toc"><h1 class="nonum">תוכן עניינים</h1>' + "".join(rows) + "</section>"


def render(body: str, name: str) -> Path:
    page = BUILD / f"{name}.html"
    page.write_text(HEAD + body + TAIL, encoding="utf-8")
    pdf = BUILD / f"{name}.pdf"
    if pdf.exists():
        pdf.unlink()
    subprocess.run(
        [str(CHROME), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
         f"--user-data-dir={BUILD / 'profile'}", "--virtual-time-budget=15000",
         f"--print-to-pdf={pdf}", page.as_uri()],
        check=True, capture_output=True, timeout=300,
    )
    return pdf


def page_count(pdf: Path) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", pdf.read_bytes()))


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    for item in ("style.css",):
        shutil.copy(HERE / item, BUILD / item)
    shutil.copytree(HERE / "vendor", BUILD / "vendor", dirs_exist_ok=True)
    shutil.copytree(HERE / "fig", BUILD / "fig", dirs_exist_ok=True)

    errors: list[str] = []
    parts = chapters()
    refs = 0
    processed = []
    for name, text in parts:
        refs += verify_refs(text, errors)
        text = re.sub(r'(<span class="ref" data-sym="[^"]+">)([^<]+)(</span>)',
                      lambda m: m.group(1) + m.group(2).replace("/", "/<wbr>") + m.group(3), text)
        processed.append((name, fill_bits(fill_code(text, errors))))
    if errors:
        print("\n".join(errors))
        sys.exit(1)

    # Number chapters (h1) and sections (h2) and collect TOC entries.
    entries: list[tuple[str, str, int, str]] = []
    numbered = []
    chapter_no = 0
    for name, text in processed:
        if name.startswith("00"):
            numbered.append((name, text))
            continue
        chapter_no += 1
        section_no = 0

        def h1(match: re.Match) -> str:
            entries.append((f"c{chapter_no}", f"{chapter_no}. {match.group(1)}", 1, name))
            return f'<h1 id="c{chapter_no}">{chapter_no}. {match.group(1)}</h1>'

        def h2(match: re.Match) -> str:
            nonlocal section_no
            section_no += 1
            label = f"{chapter_no}.{section_no} {match.group(1)}"
            entries.append((f"c{chapter_no}s{section_no}", label, 2, name))
            return f'<h2 id="c{chapter_no}s{section_no}">{label}</h2>'

        text = re.sub(r"<h1>(.*?)</h1>", h1, text, count=1)
        text = re.sub(r"<h2>(.*?)</h2>", h2, text)
        numbered.append((name, f'<section class="chapter" data-name="{name}">{text}</section>'))

    front = [n for n, _ in numbered if n.startswith("00")]

    def assemble(pages: dict[str, int]) -> str:
        toc = toc_html([(a, t, l, pages.get(a, 0)) for a, t, l, _ in entries])
        return "".join(t for n, t in numbered if n in front) + toc + "".join(t for n, t in numbered if n not in front)

    # Pass 1 renders with placeholder numbers; pdf.js reads the named
    # destinations (heading ids) to find the real page of every heading.
    sys.path.insert(0, str(HERE))
    from inspect_pdf import run as inspect

    pages = {a: 0 for a, *_ in entries}
    for attempt in range(3):
        final = render(assemble(pages), "LayerForge_Technical_Architecture_Explained")
        found = inspect("dests", str(final))
        measured = {a: found["dests"].get(a, 0) for a, *_ in entries}
        if measured == pages:
            break
        pages = measured
        import inspect_pdf
        inspect_pdf.RESULT.clear(); inspect_pdf.DONE.clear()
    from find_overflow import check

    overflow = check()["bad"]
    if overflow:
        print("OVERFLOW (would shrink pages):", json.dumps(overflow, ensure_ascii=False)[:2000])
        sys.exit(2)
    missing = [a for a, v in pages.items() if not v]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(final, OUT)
    print(json.dumps({"verified_refs": refs, "pages": found["pages"], "toc_stable": measured == pages,
                      "passes": attempt + 1, "missing_dests": missing,
                      "chapter_start": {a: v for a, v in pages.items() if "s" not in a}, "pdf": str(OUT)}, indent=1))


if __name__ == "__main__":
    main()
