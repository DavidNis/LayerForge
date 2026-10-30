"""Entry point of the packaged Windows build (LayerForge.exe); see layerforge_l1.app.launcher."""

import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from layerforge_l1.app.launcher import main

    raise SystemExit(main())
