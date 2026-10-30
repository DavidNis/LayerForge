LayerForge (portable Windows build)
===================================

Start:  double-click LayerForge.exe in this folder.
        A small "LayerForge" window opens and the app opens in your web browser.
        If the browser does not open, click "Open LayerForge" in the window.

Stop:   close the LayerForge window (or click Quit). Closing only the browser tab
        leaves LayerForge running.

Your files:
  Projects, previews and print kits:  Documents\LayerForge
  Log (for problem reports):          %LOCALAPPDATA%\LayerForge\logs\layerforge.log
Nothing is saved inside this application folder, so it can be replaced by a newer
build or deleted without losing projects.

Notes:
  - Keep this folder together: LayerForge.exe needs the _internal folder next to it.
  - Runs locally only (http://127.0.0.1); no Python installation is needed.
  - If port 8765 is in use, the next free port is used; the window shows the address.
  - Starting LayerForge a second time just reopens the browser on the running copy.
  - Physical printability is NOT VERIFIED by the app; only a real print can verify it.
  - Advanced: LayerForge.exe --workspace "D:\My LayerForge projects" uses another
    projects folder (e.g. from a shortcut's Target field).
