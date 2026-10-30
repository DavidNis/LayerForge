"""Application projects on disk; every operation delegates to the approved core.

One project directory per artwork::

    project.json            app record: settings, chosen candidate, kit list
    source/<image>          the uploaded image, unchanged
    proposal/               L1 ``propose`` output (candidates, previews, proposal.json)
    review/                 L2 review project (``start_review``; saved after every edit)
    draft/                  cached views of the current review draft: state.json
                            (validation, inspection, last change), previews/
    printability/           L3 ``assess_printability`` estimate of the draft + maps
    kits/<kit id>/          L3 ``build_print_kit`` output
    artwork/                region recolors of the flat-color artwork (``core/artwork.py`` log),
                            not yet planned; moved to ``proposal/artwork_edits/`` by a re-plan

Planning, editing, validation, CAD and printability are not implemented here:
this module only calls ``propose``, ``review.*``, ``validate_stack``,
``assess_printability`` and ``build_print_kit`` and records their reports.  The
review project itself is the save format, so a project reloads after a restart.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import stat
import threading
import zipfile
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .. import PROJECT_ROOT
from ..core import review
from ..core.editing import EDIT_OPS, write_change_image
from ..core.model import MANIFEST_NAME, StackError, file_sha256
from ..core.validation import validate_stack

APP_SCHEMA = "layerforge.app_project"
APP_VERSION = "1.0.0"
PROCESS_PRESETS_DIR = PROJECT_ROOT / "fixtures" / "print_processes"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".jfif", ".jpe")  # .jfif/.jpe are JPEG files
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SCALES = (1, 2, 4)
KIT_ID = re.compile(r"kit_\d{8}_\d{6}x*")  # ids made by ``build_kit``; nothing else is ever deleted
# Why actions that rebuild the artwork from the source cannot keep region recolors (yet).
ARTWORK_LIMITS = {
    "Upscale & Retry": "it re-samples the source into a finer pixel grid, and recolors are recorded as exact pixel "
                       "regions of the current grid (mapping them onto new pixels is not implemented)",
    "re-running intake": "it rebuilds the flat-color artwork from the source with new intake settings or detail "
                         "choices, so the recolored regions may no longer exist as recorded",
}
PHYSICAL_STATUS = {
    "status": "NOT_VERIFIED",
    "note": "Only a real print reviewed by you (L-G10) can verify a result; no file or estimate does.",
}


class AppError(StackError):
    """A request the application cannot carry out as given (message is shown to the user)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Any:
    if not Path(path).is_file():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Core reports are written in place by running jobs; a read can catch one half-written.
        raise AppError(f"{Path(path).name} is being written or is incomplete; reload in a moment") from None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, indent=2, default=_json_default) + "\n", encoding="utf-8")
    partial.replace(path)


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"not JSON serializable: {type(value)}")


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:48] or "project"


def _is_link(path: Path) -> bool:
    """A symlink or (Windows) directory junction: never followed when deleting."""

    return os.path.islink(path) or bool(getattr(os.path, "isjunction", lambda _: False)(path))


def _failure(path: Path, root_name: str, error: OSError) -> dict[str, str]:
    return {"path": path.as_posix().split(f"/{root_name}/", 1)[-1] if f"/{root_name}/" in path.as_posix()
            else path.name, "error": error.strerror or str(error)}


def _remove_link(path: Path, root_name: str) -> list[dict[str, str]]:
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            os.rmdir(path)  # a junction: removes the link, not the target's contents
        else:
            try:
                os.unlink(path)
            except IsADirectoryError:
                os.rmdir(path)
            except PermissionError:
                os.rmdir(path)  # directory symlink on Windows
        return []
    except OSError as error:
        return [_failure(path, root_name, error)]


def _remove_file(path: Path, root_name: str) -> list[dict[str, str]]:
    try:
        os.unlink(path)
        return []
    except PermissionError:
        try:  # read-only attribute (Windows): clear it once and retry
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            os.unlink(path)
            return []
        except OSError as error:
            return [_failure(path, root_name, error)]
    except FileNotFoundError:
        return []
    except OSError as error:
        return [_failure(path, root_name, error)]


def _remove_tree(root: Path, keep_last: str) -> tuple[int, list[dict[str, str]]]:
    """Delete ``root`` bottom-up without following links; ``keep_last`` (top level) goes last."""

    removed, failed = 0, []

    def walk(folder: Path) -> None:
        nonlocal removed
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name == keep_last and folder == root)
        except OSError as error:
            failed.append(_failure(folder, root.name, error))
            return
        for entry in entries:
            path = Path(entry.path)
            if folder == root and entry.name == keep_last and failed:
                continue  # keep the kit listed (and retryable) while anything else is left
            if entry.is_dir(follow_symlinks=False) and not _is_link(path):
                remove_dir(path)
                continue
            problems = _remove_link(path, root.name) if _is_link(path) else _remove_file(path, root.name)
            failed.extend(problems)
            removed += 0 if problems else 1

    def remove_dir(folder: Path) -> None:
        before = len(failed)
        walk(folder)
        try:
            os.rmdir(folder)
        except OSError as error:
            if len(failed) == before:  # otherwise not empty because of a failure already reported
                failed.append(_failure(folder, root.name, error))

    remove_dir(root)
    return removed, failed


def check_detail_decisions(decisions: Any) -> dict[str, dict[str, str]]:
    """``{detail id: {"action": "preserve" | "remove" | "adjust", "color": "#RRGGBB" (adjust)}}``."""

    if not isinstance(decisions, dict):
        raise AppError("detail_decisions must be an object of detail id -> decision")
    out = {}
    for key, value in decisions.items():
        if not (isinstance(key, str) and re.fullmatch(r"(color|removed)_\d{4}_\d{4}", key)):
            raise AppError(f"unknown intake detail id {key!r}")
        if not isinstance(value, dict) or value.get("action") not in ("preserve", "remove", "adjust"):
            raise AppError(f"{key}: action must be preserve, remove or adjust")
        entry = {"action": value["action"]}
        if value["action"] == "adjust":
            color = str(value.get("color", ""))
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                raise AppError(f"{key}: adjust needs a color #RRGGBB")
            entry["color"] = color.upper()
        out[key] = entry
    return out


def process_from(values: dict[str, Any] | None):
    """A ``PrintProcess`` from form values (validated by ``PrintProcess`` itself)."""

    from ..core.printkit import PrintProcess

    if not values:
        return PrintProcess()
    known = {item.name: item for item in fields(PrintProcess)}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise AppError(f"unknown printer/process fields {unknown}")
    data: dict[str, Any] = {}
    for key, value in values.items():
        if known[key].type in ("float", float):
            try:
                data[key] = float(value)
            except (TypeError, ValueError):
                raise AppError(f"{key} must be a number, got {value!r}") from None
        else:
            data[key] = str(value)
    return PrintProcess(**data)


def passing_validation(stack: Any) -> dict[str, Any]:
    """Summary of a draft ``apply_to_project`` validated as PASS (per-check details not recomputed)."""

    colors = [s.color for s in stack.sheets]
    current = stack.review["current"]
    return {
        "status": "PASS", "failures": [], "details_recomputed": False,
        "reconstruction": {"evaluated": stack.target_rgb is not None,
                           "mismatched_pixels": current["reconstruction_vs_reviewed_target_px"]},
        "mm_per_pixel": stack.grid.mm_per_pixel, "outer_sheet_mm": [stack.grid.outer_sheet_mm] * 2,
        "repeated_colors": sorted({c for c in colors if colors.count(c) > 1}),
        "sheets": [{"id": s.id, "pass": True} for s in stack.sheets],
    }


