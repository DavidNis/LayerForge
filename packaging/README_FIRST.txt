LayerForge (portable Windows build)
===================================

Start:  double-click LayerForge.exe in this folder.
        A small "LayerForge" window opens and the app opens in your web browser.
        If the browser does not open, click "Open LayerForge" in the window.

Stop:   click "Stop LayerForge" in the window (or close the window). Closing the
        browser tab leaves LayerForge running. A planning step that is still running
        is stopped first; your projects are always kept.

Stop planning: while sheets are being planned or a review is being opened ("Review
        this candidate"), "Stop" in the progress bar of the page stops that step; the
        project stays as it was before.

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
  - If port 8765 is in use, the next free port is used; the window shows the address.
  - Starting LayerForge a second time just reopens the browser on the running copy.
  - Physical printability is NOT VERIFIED by the app; only a real print can verify it.
  - Advanced: LayerForge.exe --workspace "D:\My LayerForge projects" uses another
    projects folder (e.g. from a shortcut's Target field).
