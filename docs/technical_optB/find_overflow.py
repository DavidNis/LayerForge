"""List elements of the built HTML that are wider than the A4 content box."""

from __future__ import annotations

import http.server
import json
import subprocess
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SCRIPT = """<script>
setTimeout(() => {
  const limit = document.body.getBoundingClientRect();
  const bad = []; const seen = new Set();
  for (const e of document.body.querySelectorAll('*')) {
    const r = e.getBoundingClientRect();
    if (!(r.width && (r.left < limit.left - 1 || r.right > limit.right + 1))) continue;
    const box = e.closest('.mb, .m, pre, table, figure, .flow, .arch, .bitsrow, li, p, h1, h2, h3') || e;
    if (seen.has(box)) continue; seen.add(box);
    const br = box.getBoundingClientRect();
    bad.push({tag: box.tagName + '.' + (typeof box.className === 'string' ? box.className : ''), w: Math.round(Math.max(r.right, br.right) - Math.min(r.left, br.left)),
              text: (box.textContent || '').slice(0, 90)});
  }
  for (const e of document.querySelectorAll('.texerr')) bad.push({tag: 'TEX', w: 0, text: e.textContent.slice(0, 160)});
  fetch('/result', {method: 'POST', body: JSON.stringify({body: Math.round(limit.width),
    bad: bad.map(({el, ...rest}) => rest)})});
}, 3000);
</script>"""

RESULT: dict = {}
DONE = threading.Event()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BUILD), **kwargs)

    def log_message(self, *args):
        pass

    def do_POST(self):
        RESULT.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.end_headers()
        DONE.set()


def check() -> dict:
    RESULT.clear(); DONE.clear()
    page = (BUILD / "LayerForge_Technical_Architecture_Explained.html").read_text(encoding="utf-8")
    (BUILD / "overflow_check.html").write_text(page.replace("</body>", SCRIPT + "</body>"), encoding="utf-8")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # 178 mm content width at 96 dpi = 673 px
    proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--window-size=673,900",
                             f"--user-data-dir={BUILD / 'profile_overflow'}",
                             f"http://127.0.0.1:{server.server_port}/overflow_check.html"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    DONE.wait(120)
    proc.kill()
    server.shutdown()
    return RESULT


if __name__ == "__main__":
    print(json.dumps(check(), ensure_ascii=False, indent=1))