def process_presets() -> list[dict[str, Any]]:
    from ..core.printkit import PrintProcess

    presets = [{"file": None, **asdict(PrintProcess())}]
    for path in sorted(PROCESS_PRESETS_DIR.glob("*.json")):
        try:
            presets.append({"file": path.name, **asdict(PrintProcess.from_json(path))})
        except StackError:
            continue
    return presets


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._artwork_lock = threading.Lock()  # artwork edits are quick requests, not queued jobs
        self._basis_sha: dict[tuple[str, int, int], str] = {}  # artwork file stamp -> pixel hash

    # -- paths and records -----------------------------------------------------------
    def path(self, name: str) -> Path:
        if slug(name) != name:
            raise AppError(f"invalid project name {name!r}")
        path = self.root / name
        if not (path / "project.json").is_file():
            raise AppError(f"no project {name!r} in {self.root}")
        return path

    def review_dir(self, name: str) -> Path:
        project = self.path(name) / "review"
        if not (project / MANIFEST_NAME).is_file():
            raise AppError("no review yet; pick a candidate from the proposal first")
        return project

    def record(self, name: str) -> dict[str, Any]:
        return _read_json(self.path(name) / "project.json")

    def _save_record(self, name: str, record: dict[str, Any]) -> None:
        _write_json(self.root / name / "project.json", record)

    def projects(self) -> list[dict[str, Any]]:
        rows = []
        for path in sorted(self.root.iterdir()):
            record = _read_json(path / "project.json") if path.is_dir() else None
            if record and record.get("schema") == APP_SCHEMA:
                rows.append({"name": record["name"], "created_utc": record["created_utc"],
                             "source": record["source"]["file"], "stage": self.stage(path.name)})
        return rows

    def delete_project(self, name: str) -> dict[str, Any]:
        """Delete one project folder and everything in it (the project is its folder: nothing else
        records it).

        Only ``<workspace>/<name>`` is touched: the name must be a plain project name (no path
        parts) whose folder holds an app ``project.json`` and lies directly in the workspace.  Links
        (symlinks, junctions) inside are removed as links and never followed; a project folder that
        is itself a link is refused.  The imported image was copied into ``source/`` at import, so
        the original file elsewhere on disk is never touched.  ``project.json`` goes last: if a file
        cannot be removed (open in another program, no permission), the project stays listed with
        what is left, and deleting again retries."""

        if not isinstance(name, str) or slug(name) != name:
            raise AppError(f"invalid project name {name!r}")
        folder = self.root / name
        if _is_link(folder):
            raise AppError(f"{name!r} is a link to another location, not a project folder; it was not deleted")
        record = _read_json(folder / "project.json") if folder.is_dir() else None
        if not record or record.get("schema") != APP_SCHEMA:
            raise AppError(f"no project {name!r} in {self.root}")
        if folder.resolve().parent != self.root:
            raise AppError(f"{name!r} is not directly inside the workspace; it was not deleted")
        removed, failed = _remove_tree(folder, keep_last="project.json")
        gone = not os.path.lexists(folder)
        return {"project": name, "status": "DELETED" if gone else "PARTIALLY_DELETED", "removed_files": removed,
                "failed": failed[:50], "failed_count": len(failed), "folder": folder.as_posix(), "deleted_utc": _now(),
                "notes": ["The original image you imported from elsewhere on disk was not touched."]}

    def stage(self, name: str) -> str:
        path = self.root / name
        if (path / "kits").is_dir() and any((path / "kits").iterdir()):
            return "kit"
        if (path / "review" / MANIFEST_NAME).is_file():
            return "review"
        if (path / "proposal" / "proposal.json").is_file():
            return "proposal"
        return "new"

    # -- 1. import + L1 proposal ---------------------------------------------------------
    def create(self, filename: str, data: bytes, settings: dict[str, Any]) -> str:
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            raise AppError(f"{filename}: upload a flat-color PNG or JPEG ({', '.join(IMAGE_SUFFIXES)})")
        if not data:
            raise AppError("the uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise AppError(f"the image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        try:
            with Image.open(io.BytesIO(data)) as image:
                kind = image.format
                image.verify()
        except Exception as error:  # noqa: BLE001 - Pillow raises many types for bad files
            raise AppError(f"{filename} is not a readable image: {error}") from None
        if kind not in ("PNG", "JPEG"):
            raise AppError(f"{filename} is a {kind} image; upload a flat-color PNG or JPEG")
        settings = self._proposal_settings(settings)
        base = slug(settings.pop("name", None) or Path(filename).stem)
        name, n = base, 1
        while (self.root / name).exists():
            n += 1
            name = f"{base}_{n}"
        path = self.root / name
        (path / "source").mkdir(parents=True)
        source = path / "source" / (slug(Path(filename).stem) + suffix)
        source.write_bytes(data)
        self._save_record(name, {
            "schema": APP_SCHEMA, "version": APP_VERSION, "name": name, "created_utc": _now(),
            "source": {"file": source.relative_to(path).as_posix(), "original_name": filename,
                       "sha256": file_sha256(source)},
            "proposal_settings": settings, "candidate": None, "kits": [],
        })
        return name

    @staticmethod
    def _proposal_settings(settings: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"name": settings.get("name") or None}
        try:
            out["scale"] = int(settings.get("scale", 2))
            out["outer_mm"] = float(settings.get("outer_mm", 225.0))
            out["thickness_mm"] = float(settings.get("thickness_mm", 1.6))
            out["max_colors"] = int(settings.get("max_colors", 12))
        except (TypeError, ValueError) as error:
            raise AppError(f"invalid proposal setting: {error}") from None
        if out["scale"] not in SCALES:
            raise AppError(f"scale must be one of {SCALES}")
        if not (out["outer_mm"] > 0 and out["thickness_mm"] > 0 and 2 <= out["max_colors"] <= 32):
            raise AppError("outer size and thickness must be positive; max colors 2..32")
        return out

    def propose(self, name: str, progress: Callable[..., None],
                decisions: dict[str, Any] | None = None, plan: bool = True) -> dict[str, Any]:
        """Run the L1 proposal; ``decisions`` (intake detail id -> action) replace the saved ones.

        ``plan=False`` stops after intake so the artwork colors can be edited before planning."""

        from ..core.intake import IntakeConfig
        from ..core.planning import PlanningConfig
        from ..core.propose import ProposalConfig, propose

        path, record = self.path(name), self.record(name)
        if (path / "review").exists():
            raise AppError("this project already has a review; start a new project to re-propose")
        self.refuse_if_artwork_edited(name, "re-running intake")
        if decisions is not None:
            record["detail_decisions"] = check_detail_decisions(decisions)
            self._save_record(name, record)
        shutil.rmtree(path / "proposal", ignore_errors=True)
        shutil.rmtree(path / "upscale", ignore_errors=True)  # previews of the replaced proposal
        s = record["proposal_settings"]
        planning = PlanningConfig(outer_sheet_mm=self.sheet_outer_mm(record), scale=self.processing_scale(record),
                                  thickness_mm=s["thickness_mm"])
        shutil.rmtree(path / "artwork", ignore_errors=True)  # an empty log on the replaced artwork
        result = propose(path / record["source"]["file"], path / "proposal", IntakeConfig(max_colors=s["max_colors"]),
                         ProposalConfig(planning=planning, plan=plan), progress, record.get("detail_decisions") or None)
        return {"status": result["status"], "selected": result["selected"]}

    # -- 1b. Upscale & Retry of a failed proposal ----------------------------------------------
    @staticmethod
    def processing_scale(record: dict[str, Any]) -> int:
        """Planner scale in use: the import setting, or the one chosen by Upscale & Retry."""

        return int(record.get("processing_scale") or record["proposal_settings"]["scale"])

    @staticmethod
    def sheet_outer_mm(record: dict[str, Any]) -> float:
        """Sheet size in use: the import setting, or the one approved in a physical resize."""

        return float(record.get("sheet_outer_mm") or record["proposal_settings"]["outer_mm"])

    def failed_proposal(self, name: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        from ..core.propose import NEEDS_DECISION

        path, record = self.path(name), self.record(name)
        proposal = _read_json(path / "proposal" / "proposal.json")
        if proposal is None:
            raise AppError("no proposal yet; run the proposal first")
        if proposal["status"] != NEEDS_DECISION:
            raise AppError("Upscale & Retry is only offered when the automatic proposal failed")
        if (path / "review").exists():
            raise AppError("this project already has a review; start a new project to re-propose")
        self.refuse_if_artwork_edited(name, "Upscale & Retry")
        return path, record, proposal

    def _preview_id(self, record: dict[str, Any], factor: int) -> str:
        from ..core.upscale import preview_id

        s = record["proposal_settings"]
        return preview_id(record["source"]["sha256"], self.sheet_outer_mm(record), self.processing_scale(record), factor,
                          s["max_colors"], record.get("detail_decisions"))

    @staticmethod
    def check_factor(factor: Any) -> int:
        from ..core.upscale import UPSCALE_FACTORS

        if isinstance(factor, bool) or factor not in UPSCALE_FACTORS:
            raise AppError(f"factor must be one of {list(UPSCALE_FACTORS)}")
        return int(factor)

    def upscale_preview(self, name: str, factor: Any, progress: Callable[..., None]) -> dict[str, Any]:
        """Intake of the failed proposal's image at ``factor`` x the processing resolution (no planning)."""

        from ..core.intake import IntakeConfig
        from ..core.upscale import preview_upscale

        path, record, _ = self.failed_proposal(name)
        factor = self.check_factor(factor)
        out = path / "upscale" / f"x{factor}"
        shutil.rmtree(out, ignore_errors=True)
        result = preview_upscale(path / record["source"]["file"], path / "proposal", out, factor,
                                 IntakeConfig(max_colors=record["proposal_settings"]["max_colors"]),
                                 record.get("detail_decisions"), progress)
        _write_json(out / "preview.json", {**result, "preview_id": self._preview_id(record, factor)})
        return {"status": result["status"], "changes": len(result.get("changes", []))}

    def check_upscale_retry(self, name: str, factor: Any, preview: Any, approve_changes: Any) -> dict[str, Any]:
        """The retry request is current, within bounds and approved; returns the saved preview."""

        from ..core.upscale import READY, resource_check

        path, record, _ = self.failed_proposal(name)
        factor = self.check_factor(factor)
        saved = _read_json(path / "upscale" / f"x{factor}" / "preview.json")
        if saved is None:
            raise AppError(f"preview the {factor}x processing resolution first")
        if saved.get("preview_id") != self._preview_id(record, factor) or preview != saved["preview_id"]:
            raise AppError("the preview is out of date (image, settings or choices changed); preview again")
        if saved["status"] != READY:
            raise AppError(f"the {factor}x preview did not pass ({saved['status']}); nothing was retried")
        if saved.get("changes") and approve_changes is not True:
            raise AppError(f"approve the {len(saved['changes'])} visible artwork changes shown in the preview first")
        fresh = resource_check(saved["outer_sheet_mm"], saved["to_scale"])
        if not fresh["fits"]:
            raise AppError(f"not enough memory for a {factor}x retry now: {fresh['reason']}")
        return saved

    def upscale_retry(self, name: str, factor: Any, preview: Any, approve_changes: Any,
                      progress: Callable[..., None]) -> dict[str, Any]:
        """Re-run the proposal at the previewed resolution in this project.

        The new run is written next to the current proposal and swapped in only when it finishes;
        the failed proposal (with its diagnostics) and the approved preview are archived under
        ``attempts/``.  On any error the current proposal stays untouched.
        """

        from ..core.intake import IntakeConfig
        from ..core.planning import PlanningConfig
        from ..core.propose import ProposalConfig, propose

        saved = self.check_upscale_retry(name, factor, preview, approve_changes)
        path, record = self.path(name), self.record(name)
        shutil.rmtree(path / "artwork", ignore_errors=True)  # checked empty; its base is replaced
        s = record["proposal_settings"]
        staging = path / "proposal.next"
        shutil.rmtree(staging, ignore_errors=True)
        planning = PlanningConfig(outer_sheet_mm=self.sheet_outer_mm(record), scale=saved["to_scale"],
                                  thickness_mm=s["thickness_mm"])
        progress("UPSCALE_RETRY_STARTING", scale=saved["to_scale"], factor=saved["factor"])
        try:
            result = propose(path / record["source"]["file"], staging, IntakeConfig(max_colors=s["max_colors"]),
                             ProposalConfig(planning=planning), progress, saved["decisions"] or None)
        except MemoryError:
            shutil.rmtree(staging, ignore_errors=True)
            raise AppError(f"ran out of memory at processing scale {saved['to_scale']}; the previous proposal is "
                           "unchanged. Close other programs or choose a smaller factor.") from None
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        previous = _read_json(path / "proposal" / "proposal.json")
        attempts = record.setdefault("attempts", [])
        folder = f"attempt_{len(attempts) + 1:02d}_scale{saved['from_scale']}"
        archive = path / "attempts" / folder
        archive.mkdir(parents=True, exist_ok=False)
        (path / "proposal").replace(archive / "proposal")
        (path / "upscale" / f"x{saved['factor']}").replace(archive / "upscale_preview")
        staging.replace(path / "proposal")
        shutil.rmtree(path / "upscale", ignore_errors=True)
        attempts.append({
            "id": folder, "archived_utc": _now(), "scale": saved["from_scale"], "status": previous["status"],
            "blockers": [f["code"] for f in previous["flags"] if f["severity"] == "BLOCKER"],
            "detail_decisions": record.get("detail_decisions") or {},
            "replaced_by": {"upscale_factor": saved["factor"], "scale": saved["to_scale"], "status": result["status"],
                            "approved_visible_changes": len(saved["changes"]), "preview_id": saved["preview_id"]},
        })
        record["processing_scale"] = saved["to_scale"]
        record["detail_decisions"] = saved["decisions"]
        self._save_record(name, record)
        return {"status": result["status"], "selected": result["selected"], "scale": saved["to_scale"]}

    # -- 1c. physical resize -----------------------------------------------------------------
    def resize_subject(self, name: str) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
        """What a size analysis is about: the review draft, else the selected or failed proposal."""

        from ..core.propose import NEEDS_DECISION, READY

        path, record = self.path(name), self.record(name)
        proposal = _read_json(path / "proposal" / "proposal.json")
        if proposal is None:
            raise AppError("no proposal yet; run the proposal first")
        if (path / "review" / MANIFEST_NAME).is_file():
            basis = {"stage": "review", "sha256": file_sha256(path / "review" / MANIFEST_NAME)}
        elif proposal["status"] == READY and proposal.get("selected"):
            basis = {"stage": "proposal", "candidate": proposal["selected"],
                     "sha256": file_sha256(path / "proposal" / "proposal.json")}
        elif proposal["status"] == NEEDS_DECISION:
            basis = {"stage": "failed_proposal", "sha256": file_sha256(path / "proposal" / "proposal.json")}
        else:
            raise AppError("the proposal has no selected candidate to assess")
        basis.update(sheet_mm=self.sheet_outer_mm(record), scale=self.processing_scale(record))
        edited = self.edited_artwork(name, load=False)
        if edited is not None:
            basis["artwork"] = {"sha256": edited["sha256"], "region_recolors": edited["region_recolors"],
                                "pending": edited["pending"]}
        return path, record, proposal, basis

    def resize_analysis(self, name: str, process_values: dict[str, Any] | None, bambu_printer: str | None,
                        progress: Callable[..., None]) -> dict[str, Any]:
        """Measure the printability issues now and tabulate them against larger sheet sizes."""

        import numpy as np

        from ..core import resize
        from ..core.model import load_stack
        from ..core.printkit import assess_printability

        path, record, proposal, basis = self.resize_subject(name)
        process = process_from(process_values)
        limit = resize.usable_sheet_mm(process, bambu_printer or None)
        mm_per_pixel = proposal["intake"]["artwork_mm_per_pixel"]
        if basis["stage"] == "failed_proposal":
            target = np.asarray(Image.open(path / "proposal" / "intake" / "target.png").convert("RGB"))
            obstruction = np.zeros(target.shape[:2], bool)
            for row in proposal["candidates"]:
                map_path = path / "proposal" / "candidates" / row["name"] / "obstruction.png"
                if map_path.is_file():
                    obstruction |= np.asarray(Image.open(map_path)) > 0
            issues = resize.obstruction_issues(obstruction, target, mm_per_pixel, proposal["intake"]["structural_width_mm"])
            measured: dict[str, Any] = {"source": "planner obstruction map (no stack was planned)" if obstruction.any()
                                        else "no stack was planned and no obstruction map exists (the search failed)",
                                        "counts": {"UNSUPPORTED": len(issues), "RISK": 0}, "sheet_count": None}
        else:
            stack = (review.load_project(path / "review") if basis["stage"] == "review" else
                     load_stack(path / "proposal" / "candidates" / basis["candidate"] / "stack"))
            progress("ASSESSING_PRINTABILITY", sheets=len(stack.sheets), process=process.name)
            report = assess_printability(stack, process)
            issues = resize.issue_requirements(report["features"], process, stack.grid.mm_per_pixel)
            measured = {"source": f"FDM estimate of the {basis['stage']} ({process.name})", "counts": report["counts"],
                        "sheet_count": len(stack.sheets)}
        if basis.get("artwork", {}).get("pending"):
            measured["source"] += ("; measured on the sheets planned from the previous artwork (the artwork has "
                                   f"{basis['artwork']['region_recolors']} unplanned region recolor(s), planned by the retry)")
        options = resize.size_options(issues, basis["sheet_mm"], limit["max_sheet_mm"])
        analysis = {"schema": resize.ANALYSIS_SCHEMA, "basis": basis, "process": asdict(process),
                    "bambu_printer": bambu_printer or None, "limit": limit, "measured": measured,
                    "issues": issues, "options": options, "checked_utc": _now()}
        if basis["stage"] == "failed_proposal" and options["recommended"] is None and limit["max_sheet_mm"] > basis["sheet_mm"]:
            reasons = sorted({str(c.get("reason", ""))[:80] for c in proposal["candidates"] if c["status"] == "PLANNING_FAILED"})
            analysis["trial_search"] = {
                "available": True, "planning_failures": reasons,
                "why": "the failure cannot be predicted from feature widths (e.g. the search reached its sheet limit); "
                       "trial planning at a few sizes finds the smallest size that plans",
                "estimated_minutes_per_trial": round(float(proposal.get("seconds") or 0) / max(1, len(proposal["candidates"])) / 60, 1)}
        analysis["analysis_id"] = resize.analysis_id([basis, analysis["process"], bambu_printer, limit])
        out = path / "resize"
        shutil.rmtree(out, ignore_errors=True)
        _write_json(out / "analysis.json", analysis)
        progress("RESIZE_ANALYSIS_DONE", recommended=(options["recommended"] or {}).get("sheet_mm"))
        return {"recommended": options["recommended"], "max_sheet_mm": limit["max_sheet_mm"]}

    def resize_search(self, name: str, progress: Callable[..., None]) -> dict[str, Any]:
        """Trial planning (no CAD) to find the smallest sheet that plans, for a failed proposal."""

        from ..core import resize
        from ..core.intake import IntakeConfig

        path, record, analysis = self.current_analysis(name)
        if not analysis.get("trial_search", {}).get("available"):
            raise AppError("trial planning is only offered for a failed proposal whose failure widths cannot predict")
        previous = _read_json(path / "proposal" / "proposal.json")
        intake = IntakeConfig(max_colors=record["proposal_settings"]["max_colors"])
        source = path / record["source"]["file"]
        decisions = record.get("detail_decisions") or None
        edited = self.edited_artwork(name)

        def plan(size: float) -> dict[str, Any]:
            return resize.trial_plan(source, previous, size, intake, decisions, edited)

        found = resize.search_plannable_size(plan, analysis["basis"]["sheet_mm"], analysis["limit"]["max_sheet_mm"],
                                             progress=progress)
        analysis["trial_search"].update(found, searched_utc=_now())
        if found["smallest_plannable_mm"] is not None:
            size = found["smallest_plannable_mm"]
            row = next(r for r in found["trials"] if r["sheet_mm"] == size)
            analysis["options"]["recommended"] = {"sheet_mm": size, "factor": round(size / analysis["basis"]["sheet_mm"], 4),
                                                  "UNSUPPORTED": 0, "RISK": 0, "CLEARED": 0, "source": "trial planning",
                                                  "sheet_count": row.get("sheet_count")}
        _write_json(path / "resize" / "analysis.json", analysis)
        return {"smallest_plannable_mm": found["smallest_plannable_mm"], "trials": len(found["trials"])}

    def current_analysis(self, name: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        path, record, _, basis = self.resize_subject(name)
        analysis = _read_json(path / "resize" / "analysis.json")
        if analysis is None:
            raise AppError("check printability at larger sizes first")
        if analysis["basis"] != basis:
            raise AppError("the size analysis is out of date (the plan or settings changed); check again")
        return path, record, analysis

    def resize_preview(self, name: str, size_mm: Any, progress: Callable[..., None]) -> dict[str, Any]:
        """Intake at a proposed sheet size (same pixels) with checks and the predicted findings."""

        from ..core.intake import IntakeConfig
        from ..core.resize import preview_resize

        path, record, analysis = self.current_analysis(name)
        size = self.check_size(size_mm, analysis)
        out = path / "resize" / "preview"
        shutil.rmtree(out, ignore_errors=True)
        result = preview_resize(path / record["source"]["file"], path / "proposal", out, size, analysis,
                                IntakeConfig(max_colors=record["proposal_settings"]["max_colors"]),
                                record.get("detail_decisions"), progress, self.edited_artwork(name))
        result["preview_id"] = self._resize_preview_id(record, analysis, size)
        _write_json(out / "preview.json", result)
        return {"status": result["status"], "changes": len(result["changes"])}

    @staticmethod
    def check_size(size_mm: Any, analysis: dict[str, Any]) -> float:
        try:
            size = float(size_mm)
        except (TypeError, ValueError):
            raise AppError("size must be a number of millimetres") from None
        current, limit = analysis["basis"]["sheet_mm"], analysis["limit"]["max_sheet_mm"]
        if not (current < size <= limit):
            raise AppError(f"the new sheet size must be larger than {current:g} mm and at most {limit:g} mm "
                           f"(the printer's usable area)")
        return round(size, 1)

    def _resize_preview_id(self, record: dict[str, Any], analysis: dict[str, Any], size: float) -> str:
        from ..core.resize import analysis_id

        return analysis_id([analysis["analysis_id"], size, record.get("detail_decisions") or {},
                            record["proposal_settings"]["max_colors"]])

    def check_resize_retry(self, name: str, preview: Any, approve_size: Any, approve_changes: Any) -> dict[str, Any]:
        from ..core.resize import READY

        path, record, analysis = self.current_analysis(name)  # out of date if the artwork changed since
        saved = _read_json(path / "resize" / "preview" / "preview.json")
        if saved is None:
            raise AppError("preview the new size first")
        if preview != saved.get("preview_id") or saved["preview_id"] != self._resize_preview_id(record, analysis, saved["to_mm"]):
            raise AppError("the size preview is out of date; preview again")
        if saved["status"] != READY:
            raise AppError(f"the size preview did not pass ({saved['status']}); nothing was changed")
        if approve_size is not True:
            raise AppError(f"approve changing the sheet from {saved['from_mm']:g} mm to {saved['to_mm']:g} mm first")
        if saved["changes"] and approve_changes is not True:
            raise AppError(f"approve the {len(saved['changes'])} visible artwork changes shown in the preview first")
        return {**saved, "_analysis": analysis}

    def resize_retry(self, name: str, preview: Any, approve_size: Any, approve_changes: Any,
                     progress: Callable[..., None]) -> dict[str, Any]:
        """Re-plan in this project at the approved size, then re-measure printability.

        The new proposal is built beside the current state and swapped in when it finishes; the
        previous proposal, review draft, printability estimate and the size analysis/preview are
        archived under ``attempts/`` (kits stay listed).  The image is never changed.  Planning uses
        the same planner, including its local cell refinement when a closure is infeasible.

        With region recolors (unplanned, or already planned), the edited artwork shown in the preview
        is planned pixel for pixel instead of the source's intake, and its edit log moves with the new
        proposal (``proposal/artwork_edits/``); the archived attempt keeps the previous state.
        """

        from ..core.intake import IntakeConfig
        from ..core.model import load_stack
        from ..core.planning import PlanningConfig
        import numpy as np

        from ..core.printkit import PrintProcess, assess_printability, printability_maps
        from ..core.propose import ProposalConfig, propose
        from ..core.resize import issue_requirements, remaining_at

        saved = self.check_resize_retry(name, preview, approve_size, approve_changes)
        analysis = saved.pop("_analysis")
        path, record = self.path(name), self.record(name)
        edited = self.edited_artwork(name)
        if bool(edited) != bool(saved.get("artwork_edits")):
            raise AppError("the artwork edits changed since the size preview; preview the size again")
        s = record["proposal_settings"]
        staging = path / "proposal.next"
        shutil.rmtree(staging, ignore_errors=True)
        planning = PlanningConfig(outer_sheet_mm=saved["to_mm"], scale=self.processing_scale(record),
                                  thickness_mm=s["thickness_mm"])
        progress("RESIZE_RETRY_STARTING", from_mm=saved["from_mm"], to_mm=saved["to_mm"])
        try:
            result = propose(path / record["source"]["file"], staging, IntakeConfig(max_colors=s["max_colors"]),
                             ProposalConfig(planning=planning), progress, saved["decisions"] or None,
                             artwork=None if edited is None else {
                                 "target_rgb": edited["target_rgb"], "frame_color": edited["frame_color"],
                                 "provenance": {**edited["provenance"], "carried_by": "physical_resize",
                                                "sheet_mm": saved["to_mm"]}})
        except StackError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise AppError(f"could not plan the edited artwork at {saved['to_mm']:g} mm: {error}") from None
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        # Only the artwork the user approved in the preview may be planned.
        approved = np.asarray(Image.open(path / "resize" / "preview" / "target.png").convert("RGB"))
        planned = np.asarray(Image.open(staging / "intake" / "target.png").convert("RGB"))
        if approved.shape != planned.shape or not np.array_equal(approved, planned):
            shutil.rmtree(staging, ignore_errors=True)
            raise AppError("the re-planned artwork differs from the approved preview; nothing was changed. "
                           "Preview the size again.")
        previous = _read_json(path / "proposal" / "proposal.json")
        attempts = record.setdefault("attempts", [])
        folder = f"attempt_{len(attempts) + 1:02d}_{saved['from_mm']:g}mm".replace(".", "_")
        archive = path / "attempts" / folder
        archive.mkdir(parents=True, exist_ok=False)
        for part in ("proposal", "review", "draft", "printability", "resize"):
            if (path / part).exists():
                (path / part).replace(archive / part)
        staging.replace(path / "proposal")
        shutil.rmtree(path / "upscale", ignore_errors=True)
        self._carry_artwork_log(path, archive, edited)

        check: dict[str, Any] = {"from_mm": saved["from_mm"], "to_mm": saved["to_mm"], "process": analysis["process"],
                                 "before": {**analysis["measured"], "stage": analysis["basis"]["stage"]},
                                 "predicted": remaining_at(analysis["issues"], saved["ratio"]),
                                 "predicted_basis": "estimate: measured widths times the size ratio; not a result",
                                 "artwork_matches_approved_preview": True,
                                 "proposal_status": result["status"], "proposal_seconds": result["seconds"]}
        if edited is not None:
            check["artwork_edits"] = {"region_recolors": edited["region_recolors"], "pending_before": edited["pending"],
                                      "kept_pixel_for_pixel": True, "log": "artwork_edits/edits.json"}
        if result["selected"]:
            stack = load_stack(path / "proposal" / "candidates" / result["selected"] / "stack")
            progress("ASSESSING_PRINTABILITY", sheets=len(stack.sheets))
            process = PrintProcess(**analysis["process"])
            report = assess_printability(stack, process)
            maps = printability_maps(stack, report, path / "proposal" / "resize_maps")
            after = issue_requirements(report["features"], process, stack.grid.mm_per_pixel)
            check["after"] = {"source": f"FDM estimate of the re-planned candidate ({process.name})",
                              "counts": report["counts"], "sheet_count": len(stack.sheets),
                              "candidate": result["selected"], "maps": [f"resize_maps/{m}" for m in maps],
                              "unsupported": [{k: i.get(k) for k in ("kind", "sheet", "width_mm", "at_mm", "visible_area_mm2")}
                                              for i in after if i["severity"] == "UNSUPPORTED"]}
            check["more_unsupported_than_predicted"] = max(0, report["counts"]["UNSUPPORTED"] - check["predicted"]["UNSUPPORTED"])
        _write_json(path / "proposal" / "resize_check.json", check)
        attempts.append({
            "id": folder, "archived_utc": _now(), "scale": self.processing_scale(record), "sheet_mm": saved["from_mm"],
            "status": previous["status"], "stage": analysis["basis"]["stage"],
            "blockers": [f["code"] for f in previous["flags"] if f["severity"] == "BLOCKER"],
            "detail_decisions": record.get("detail_decisions") or {},
            "replaced_by": {"kind": "physical_resize", "sheet_mm": saved["to_mm"], "status": result["status"],
                            "approved_visible_changes": len(saved["changes"]), "preview_id": saved["preview_id"]},
        })
        record["sheet_outer_mm"] = saved["to_mm"]
        record["detail_decisions"] = saved["decisions"]
        record["candidate"] = None
        self._save_record(name, record)
        return {"status": result["status"], "selected": result["selected"], "sheet_mm": saved["to_mm"],
                "after": check.get("after", {}).get("counts")}

    # -- 2. review / edit (L2) -----------------------------------------------------------
    def start_review(self, name: str, candidate: str, progress: Callable[..., None]) -> dict[str, Any]:
        path, record = self.path(name), self.record(name)
        proposal = _read_json(path / "proposal" / "proposal.json")
        if proposal is None:
            raise AppError("no proposal yet; run the proposal first")
        rows = {c["name"]: c for c in proposal["candidates"]}
        if candidate not in rows:
            raise AppError(f"unknown candidate {candidate!r}; candidates are {sorted(rows)}")
        if rows[candidate]["status"] == "PLANNING_FAILED":
            raise AppError(f"{candidate} failed planning and has no stack to review")
        if (path / "review").exists():
            raise AppError("a review already exists for this project")
        progress("REVIEW_STARTING", candidate=candidate)
        _, validation = review.start_review(path / "proposal" / "candidates" / candidate / "stack", path / "review")
        record["candidate"] = candidate
        self._save_record(name, record)
        return self.refresh_draft(name, None, progress, validation=validation)

    def refresh_draft(self, name: str, before: Any, progress: Callable[..., None],
                      change: dict[str, Any] | None = None,
                      validation: dict[str, Any] | None = None) -> dict[str, Any]:
        """Re-derive the cached validation, inspection and previews of the saved draft.

        ``validation`` is the ``validate_stack`` report of the saved draft when the
        caller already has it; ``None`` runs the validators again.
        """

        path = self.path(name)
        project = path / "review"
        stack = review.load_project(project)
        if validation is None:
            progress("VALIDATING")
            validation = validate_stack(stack)
        inspection = review.inspect_project(project)
        progress("PREVIEWING")
        previews = path / "draft" / "previews"
        shutil.rmtree(previews, ignore_errors=True)
        report = review.preview_project(project, previews, "base")
        if before is not None:
            report["changes_vs_previous"] = write_change_image(before, stack, previews / "changes_vs_previous.png")
        state = {
            "manifest_sha256": file_sha256(project / MANIFEST_NAME),
            "updated_utc": _now(),
            "validation": validation,
            "inspection": inspection,
            "previews": report,
            "last_change": change,
        }
        _write_json(path / "draft" / "state.json", state)
        return {"status": inspection["status"]["status"], "printable": inspection["status"]["printable"]}

    @staticmethod
    def check_op(op: Any) -> dict[str, Any]:
        """Request-level checks only; the edit itself is checked by ``review.apply_to_project``."""

        if not isinstance(op, dict) or op.get("op") not in EDIT_OPS:
            raise AppError(f"an edit needs 'op' set to one of {', '.join(EDIT_OPS)}")
        if "mask" in op or (isinstance(op.get("region"), dict) and "mask" in op["region"]):
            raise AppError("mask-file operands are only available from the command line (stack-edit apply)")
        return op

    def apply_edit(self, name: str, op: dict[str, Any], progress: Callable[..., None]) -> dict[str, Any]:
        op = self.check_op(op)
        project = self.review_dir(name)
        before = review.load_project(project)
        progress("APPLYING", op=op["op"])
        stack, entry = review.apply_to_project(project, op)
        change = {"action": "edit", "index": entry["index"], "op": entry["op"], "change": entry["change"],
                  "validation_status": entry["validation_status"], "failures": entry["failures"]}
        # apply_to_project already validated the edit; a passing draft needs no second
        # run (every gate check passed), a failing one is re-run for per-check details.
        validation = passing_validation(stack) if entry["validation_status"] == "PASS" else None
        result = self.refresh_draft(name, before, progress, change, validation)
        return {**result, "edit": entry["index"], "kind": entry["change"]["kind"]}

    def undo(self, name: str, keep: int | None, progress: Callable[..., None]) -> dict[str, Any]:
        project = self.review_dir(name)
        before = review.load_project(project)
        count = len(before.review["edits"])
        progress("REPLAYING", kept_edits=count - 1 if keep is None else keep)
        stack, validation = review.undo(project) if keep is None else review.revert(project, int(keep))
        change = {"action": "undo" if keep is None else "revert", "edits_before": count,
                  "edits_after": len(stack.review["edits"])}
        return self.refresh_draft(name, before, progress, change, validation)

    def verify(self, name: str, progress: Callable[..., None]) -> dict[str, Any]:
        progress("REPLAYING_LOG")
        report = review.verify_project(self.review_dir(name))
        report.pop("fingerprint", None)
        report["checked_utc"] = _now()
        _write_json(self.path(name) / "draft" / "verify.json", report)
        return report

    # -- 3. FDM estimate of the draft (L3) ---------------------------------------------
    def printability(self, name: str, process_values: dict[str, Any] | None,
                     progress: Callable[..., None]) -> dict[str, Any]:
        from ..core.printkit import assess_printability, printability_maps

        path = self.path(name)
        process = process_from(process_values)
        stack = review.load_project(self.review_dir(name))
        progress("ASSESSING_PRINTABILITY", sheets=len(stack.sheets), process=process.name)
        report = assess_printability(stack, process)
        out = path / "printability"
        shutil.rmtree(out, ignore_errors=True)
        maps = printability_maps(stack, report, out / "maps")
        for feature in report["features"]:
            feature.pop("_geometry", None)
        report.update(maps=[f"maps/{m}" for m in maps], manifest_sha256=file_sha256(path / "review" / MANIFEST_NAME),
                      checked_utc=_now(), physical=PHYSICAL_STATUS)
        _write_json(out / "printability.json", report)
        return {"status": report["status"], "counts": report["counts"]}

    # -- 4. print kit (L3) ---------------------------------------------------------------
    def build_kit(self, name: str, process_values: dict[str, Any] | None,
                  progress: Callable[..., None], bambu_printer: str | None = None) -> dict[str, Any]:
        from ..core.bambu3mf import BAMBU_PRINTERS
        from ..core.printkit import build_print_kit

        path, record = self.path(name), self.record(name)
        self.check_artwork_matches_stack(name)
        process = process_from(process_values)
        if bambu_printer and bambu_printer not in BAMBU_PRINTERS:
            raise AppError(f"unsupported Bambu printer {bambu_printer!r}")
        kit_id = "kit_" + datetime.now().strftime("%Y%m%d_%H%M%S")
        while (path / "kits" / kit_id).exists():
            kit_id += "x"
        progress("KIT_STARTED", kit=kit_id, process=process.name)
        try:
            kit = build_print_kit(self.review_dir(name), path / "kits" / kit_id, process, progress,
                                  bambu_printer=bambu_printer or None)
        except BaseException:
            shutil.rmtree(path / "kits" / kit_id, ignore_errors=True)
            raise
        record = self.record(name)  # re-read: a kit may have been deleted while this one was built
        record["kits"].append({"id": kit_id, "created_utc": _now(), "status": kit["status"]})
        self._save_record(name, record)
        return {"kit": kit_id, "status": kit["status"]}

    def kit_folder(self, name: str, kit_id: str) -> Path:
        """A kit's own directory: ``kits/<kit id>`` of this project, a real directory (not a link)."""

        folder = self._kit_entry(name, kit_id)
        if _is_link(folder) or not (folder / "print_kit.json").is_file():
            raise AppError(f"no kit {kit_id!r} in project {name!r}")
        return folder

    def _kit_entry(self, name: str, kit_id: str) -> Path:
        if not isinstance(kit_id, str) or not KIT_ID.fullmatch(kit_id):
            raise AppError(f"invalid kit id {kit_id!r}")
        return self.path(name) / "kits" / kit_id

    def delete_kit(self, name: str, kit_id: str) -> dict[str, Any]:
        """Delete one kit's generated files and its record entry.

        Only ``<project>/kits/<kit id>`` is touched (id format checked, no path parts); the
        source image, proposal, review project, draft, estimate and other kits are not.  Links
        (symlinks, junctions) are removed as links and never followed, so nothing outside the
        kit folder is deleted.  ``print_kit.json`` goes last: if a file cannot be removed (open
        in another program, no permission) the kit stays listed with what is left, and deleting
        again retries.  The record entry is dropped only when the folder is gone."""

        entry = self._kit_entry(name, kit_id)
        recorded = any(row["id"] == kit_id for row in self.record(name)["kits"])
        exists = os.path.lexists(entry)
        if not recorded and not exists:
            raise AppError(f"no kit {kit_id!r} in project {name!r}")
        removed, failed, notes = 0, [], []
        if not exists:
            notes.append("the kit folder was already missing on disk; removed from the list")
        elif _is_link(entry):
            failed += _remove_link(entry, kit_id)
            notes.append("the kit entry was a link to another location; only the link was removed")
            removed += 0 if failed else 1
        elif entry.is_dir():
            removed, failed = _remove_tree(entry, keep_last="print_kit.json")
        else:
            failed += _remove_file(entry, kit_id)
            removed += 0 if failed else 1
        gone = not os.path.lexists(entry)
        if gone:
            record = self.record(name)
            record["kits"] = [row for row in record["kits"] if row["id"] != kit_id]
            self._save_record(name, record)
        return {"kit": kit_id, "status": "DELETED" if gone else "PARTIALLY_DELETED", "removed_files": removed,
                "failed": failed[:50], "failed_count": len(failed), "notes": notes, "deleted_utc": _now()}

    def verify_kit(self, name: str, kit_id: str) -> dict[str, Any]:
        from ..core.printkit import verify_checksums

        kit = self.kit_path(name, kit_id)
        bad = verify_checksums(kit)
        return {"kit": kit_id, "checksums_ok": not bad, "changed_or_missing": bad, "checked_utc": _now()}

    def kit_path(self, name: str, kit_id: str) -> Path:
        return self.kit_folder(name, kit_id)

    def kit_zip(self, name: str, kit_id: str) -> bytes:
        kit = self.kit_path(name, kit_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(p for p in kit.rglob("*") if p.is_file()):
                archive.write(file, f"{name}_{kit_id}/{file.relative_to(kit).as_posix()}")
        return buffer.getvalue()

    # -- artwork recolor (flat-color target, not a sheet) ------------------------------------
    def artwork_basis(self, name: str, load: bool = True) -> tuple[Path, dict[str, Any], Any, dict[str, Any]]:
        """The artwork the stack was (or will be) planned from: the review's reviewed artwork if a
        review exists, else the proposal's intake target.  Returns (path, proposal, image, origin);
        ``load=False`` returns no image and takes the pixel hash from a cache keyed by file stamp."""

        from ..core import artwork as art

        path = self.path(name)
        proposal = _read_json(path / "proposal" / "proposal.json")
        if proposal is None:
            raise AppError("no processed artwork yet; run the proposal (or intake) first")
        if (path / "review" / MANIFEST_NAME).is_file() and (path / "review" / "target.png").is_file():
            file, stage = path / "review" / "target.png", "review"
        else:
            file, stage = path / "proposal" / "intake" / "target.png", "proposal"
        info = file.stat()
        key = (str(file), info.st_mtime_ns, info.st_size)
        image = art.load_rgb(file) if load or key not in self._basis_sha else None
        if image is not None:
            self._basis_sha[key] = art.rgb_sha256(image)
        origin = {"stage": stage, "file": file.relative_to(path).as_posix(), "sha256": self._basis_sha[key],
                  "proposal_status": proposal["status"]}
        return path, proposal, image if load else None, origin

    @staticmethod
    def _planned_sha(path: Path, proposal: dict[str, Any], basis_sha: str) -> str | None:
        """Hash of the artwork the current sheets were planned from (None: nothing planned yet)."""

        from ..core.propose import NOT_PLANNED

        return None if proposal["status"] == NOT_PLANNED else basis_sha

    def _artwork_log(self, name: str, create: bool = False) -> tuple[Path, dict[str, Any] | None, dict[str, Any]]:
        """The artwork edit log, (re)started on the current basis when it has no edits."""

        from ..core import artwork as art

        path, proposal, image, origin = self.artwork_basis(name, load=create)
        directory = path / "artwork"
        try:
            log = art.read_log(directory)
        except (art.ArtworkError, json.JSONDecodeError) as error:
            raise AppError(f"the artwork edit log cannot be read: {error}") from None
        if log is not None and not log["edits"] and log["base"]["sha256"] != origin["sha256"]:
            log = None  # an empty log on an artwork that has since been replaced
        if log is None and create:
            shutil.rmtree(directory, ignore_errors=True)
            log = art.start(directory, image, origin, proposal["frame_color"], proposal["intake"]["artwork_mm_per_pixel"])
        return directory, log, {"proposal": proposal, "origin": origin, "path": path}

    def artwork_differs(self, name: str) -> dict[str, Any] | None:
        """Why the current sheets are not validated against the current artwork, or None."""

        _, log, ctx = self._artwork_log(name)
        if not log or not log["edits"]:
            return None
        planned = self._planned_sha(ctx["path"], ctx["proposal"], ctx["origin"]["sha256"])
        if planned is None or log["current_sha256"] == planned:
            return None
        return {"edits": len(log["edits"]), "stage": ctx["origin"]["stage"]}

    def edited_artwork(self, name: str, load: bool = True) -> dict[str, Any] | None:
        """The artwork with region recolors that a physical resize must keep, or None.

        Either unplanned edits (``artwork/``, replayed and verified first) or a proposal already planned
        from edited artwork (its basis artwork: the review's, else the intake target it planned)."""

        from ..core import artwork as art

        path = self.path(name)
        proposal = _read_json(path / "proposal" / "proposal.json")
        if proposal is None:
            return None
        directory, log, ctx = self._artwork_log(name)
        if log and log["edits"]:
            out = {"pending": True, "sha256": log["current_sha256"], "region_recolors": len(log["edits"]),
                   "frame_color": log["base"]["frame_color"], "log_dir": directory,
                   "provenance": {"region_recolors": len(log["edits"]), "base_sha256": log["base"]["sha256"],
                                  "base_origin": log["base"]["origin"], "artwork_sha256": log["current_sha256"],
                                  "log": "artwork_edits/edits.json"}}
            if load:
                check = art.verify(directory)
                if not check["replay_identical"]:
                    raise AppError("the artwork edit log does not replay: " + "; ".join(check["problems"]))
                out["target_rgb"] = art.load_rgb(directory / "current.png")
            return out
        planned = proposal.get("artwork_edits")
        if not planned:
            return None
        origin = ctx["origin"]
        out = {"pending": False, "sha256": origin["sha256"], "region_recolors": planned["region_recolors"],
               "frame_color": proposal["frame_color"], "log_dir": path / "proposal" / "artwork_edits",
               "provenance": {**{k: v for k, v in planned.items() if k not in ("carried_by", "sheet_mm")},
                              "artwork_sha256": origin["sha256"], "log": "artwork_edits/edits.json"}}
        if load:
            out["target_rgb"] = self.artwork_basis(name)[2]
        return out

    @staticmethod
    def _carry_artwork_log(path: Path, archive: Path, edited: dict[str, Any] | None) -> None:
        """After a retry swapped in a new proposal: the edit history goes with it (the attempt keeps its own)."""

        if edited is None:
            shutil.rmtree(path / "artwork", ignore_errors=True)  # an empty log on the replaced artwork
            return
        if edited["pending"]:
            shutil.rmtree(path / "artwork" / "preview", ignore_errors=True)
            (path / "artwork").replace(path / "proposal" / "artwork_edits")
        else:  # the previous proposal (with its log) is in the archive now; the new one gets a copy
            source = archive / "proposal" / "artwork_edits"
            if source.is_dir():
                shutil.copytree(source, path / "proposal" / "artwork_edits")

    def refuse_if_artwork_edited(self, name: str, action: str) -> None:
        path = self.path(name)
        proposal = _read_json(path / "proposal" / "proposal.json") or {}
        why = ARTWORK_LIMITS.get(action, "it starts again from the source image")
        if proposal.get("artwork_edits"):
            raise AppError(f"this proposal was planned from artwork with your region recolors; {action} is not available "
                           f"for it because {why}, which would lose those recolors. Physical resize keeps them.")
        if (path / "proposal" / "proposal.json").is_file():
            _, log, _ = self._artwork_log(name)
            if log and log["edits"]:
                raise AppError(f"{len(log['edits'])} region recolor(s) are not planned yet; {action} is not available "
                               f"while they exist because {why}. Use Try Again (or Physical resize) to plan them, "
                               "or undo them first.")

    def check_artwork_matches_stack(self, name: str) -> None:
        differs = self.artwork_differs(name)
        if differs:
            raise AppError(f"the artwork was edited ({differs['edits']} region recolor(s)) after these sheets were planned; "
                           "the sheets are validated only against the previous artwork. Plan from the edited artwork "
                           "(the current attempt is kept) or undo the artwork edits before exporting.")

    def _artwork_op(self, op: Any) -> dict[str, Any]:
        from ..core import artwork as art

        try:
            return art.check_op(op)
        except art.ArtworkError as error:
            raise AppError(str(error)) from None

    def artwork_preview(self, name: str, request: Any) -> dict[str, Any]:
        """Select the region at a point (optionally inside a box) and, given a color, preview the recolor.

        Nothing is saved except preview images; Apply must name the returned ``preview_id``."""

        from ..core import artwork as art

        if not isinstance(request, dict):
            raise AppError("an artwork preview needs at_px and optionally box_px, color and source")
        with self._artwork_lock:
            directory, log, ctx = self._artwork_log(name, create=True)
            image = art.load_rgb(directory / "current.png")
            frame, mpp = log["base"]["frame_color"], log["base"]["mm_per_pixel"]
            out = directory / "preview"
            shutil.rmtree(out, ignore_errors=True)
            out.mkdir(parents=True)
            try:
                if not request.get("color"):
                    mask, selection = art.select_region(image, frame, request.get("at_px"), request.get("box_px"), mpp)
                    stamp = selection["region_sha256"][:12]
                    art.save_rgb(art.highlight(image, mask, selection["box_px"]), out / "selection.png")
                    return {"selection": selection, "images": {
                        "selection": self.url(name, "artwork/preview/selection.png", stamp)}}
                op = art.check_op({**request, "op": "recolor_region"})
                after, mask, selection, change = art.plan_edit(image, op, frame, mpp)
            except art.ArtworkError as error:
                raise AppError(str(error)) from None
            pid = art.preview_id(log["current_sha256"], op)
            art.save_rgb(art.highlight(image, mask, selection["box_px"]), out / "selection.png")
            art.save_rgb(after, out / "after.png")
            art.save_rgb(art.before_after(image, after, mask), out / "before_after.png")
            return {"preview_id": pid, "op": op, "selection": selection, "change": change,
                    "palette_after": art.palette_summary(after, frame, mpp),
                    "images": {k: self.url(name, f"artwork/preview/{k}.png", pid)
                               for k in ("selection", "after", "before_after")},
                    "notes": ["Artwork edit only: no sheet changes until you plan again from the edited artwork.",
                              "The sheet count is known only after planning; fewer colors do not guarantee fewer sheets."]}

    def artwork_sample(self, name: str, at_px: Any) -> dict[str, Any]:
        """Eyedropper: the exact artwork color at a point."""

        from ..core import artwork as art

        with self._artwork_lock:
            directory, log, ctx = self._artwork_log(name)
            image = art.load_rgb(directory / "current.png") if log else self.artwork_basis(name)[2]
            try:
                row, col = art.check_point(image, at_px)
            except art.ArtworkError as error:
                raise AppError(str(error)) from None
            color = "#%02X%02X%02X" % tuple(int(v) for v in image[row, col])
            frame = ctx["proposal"]["frame_color"]
            if frame and color == frame.upper():
                raise AppError("that point is the frame padding (never visible); sample a color inside the artwork")
            return {"color": color, "at_px": [row, col]}

    def artwork_apply(self, name: str, op: Any, preview: Any) -> dict[str, Any]:
        from ..core import artwork as art

        op = self._artwork_op(op)
        with self._artwork_lock:
            directory, log, _ = self._artwork_log(name)
            if log is None or preview != art.preview_id(log["current_sha256"], op):
                raise AppError("this preview is out of date (the artwork changed since); preview the edit again")
            try:
                _, entry = art.apply(directory, op)
            except art.ArtworkError as error:
                raise AppError(str(error)) from None
            shutil.rmtree(directory / "preview", ignore_errors=True)
            (directory / "verify.json").unlink(missing_ok=True)
            return {"edit": entry["index"], "change": entry["change"]}

    def artwork_undo(self, name: str, keep: Any = None) -> dict[str, Any]:
        from ..core import artwork as art

        with self._artwork_lock:
            directory, log, _ = self._artwork_log(name)
            if not log or not log["edits"]:
                raise AppError("there are no artwork edits to undo")
            count = len(log["edits"])
            if keep is None:
                keep = count - 1
            if isinstance(keep, bool) or not isinstance(keep, int):
                raise AppError("keep must be a whole number of edits")
            try:
                _, log = art.revert(directory, keep)
            except art.ArtworkError as error:
                raise AppError(str(error)) from None
            shutil.rmtree(directory / "preview", ignore_errors=True)
            (directory / "verify.json").unlink(missing_ok=True)
            return {"edits_before": count, "edits_after": len(log["edits"])}

    def artwork_verify(self, name: str) -> dict[str, Any]:
        from ..core import artwork as art

        with self._artwork_lock:
            directory, log, _ = self._artwork_log(name)
            if log is None:
                raise AppError("there are no artwork edits to verify")
            report = art.verify(directory)
            _write_json(directory / "verify.json", report)
            return report

    def check_artwork_replan(self, name: str) -> tuple[dict[str, Any] | None, dict[str, Any], Any]:
        """(log, context, artwork to plan) when planning from the current artwork is meaningful."""

        from ..core import artwork as art
        from ..core.propose import NOT_PLANNED

        with self._artwork_lock:
            directory, log, ctx = self._artwork_log(name)
            if log and log["edits"]:
                check = art.verify(directory)
                if not check["replay_identical"]:
                    raise AppError("the artwork edit log does not replay: " + "; ".join(check["problems"]))
                edited = art.load_rgb(directory / "current.png")
            else:
                edited = self.artwork_basis(name)[2]
            if ctx["proposal"]["status"] != NOT_PLANNED and art.rgb_sha256(edited) == ctx["origin"]["sha256"]:
                raise AppError("the artwork is the one the current sheets were planned from; there is nothing to re-plan")
        return log, ctx, edited

    def artwork_replan(self, name: str, progress: Callable[..., None]) -> dict[str, Any]:
        """Plan sheets from the edited artwork (explicit request only).

        The proposal is re-run with the same source, settings and detail choices; its intake target
        is replaced by the edited artwork (frame and grid unchanged) and planned by the unchanged
        planner.  The previous proposal, review, estimate and size check are archived as an attempt
        (kits stay listed); the artwork log moves with the new proposal as its provenance."""

        from ..core import artwork as art
        from ..core.intake import IntakeConfig
        from ..core.model import load_stack
        from ..core.planning import PlanningConfig
        from ..core.propose import NOT_PLANNED, ProposalConfig, propose

        path, record = self.path(name), self.record(name)
        log, ctx, edited = self.check_artwork_replan(name)
        proposal, origin = ctx["proposal"], ctx["origin"]
        edits = log["edits"] if log else []
        edited_sha = art.rgb_sha256(edited)
        if proposal["status"] != NOT_PLANNED and (path / "review" / MANIFEST_NAME).is_file():
            before_sheets = len(review.load_project(path / "review").sheets)
        else:
            row = next((c for c in proposal["candidates"] if c["name"] == proposal.get("selected")), None)
            before_sheets = row["sheet_count"] if row else None
        before = {"status": proposal["status"], "stage": origin["stage"], "palette_size": proposal["intake"]["palette_size"],
                  "sheet_count": before_sheets, "artwork_sha256": origin["sha256"]}
        s = record["proposal_settings"]
        staging = path / "proposal.next"
        shutil.rmtree(staging, ignore_errors=True)
        planning = PlanningConfig(outer_sheet_mm=self.sheet_outer_mm(record), scale=self.processing_scale(record),
                                  thickness_mm=s["thickness_mm"])
        provenance = {"region_recolors": len(edits), "base_sha256": log["base"]["sha256"] if log else origin["sha256"],
                      "base_origin": log["base"]["origin"] if log else origin, "artwork_sha256": edited_sha,
                      "log": "artwork_edits/edits.json" if edits else None}
        progress("ARTWORK_REPLAN_STARTING", edits=len(edits))
        try:
            result = propose(path / record["source"]["file"], staging, IntakeConfig(max_colors=s["max_colors"]),
                             ProposalConfig(planning=planning), progress, record.get("detail_decisions") or None,
                             artwork={"target_rgb": edited, "provenance": provenance} if edits else None)
        except StackError as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise AppError(f"could not plan from the edited artwork: {error}") from None
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        planned = art.load_rgb(staging / "intake" / "target.png")
        if art.rgb_sha256(planned) != edited_sha:
            shutil.rmtree(staging, ignore_errors=True)
            raise AppError("the planned artwork differs from the edited artwork; nothing was changed")
        with self._artwork_lock:
            attempts = record.setdefault("attempts", [])
            if proposal["status"] == NOT_PLANNED:
                shutil.rmtree(path / "proposal")  # intake only: nothing was planned, nothing to archive
                folder = None
            else:
                folder = f"attempt_{len(attempts) + 1:02d}_artwork"
                archive = path / "attempts" / folder
                archive.mkdir(parents=True, exist_ok=False)
                for part in ("proposal", "review", "draft", "printability", "resize"):
                    if (path / part).exists():
                        (path / part).replace(archive / part)
            staging.replace(path / "proposal")
            shutil.rmtree(path / "upscale", ignore_errors=True)
            if (path / "artwork").exists():
                shutil.rmtree(path / "artwork" / "preview", ignore_errors=True)
                if edits:
                    (path / "artwork").replace(path / "proposal" / "artwork_edits")
                else:
                    shutil.rmtree(path / "artwork", ignore_errors=True)
        after_sheets = None
        if result["selected"]:
            after_sheets = len(load_stack(path / "proposal" / "candidates" / result["selected"] / "stack").sheets)
        after = {"status": result["status"], "palette_size": result["intake"]["palette_size"],
                 "sheet_count": after_sheets, "selected": result["selected"], "artwork_sha256": edited_sha}
        _write_json(path / "proposal" / "artwork_check.json", {
            "before": before, "after": after, "region_recolors": len(edits), "archived_as": folder, "checked_utc": _now(),
            "note": "Sheet counts are measured from the planned stacks. Merging colors can leave the count unchanged "
                    "or raise it; only these measured values count."})
        if folder:
            attempts.append({
                "id": folder, "archived_utc": _now(), "scale": self.processing_scale(record),
                "sheet_mm": self.sheet_outer_mm(record), "status": proposal["status"], "stage": origin["stage"],
                "blockers": [f["code"] for f in proposal["flags"] if f["severity"] == "BLOCKER"],
                "detail_decisions": record.get("detail_decisions") or {},
                "replaced_by": {"kind": "artwork_edit", "region_recolors": len(edits), "status": result["status"],
                                "approved_visible_changes": len(edits)},
            })
        record["candidate"] = None
        self._save_record(name, record)
        return {"status": result["status"], "selected": result["selected"], "before": before, "after": after}

    def _artwork_view(self, name: str, path: Path, proposal: dict[str, Any]) -> dict[str, Any]:
        """Artwork colors panel: current artwork, its palette, the edit log and whether the sheets match it."""

        from ..core.propose import NOT_PLANNED

        try:
            directory, log, ctx = self._artwork_log(name)
        except Exception as error:  # noqa: BLE001 - a broken log must not hide the rest of the project
            return {"available": False, "reason": str(error)}
        origin = ctx["origin"]
        planned_sha = self._planned_sha(path, proposal, origin["sha256"])
        if log is not None:
            stamp = log["current_sha256"][:12]
            current_sha = log["current_sha256"]
            palette = log["edits"][-1]["palette_after"] if log["edits"] else log["base"]["palette"]
            image = self.url(name, "artwork/current.png", stamp)
        else:
            current_sha = origin["sha256"]
            palette = [{"hex": c["hex"], "area_mm2": c["area_mm2"], "regions_4": c["source_regions_4"]}
                       for c in proposal["intake"]["colors"]] if origin["stage"] == "proposal" else None
            image = self.url(name, origin["file"], current_sha[:12])
        edits = (log or {}).get("edits", [])
        differs = bool(edits) and planned_sha is not None and current_sha != planned_sha
        view = {
            "available": True, "stage": origin["stage"], "planned": proposal["status"] != NOT_PLANNED,
            "image": image, "frame_color": proposal["frame_color"],
            "mm_per_pixel": proposal["intake"]["artwork_mm_per_pixel"],
            "current_sha256": current_sha, "planned_sha256": planned_sha,
            "edits": [{k: e[k] for k in ("index", "op", "from", "to", "selection", "change", "applied_utc")} for e in edits],
            "palette": palette, "differs_from_planned": differs,
            "base_changed": bool(log and edits and log["base"]["sha256"] != origin["sha256"]),
            "can_replan": proposal["status"] == NOT_PLANNED or differs,
            "planned_from_edited_artwork": proposal.get("artwork_edits"),
        }
        verify = _read_json(directory / "verify.json")
        if verify is not None and log is not None:
            view["verify"] = verify
        return view

    # -- state for the UI --------------------------------------------------------------
    def state(self, name: str) -> dict[str, Any]:
        path, record = self.path(name), self.record(name)
        current_sha = file_sha256(path / "review" / MANIFEST_NAME) if (path / "review" / MANIFEST_NAME).is_file() else None
        state: dict[str, Any] = {
            "name": name, "record": record, "stage": self.stage(name), "physical": PHYSICAL_STATUS,
            "source_url": self.url(name, record["source"]["file"]),
        }
        proposal = _read_json(path / "proposal" / "proposal.json")
        if proposal is not None:
            state["proposal"] = self._proposal_view(name, path, proposal)
            state["upscale"] = self._upscale_view(name, path, record, proposal)
            state["resize"] = self._resize_view(name, path, record, proposal)
            state["artwork"] = self._artwork_view(name, path, proposal)
        draft = _read_json(path / "draft" / "state.json")
        if draft is not None:
            state["draft"] = self._draft_view(name, path, draft, current_sha)
        verify = _read_json(path / "draft" / "verify.json")
        if verify is not None:
            state["verify"] = verify
        estimate = _read_json(path / "printability" / "printability.json")
        if estimate is not None:
            estimate["stale"] = estimate.get("manifest_sha256") != current_sha
            estimate["map_urls"] = [self.url(name, f"printability/{m}") for m in estimate["maps"]]
            state["printability"] = estimate
        state["kits"] = [{**self._kit_view(name, path, row["id"], current_sha), "created_utc": row.get("created_utc")}
                         for row in reversed(record["kits"])
                         if KIT_ID.fullmatch(row["id"]) and not _is_link(path / "kits" / row["id"])
                         and (path / "kits" / row["id"] / "print_kit.json").is_file()]
        return state

    def url(self, name: str, relative: str, stamp: Any = None) -> str:
        suffix = f"?v={stamp}" if stamp else ""
        return f"/files/{name}/{relative}{suffix}"

    def _proposal_view(self, name: str, path: Path, proposal: dict[str, Any]) -> dict[str, Any]:
        stamp = int((path / "proposal" / "proposal.json").stat().st_mtime)
        view = {k: proposal.get(k) for k in ("status", "selected", "palette", "frame_color", "flags", "candidates",
                                             "cad", "seconds", "color_topology", "planning_config", "source",
                                             "artwork_edits")}
        check = _read_json(path / "proposal" / "artwork_check.json")
        if check is not None:
            view["artwork_check"] = check
        try:
            self.refuse_if_artwork_edited(name, "re-running intake")
        except AppError as error:
            view["intake_rerun_blocked"] = str(error)
        view["intake"] = {k: proposal["intake"].get(k) for k in ("palette_size", "colors", "transparency",
                                                                  "non_flat_interior_fraction", "snap_changed_fraction")
                          if k in proposal["intake"]}
        view["images"] = {"target": self.url(name, "proposal/intake/target.png", stamp)}
        view["details"] = [{**d, "image_url": self.url(name, f"proposal/{d['image']}", stamp)}
                           for d in proposal.get("intake_details", [])]
        view["detail_decisions"] = proposal.get("detail_decisions", {})
        if (path / "proposal" / "intake" / "details_overview.png").is_file():
            view["images"]["details_overview"] = self.url(name, "proposal/intake/details_overview.png", stamp)
        view["palette_added"] = proposal["intake"].get("palette_added_by_decisions", [])
        if (path / "proposal" / "intake" / "unmatched_regions.png").is_file():
            view["images"]["unmatched"] = self.url(name, "proposal/intake/unmatched_regions.png", stamp)
        selected = path / "proposal" / "selected" / "previews"
        for image in ("assembled_front", "exploded", "source_comparison"):
            if (selected / f"{image}.png").is_file():
                view["images"][image] = self.url(name, f"proposal/selected/previews/{image}.png", stamp)
        for row in view["candidates"]:
            if row.get("obstruction_map"):
                row["obstruction_url"] = self.url(name, f"proposal/candidates/{row['name']}/obstruction.png", stamp)
        return view

    def _upscale_view(self, name: str, path: Path, record: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        """Upscale & Retry: options while the proposal has failed, and the archived attempts."""

        from ..core.propose import NEEDS_DECISION
        from ..core.upscale import UPSCALE_FACTORS, resource_check

        scale = self.processing_scale(record)
        view: dict[str, Any] = {"scale": scale, "import_scale": record["proposal_settings"]["scale"], "attempts": []}
        for row in record.get("attempts", []):
            base = f"attempts/{row['id']}"
            if not re.fullmatch(r"attempt_\d{2}_(scale\d+|[\d_]+mm|artwork)", row["id"]) or _is_link(path / base):
                continue
            old = _read_json(path / base / "proposal" / "proposal.json") or {}
            view["attempts"].append({**row, "images": {
                "target": self.url(name, f"{base}/proposal/intake/target.png"),
                "obstruction": [self.url(name, f"{base}/proposal/candidates/{c['name']}/obstruction.png")
                                for c in old.get("candidates", []) if c.get("obstruction_map")],
                "comparison": next((self.url(name, f"{base}/{c}") for c in ("upscale_preview/comparison.png",
                                    "resize/preview/comparison.png") if (path / base / c).is_file()), None),
            }, "candidates": [{k: c.get(k) for k in ("name", "status", "reason", "sheet_count")} for c in old.get("candidates", [])]})
        view["available"] = proposal.get("status") == NEEDS_DECISION and not (path / "review").exists()
        if not view["available"]:
            return view
        try:
            self.refuse_if_artwork_edited(name, "Upscale & Retry")
        except AppError as error:  # shown instead of the options; the server refuses the request too
            view["blocked_by_artwork"] = str(error)
        view["options"] = [{"factor": f, **resource_check(self.sheet_outer_mm(record), scale * f)} for f in UPSCALE_FACTORS]
        view["previews"] = {}
        for factor in UPSCALE_FACTORS:
            base = f"upscale/x{factor}"
            saved = _read_json(path / base / "preview.json")
            if saved is None:
                continue
            stamp = int((path / base / "preview.json").stat().st_mtime)
            saved["current"] = saved.get("preview_id") == self._preview_id(record, factor)
            saved["images"] = {stem: self.url(name, f"{base}/{stem}.png", stamp)
                               for stem in ("comparison", "changes_highlighted", "target", "details_overview")
                               if (path / base / f"{stem}.png").is_file()}
            for row in saved.get("changes", []) + saved.get("details", []):
                if row.get("image"):
                    row["image_url"] = self.url(name, f"{base}/{row['image']}", stamp)
            view["previews"][str(factor)] = saved
        return view

    def _resize_view(self, name: str, path: Path, record: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
        """Physical size: current dimensions, the analysis and preview if current, the last re-check."""

        view: dict[str, Any] = {"sheet_mm": self.sheet_outer_mm(record),
                                "import_sheet_mm": float(record["proposal_settings"]["outer_mm"])}
        try:
            *_, basis = self.resize_subject(name)
            view["available"] = True
        except AppError as error:
            view.update(available=False, reason=str(error))
            basis = None
        check = _read_json(path / "proposal" / "resize_check.json")
        if check is not None:
            if check.get("after"):
                check["after"]["map_urls"] = [self.url(name, f"proposal/{m}") for m in check["after"].get("maps", [])
                                              if (path / "proposal" / m).is_file()]
            view["last_check"] = check
        analysis = _read_json(path / "resize" / "analysis.json") if basis else None
        if analysis is not None:
            analysis["current"] = analysis["basis"] == basis
            analysis["issues"] = [i for i in analysis["issues"]]  # full list (the UI groups and limits it)
            view["analysis"] = analysis
            saved = _read_json(path / "resize" / "preview" / "preview.json")
            if saved is not None:
                stamp = int((path / "resize" / "preview" / "preview.json").stat().st_mtime)
                saved["current"] = analysis["current"] and saved.get("preview_id") == self._resize_preview_id(
                    record, analysis, saved["to_mm"])
                saved["images"] = {stem: self.url(name, f"resize/preview/{stem}.png", stamp)
                                   for stem in ("comparison", "target") if (path / "resize" / "preview" / f"{stem}.png").is_file()}
                for row in saved.get("changes", []) + saved.get("details", []):
                    row["image_url"] = self.url(name, f"resize/preview/{row['image']}", stamp)
                view["preview"] = saved
        return view

    def _draft_view(self, name: str, path: Path, draft: dict[str, Any], current_sha: str | None) -> dict[str, Any]:
        stamp = draft["manifest_sha256"][:12] + str(int((path / "draft" / "state.json").stat().st_mtime))
        previews = draft["previews"]
        base = "draft/previews"
        images = {stem: self.url(name, f"{base}/{stem}.png", stamp)
                  for stem in ("assembled_front", "exploded", "source_comparison", "changes_vs_base", "changes_vs_previous")
                  if (path / base / f"{stem}.png").is_file()}
        sheets = []
        checks = {row["id"]: row for row in draft["validation"]["sheets"]}
        for row in draft["inspection"]["sheets_back_to_front"]:
            check = checks.get(row["id"], {})
            sheets.append({**row, "image": self.url(name, f"{base}/sheets/{row['export_name']}.png", stamp),
                           "checks": {k: check.get(k) for k in (
                               "pass", "four_neighbor", "width_status", "shared_perimeter_complete",
                               "visible_inside_retained", "retained_components_4", "orphan_structural_cores",
                               "thin_decorative_warnings", "narrow_openings_under_0_8mm")},
                           "vector_checks": check.get("vector_checks")})
        validation = draft["validation"]
        return {
            "stale": draft["manifest_sha256"] != current_sha,
            "updated_utc": draft["updated_utc"],
            "status": draft["inspection"]["status"],
            "base": draft["inspection"]["base"],
            "edits": draft["inspection"]["edits"],
            "islands": draft["inspection"]["islands"],
            "total_height_mm": draft["inspection"]["total_height_mm"],
            "sheets": sheets,
            "validation": {"status": validation["status"], "failures": validation["failures"],
                           "details_recomputed": validation.get("details_recomputed", True),
                           "reconstruction": validation["reconstruction"], "mm_per_pixel": validation["mm_per_pixel"],
                           "outer_sheet_mm": validation["outer_sheet_mm"][0],
                           "repeated_colors": validation["repeated_colors"]},
            "previews": {"images": images, "notice": previews.get("notice"),
                         "changes_vs_base": previews.get("changes_vs_base"),
                         "changes_vs_previous": previews.get("changes_vs_previous"),
                         "mask_source": previews.get("mask_source")},
            "last_change": draft.get("last_change"),
        }

    def _kit_view(self, name: str, path: Path, kit_id: str, current_sha: str | None) -> dict[str, Any]:
        kit = _read_json(path / "kits" / kit_id / "print_kit.json")
        base = f"kits/{kit_id}"
        findings = [f for f in kit["printability"]["features"] if f["severity"] != "INFO"]
        folder = path / "kits" / kit_id
        missing = [row["file_stl"] for row in kit["assembly_back_to_front"] if not (folder / row["file_stl"]).is_file()]
        missing += [row["file_step"] for row in kit["assembly_back_to_front"] if not (folder / row["file_step"]).is_file()]
        return {
            "id": kit_id,
            "folder": f"{name}/kits/{kit_id}",
            "base_url": self.url(name, base),
            "missing_files": missing,
            "process_name": kit["printability"]["process"]["name"],
            "stale": kit["source_stack"]["manifest_sha256"] != current_sha,
            "status": kit["status"],
            "cad_problems": kit["cad_problems"],
            "printability": {"status": kit["printability"]["status"], "counts": kit["printability"]["counts"],
                             "process": kit["printability"]["process"], "findings": findings},
            "comparisons": kit["comparisons"],
            "assembly": kit["assembly_back_to_front"],
            "filament_by_color": kit["filament_by_color"],
            "total_stack_height_mm": kit["total_stack_height_mm"],
            "not_included": kit["not_included"],
            "bambu": self._bambu_view(name, base, kit.get("bambu_3mf")),
            "images": {stem: self.url(name, f"{base}/previews/{stem}.png") for stem in
                       ("assembled_front", "assembled_side", "exploded_labelled", "source_comparison")
                       if (path / base / "previews" / f"{stem}.png").is_file()},
            "maps": [self.url(name, f"{base}/previews/{f}") for f in kit["previews"]["files"]
                     if f.startswith("printability/")],
            "guide_url": self.url(name, f"{base}/PRINT_AND_STACK_GUIDE.md"),
            "manifest_url": self.url(name, f"{base}/print_kit.json"),
            "zip_url": f"/api/projects/{name}/kits/{kit_id}/zip",
        }

    def _bambu_view(self, name: str, base: str, report: dict[str, Any] | None) -> dict[str, Any] | None:
        if report is None:
            return None
        check = report.get("bambu_studio_check") or {}
        return {
            "status": report["status"], "printer": report["printer"], "problems": report.get("problems", []),
            "validation": (report.get("validation") or {}).get("level"),
            "check_status": check.get("status"), "check_tool": check.get("tool"), "check_problems": check.get("problems", []),
            "process_preset": report.get("process_preset"), "notes": report.get("notes", []),
            "plates": [{k: p.get(k) for k in ("plate", "plate_name", "filament_slot", "color_hex", "bed_position_mm")}
                       for p in report.get("plates", [])],
            "url": self.url(name, f"{base}/{report['file']}") if report.get("file") else None,
        }

    def file(self, name: str, relative: str) -> Path:
        """A file inside the project directory (no escaping it)."""

        root = self.path(name).resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            raise AppError(f"no file {relative!r} in project {name!r}")
        return target
