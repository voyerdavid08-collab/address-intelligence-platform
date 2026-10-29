import csv
import json

from scripts import compare_geocode_modes


def _cache(path, city, zip5, code):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "status_code": 200,
                "data": {
                    "candidates": [
                        {
                            "precisionCode": code,
                            "address": {
                                "areaName3": city,
                                "postCode1": zip5,
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )


def test_compare_script_only_prints_ids_in_both_caches(tmp_path, monkeypatch, capsys):
    input_path = tmp_path / "policyholders.csv"
    with input_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=("policy_id", "raw_city", "raw_zip5"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            (
                {"policy_id": "POL-1", "raw_city": "Sample City", "raw_zip5": "12345"},
                {"policy_id": "POL-2", "raw_city": "Other City", "raw_zip5": "54321"},
            )
        )
    single_dir = tmp_path / "single"
    structured_dir = tmp_path / "structured"
    _cache(single_dir / "POL-1.json", "Sample City", "12345", "S8")
    _cache(structured_dir / "POL-1.json", "Sample City", "12345-9999", "G3")
    _cache(single_dir / "POL-2.json", "Other City", "54321", "S4")
    monkeypatch.setattr(compare_geocode_modes, "INPUT_PATH", input_path)
    monkeypatch.setattr(compare_geocode_modes, "SINGLE_CACHE", single_dir)
    monkeypatch.setattr(compare_geocode_modes, "STRUCTURED_CACHE", structured_dir)

    assert compare_geocode_modes.main() == 0

    output = capsys.readouterr().out
    assert "POL-1" in output
    assert "POL-2" not in output
    assert "S5/S7/S8" in output
    assert "Z/G" in output
    assert "single:yes; structured:yes" in output
