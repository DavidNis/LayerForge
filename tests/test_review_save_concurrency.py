"""Review saves while the files are being read (Windows file sharing), ``core/review.py::_save``.

On Windows a file another handle has open can be neither deleted (WinError 32) nor replaced
(WinError 5).  The app reads ``review/target.png`` from request threads while an edit job saves the
review, so the old save (delete ``target.png`` and ``layers/``, then write) could fail and leave the
project without them.  These tests hold a real read handle open at a chosen moment, so the overlap is
deterministic: ``model._pause`` (the wait between replace attempts) is where the test lets the reader go.
Synthetic stack from ``test_widening`` (no user artwork).
"""

from __future__ import annotations

import os
import threading

import numpy as np
import pytest
from PIL import Image

from layerforge_l1.core import artwork as art
from layerforge_l1.core import model, review
from layerforge_l1.core.model import Sheet, SheetStack, save_stack
from test_widening import _stack

WINDOWS = os.name == "nt"


def _hashes(folder):
    import hashlib

    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


@pytest.fixture
def project(tmp_path):
    save_stack(_stack(), tmp_path / "planned")
    review.start_review(tmp_path / "planned", tmp_path / "review")
    return tmp_path / "review"


def _with_target(stack: SheetStack, value: int) -> SheetStack:
    """The same sheets with one target pixel changed (so target.png must be replaced)."""

    target = stack.target_rgb.copy()
    target[0, 0] = value
    return SheetStack(stack.name, stack.grid, [Sheet(s.id, s.color, s.retained.copy(), s.visible.copy(), s.thickness_mm, rim=s.rim)
                                               for s in stack.sheets], target_rgb=target, provenance=stack.provenance,
                      review=stack.review)


@pytest.mark.skipif(not WINDOWS, reason="Windows file sharing")
def test_root_cause_a_read_handle_blocks_delete_and_replace(project) -> None:
    other = project / "other.png"
    Image.fromarray(np.zeros((4, 4, 3), np.uint8)).save(other)
    with open(project / "target.png", "rb"):
        with pytest.raises(PermissionError) as deleted:
            (project / "target.png").unlink()
        with pytest.raises(PermissionError) as replaced:
            os.replace(other, project / "target.png")
    assert deleted.value.winerror == 32 and replaced.value.winerror == 5


def test_an_edit_saves_while_target_png_is_being_read(project, monkeypatch) -> None:
    # The edit does not change the artwork: target.png is left as it is, so the reader never blocks it.
    pauses = []
    monkeypatch.setattr(model, "_pause", pauses.append)
    sheet = review.load_project(project).sheets[-1].id
    before = (project / "target.png").read_bytes()
    with open(project / "target.png", "rb"):
        edited, entry = review.apply_to_project(project, {"op": "thickness", "sheet": sheet, "mm": 2.0})
    assert pauses == [] and (project / "target.png").read_bytes() == before
    assert review.load_project(project).sheets[-1].thickness_mm == 2.0
    assert review.verify_project(project)["replay_identical"]


def test_a_save_that_replaces_target_png_waits_for_the_reader(project, monkeypatch) -> None:
    reader = open(project / "target.png", "rb")
    released = []

    def pause(seconds):  # the save found target.png in use: the reader finishes now
        released.append(seconds)
        reader.close()

    monkeypatch.setattr(model, "_pause", pause)
    changed = _with_target(review.load_project(project), 7)
    try:
        review._save(changed, project)
    finally:
        reader.close()
    assert bool(released) == WINDOWS  # on Windows the overlap really happened and was waited out
    loaded = review.load_project(project)
    assert np.array_equal(loaded.target_rgb, changed.target_rgb)
    assert not list(project.rglob("*.partial*"))


