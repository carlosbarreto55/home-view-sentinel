"""Export an Ultralytics YOLO model to CoreML with NMS (``make setup``).

Writes ``models/<name>.mlpackage`` (git-ignored) and records what was exported
in ``models/README.md``. Needs the ``export`` dependency group (Ultralytics and
PyTorch), which ``make setup`` installs for the export and removes afterwards
so the detector runs without PyTorch.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from sentinel.detector.inference import MODELS_DIR, model_path

# Height x width of the model input: 1280x720 frames scale to 640x360 and are
# padded to 640x384 (a multiple of the model's stride, 32).
DEFAULT_IMGSZ = (384, 640)


def export(name: str, imgsz: tuple[int, int], models_dir: Path = MODELS_DIR) -> Path:
    import coremltools
    import torch
    import ultralytics
    from ultralytics import YOLO

    target = model_path(name, models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as work:
        weights = Path(work) / f"{name}.pt"
        model = YOLO(str(weights))  # downloads the weights into the work folder
        exported = Path(model.export(format="coreml", nms=True, imgsz=list(imgsz)))
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(exported), target)
    record(
        models_dir / "README.md",
        name=name,
        imgsz=imgsz,
        versions={
            "ultralytics": ultralytics.__version__,
            "coremltools": coremltools.__version__,
            "torch": torch.__version__,
        },
    )
    return target


def record(
    path: Path,
    name: str,
    imgsz: tuple[int, int],
    versions: dict[str, str],
    today: date | None = None,
) -> None:
    """Write the model's row in the README, keeping the rows of the other models."""
    prefix = f"| `{name}.mlpackage` |"
    others = []
    if path.exists():
        others = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `") and not line.startswith(prefix)
        ]
    row = (
        f"{prefix} {imgsz[0]} x {imgsz[1]} | {(today or date.today()).isoformat()} "
        f"| {versions['ultralytics']} | {versions['coremltools']} | {versions['torch']} |"
    )
    lines = [
        "# Models",
        "",
        "Exported by `make setup` (`python -m sentinel.detector.export`); the",
        "`.mlpackage` files are not committed.",
        "",
        "| Model | Input (h x w) | Exported | Ultralytics | coremltools | PyTorch |",
        "|---|---|---|---|---|---|",
        *sorted([*others, row]),
        "",
        "Ultralytics YOLO is AGPL-3.0, acceptable for this personal, undistributed project.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sentinel.detector.export")
    parser.add_argument("--model", default="yolo11s", help="yolo11n or yolo11s")
    parser.add_argument("--imgsz", type=int, nargs=2, default=DEFAULT_IMGSZ, metavar=("H", "W"))
    parser.add_argument("--force", action="store_true", help="export again even if present")
    args = parser.parse_args(argv)
    target = model_path(args.model)
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to export again)")
        return 0
    print(f"exported {export(args.model, tuple(args.imgsz))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
