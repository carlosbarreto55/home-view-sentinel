from datetime import date

from sentinel.detector.export import record

VERSIONS = {"ultralytics": "8.4.182", "coremltools": "9.0", "torch": "2.7.0"}


def rows(path):
    return [line for line in path.read_text().splitlines() if line.startswith("| `")]


def test_record_keeps_the_other_models(tmp_path):
    readme = tmp_path / "README.md"
    record(readme, "yolo11n", (384, 640), VERSIONS, today=date(2026, 10, 18))
    record(readme, "yolo11s", (384, 640), VERSIONS, today=date(2026, 10, 18))
    assert rows(readme) == [
        "| `yolo11n.mlpackage` | 384 x 640 | 2026-10-18 | 8.4.182 | 9.0 | 2.7.0 |",
        "| `yolo11s.mlpackage` | 384 x 640 | 2026-10-18 | 8.4.182 | 9.0 | 2.7.0 |",
    ]


def test_record_replaces_the_row_of_a_model_exported_again(tmp_path):
    readme = tmp_path / "README.md"
    record(readme, "yolo11n", (384, 640), VERSIONS, today=date(2026, 10, 18))
    record(readme, "yolo11n", (640, 640), VERSIONS, today=date(2026, 10, 19))
    assert rows(readme) == [
        "| `yolo11n.mlpackage` | 640 x 640 | 2026-10-19 | 8.4.182 | 9.0 | 2.7.0 |",
    ]