@pytest.mark.parametrize("held", ["target.png", "mask"])
def test_a_failed_save_keeps_the_previous_save(project, monkeypatch, held) -> None:
    """The reader never lets go: the save fails after the bounded wait and every file is as before -- also
    when the file in use comes after files already replaced (they are put back)."""

    if not WINDOWS:
        pytest.skip("a held file blocks replacing only on Windows")
    monkeypatch.setattr(model, "_pause", lambda seconds: None)  # the bounded wait, without sleeping
    stack = review.load_project(project)
    before = _hashes(project)
    changed = _with_target(stack, 9)
    changed.sheets[-1].retained[100, 100] = not changed.sheets[-1].retained[100, 100]  # front sheet mask changes
    path = project / ("target.png" if held == "target.png" else f"layers/{stack.sheets[-1].id}/{model.RETAINED_MASK_NAME}")
    with open(path, "rb"):
        with pytest.raises(PermissionError):
            review._save(changed, project)
    assert _hashes(project) == before and not list(project.rglob("*.partial*"))
    again = review.load_project(project)
    assert np.array_equal(again.target_rgb, stack.target_rgb)
    assert all(np.array_equal(a.retained, b.retained) for a, b in zip(again.sheets, stack.sheets))
    assert review.verify_project(project)["replay_identical"]
    review._save(changed, project)  # works once the reader has gone
    assert np.array_equal(review.load_project(project).target_rgb, changed.target_rgb)


def test_concurrent_loads_and_saves_never_see_a_partial_project(project) -> None:
    """Saves alternate between two artworks while other threads load the project and read target.png
    directly (``artwork.load_rgb``, as the app's requests do): every read is one complete version."""

    first = review.load_project(project)
    versions = [_with_target(first, 1), _with_target(first, 2)]
    targets = {v.target_rgb[0, 0, 0] for v in versions}
    errors: list[BaseException] = []
    seen: list[int] = []
    done = threading.Event()

    def save() -> None:
        try:
            for i in range(16):
                review._save(versions[i % 2], project)
        except BaseException as error:  # noqa: BLE001 - reported by the test
            errors.append(error)
        finally:
            done.set()

    def read(direct: bool) -> None:
        try:
            while not done.is_set():
                if direct:
                    pixel = art.load_rgb(project / "target.png")[0, 0, 0]  # as the app's requests read it
                else:
                    pixel = review.load_project(project).target_rgb[0, 0, 0]
                seen.append(int(pixel))
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=save), threading.Thread(target=read, args=(True,)),
               threading.Thread(target=read, args=(False,))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert errors == [] and seen and set(seen) <= targets | {int(first.target_rgb[0, 0, 0])}


def test_switching_candidates_waits_for_a_reader_of_the_review(tmp_path, monkeypatch) -> None:
    """Moving review/ aside (candidate switching) while a request reads review/target.png: on Windows the
    folder cannot be moved until the file is closed; the move waits, nothing is left half-moved."""

    import json

    from layerforge_l1.app.workspace import Workspace

    root, name = tmp_path / "ws", "two_candidates"
    for candidate in ("beam_area", "beam_colors"):
        save_stack(_stack(), root / name / "proposal" / "candidates" / candidate / "stack")
    (root / name / "proposal" / "proposal.json").write_text(json.dumps(
        {"candidates": [{"name": "beam_area", "status": "VALID"}, {"name": "beam_colors", "status": "VALID"}],
         "selected": "beam_area"}), encoding="utf-8")
    (root / name / "project.json").write_text(json.dumps(
        {"name": name, "candidate": None, "kits": [], "proposal_settings": {
            "scale": 2, "outer_mm": 200.0, "thickness_mm": 1.6, "max_colors": 12, "widen_openings": False}}),
        encoding="utf-8")
    ws = Workspace(root)
    ws.start_review(name, "beam_area", lambda *a, **k: None)
    project = root / name
    before = _hashes(project / "review")
    reader = open(project / "review" / "target.png", "rb")
    released = []

    def pause(seconds):
        released.append(seconds)
        reader.close()

    monkeypatch.setattr(model, "_pause", pause)
    try:
        ws.start_review(name, "beam_colors", lambda *a, **k: None)
    finally:
        reader.close()
    assert bool(released) == WINDOWS
    assert ws.record(name)["candidate"] == "beam_colors"
    assert _hashes(project / "reviews" / "beam_area" / "review") == before
