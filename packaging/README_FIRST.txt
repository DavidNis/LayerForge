LayerForge (portable Windows build)
===================================

Start:  double-click LayerForge.exe in this folder. LayerForge starts in the
        background (no window) and opens in your web browser. If the browser does
        not open, double-click LayerForge.exe again.

Stop:   close the LayerForge browser tab. LayerForge stops by itself about 10 seconds
        after its last tab is closed, once nothing is running (planning or a print kit
        being built finishes first; a reload or a second tab keeps it running). If the
        browser crashes, it stops after 30 minutes (a sleeping tab keeps it running).  "Exit" at the top right of
        the page stops it at once.  Your projects are always saved.

Your files:
  Projects, previews and print kits:  Documents\LayerForge
  Log (for problem reports):          %LOCALAPPDATA%\LayerForge\logs\layerforge.log
Nothing is saved inside this application folder, so it can be replaced by a newer
build or deleted without losing projects.

Accepted plan (Plan tab -> Accepted plan; also "Accept this plan" in Review layers):
  - "Accept this plan" keeps one copy of the current plan: artwork, candidates,
    reviews with their edits, issue decisions, sheet size and scale.
  - "Restore accepted plan" brings that copy back exactly, without planning again.
  - Only one accepted plan is kept; accepting again replaces it. Experiments after
    accepting (artwork edits, re-planning, resize, upscale) never change it.
  - A new plan replaces the current one; it is not added to Previous attempts.
    Attempts saved by earlier versions are left as they are.
  - Accepting a plan does not mean it prints; only a real print can verify that.

Notes:
  - Keep this folder together: LayerForge.exe needs the _internal folder next to it.
  - Runs locally only (http://127.0.0.1); no Python installation is needed.
  - If port 8765 is in use, the next free port is used (the browser opens on it).
  - Starting LayerForge a second time just reopens the browser on the running copy.
  - Physical printability is NOT VERIFIED by the app; only a real print can verify it.
  - Advanced: LayerForge.exe --workspace "D:\My LayerForge projects" uses another
    projects folder (e.g. from a shortcut's Target field).
