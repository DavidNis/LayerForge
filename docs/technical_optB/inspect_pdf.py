"""Inspect a PDF with pdf.js in headless Chrome (no poppler on this host).

  inspect_pdf.py dests  <pdf>                         -> JSON {pages, dests: {name: page}}
  inspect_pdf.py render <pdf> <first> <last> <outdir> -> page_NNN.png per page

The page posts its result back to a local HTTP server; nothing is fetched
from the network.
"""

from __future__ import annotations

import base64
import http.server
import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "build" / "inspect"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

PAGE = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<script type="module">
import * as pdfjs from './pdf.min.mjs';
pdfjs.GlobalWorkerOptions.workerSrc = './pdf.worker.min.mjs';
const post = (body) => fetch('/result', {method: 'POST', body: JSON.stringify(body)});
try {
  const pdf = await pdfjs.getDocument({url: './doc.pdf', disableRange: true, disableStream: true}).promise;
  if ('%MODE%' === 'dests') {
    const dests = await pdf.getDestinations(); const out = {pages: pdf.numPages, dests: {}};
    for (const [name, d] of Object.entries(dests)) out.dests[name] = (await pdf.getPageIndex(d[0])) + 1;
    await post(out);
  } else {
    const pages = {};
    for (let n = %FIRST%; n <= Math.min(%LAST%, pdf.numPages); n++) {
      const page = await pdf.getPage(n); const vp = page.getViewport({scale: %SCALE%});
      const c = document.createElement('canvas'); c.width = vp.width; c.height = vp.height;
      const ctx = c.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);
      await page.render({canvasContext: ctx, viewport: vp}).promise;
      pages[n] = c.toDataURL('image/png');
    }
    await post({pages});
  }
} catch (e) { await post({error: String(e)}); }
</script></body></html>"""

RESULT: dict = {}
DONE = threading.Event()


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WORK), **kwargs)

    def log_message(self, *args):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except ConnectionError:
            pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        RESULT.update(json.loads(body))
        self.send_response(200)
        self.end_headers()
        DONE.set()


def run(mode: str, pdf: str, first=1, last=1, scale=1.0) -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    for name in ("pdf.min.mjs", "pdf.worker.min.mjs"):
        shutil.copy(HERE / "vendor" / name, WORK / name)
    shutil.copy(pdf, WORK / "doc.pdf")
    page = PAGE.replace("%MODE%", mode).replace("%FIRST%", str(first)).replace("%LAST%", str(last)).replace("%SCALE%", str(scale))
    (WORK / "index.html").write_text(page, encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={HERE / 'build' / 'profile_inspect'}",
         f"http://127.0.0.1:{server.server_port}/index.html"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not DONE.wait(300):
            raise TimeoutError("pdf.js page did not report back")
    finally:
        proc.kill()
        server.shutdown()
    if "error" in RESULT:
        raise RuntimeError(RESULT["error"])
    return RESULT


if __name__ == "__main__":
    if sys.argv[1] == "dests":
        print(json.dumps(run("dests", sys.argv[2])))
    else:
        out = Path(sys.argv[5])
        out.mkdir(parents=True, exist_ok=True)
        scale = float(sys.argv[6]) if len(sys.argv) > 6 else 1.0
        result = run("render", sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), scale)
        for n, url in result["pages"].items():
            (out / f"page_{int(n):03d}.png").write_bytes(base64.b64decode(url.split(",", 1)[1]))
        print("rendered", sorted(int(n) for n in result["pages"]))
