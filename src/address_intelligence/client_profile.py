"""Load, validate, and estimate costs from client-specific YAML profiles."""

import csv
from dataclasses import dataclass
from decimal import Decimal
import math
import os
from pathlib import Path
from typing import Any

import yaml

from address_intelligence.credits import PRICE_TABLE


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "config" / "client_profile.yaml"
CANONICAL_COLUMNS = (
    "policy_id",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "zip5",
    "metro",
)
TOP_LEVEL_KEYS = {
    "client",
    "input",
    "output",
    "evaluation",
    "verification",
    "enrichment",
    "comparison",
    "ai",
    "data_handling",
}
ENRICHMENT_OPERATIONS = {
    "flood_by_location",
    "distance_to_flood_hazard_by_location",
    "property_attributes_v2",
}
SECRET_KEY_PARTS = ("key", "secret", "token", "password")
_INTERNAL_COLUMN_NAMES = {
    "policy_id": "policy_id",
    "address_line1": "raw_address_line1",
    "address_line2": "raw_address_line2",
    "city": "raw_city",
    "state": "raw_state",
    "zip5": "raw_zip5",
    "metro": "metro",
}


def _config_error(message: str) -> Exception:
    from address_intelligence.config import ConfigError

    return ConfigError(message)


def _reject_secret_keys(value: Any, path: str = "profile") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            if any(part in name.casefold() for part in SECRET_KEY_PARTS):
                raise _config_error(
                    f"Client profile field '{path}.{name}' resembles a secret; keep secrets in .env."
                )
            _reject_secret_keys(child, f"{path}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secret_keys(child, f"{path}[{index}]")


@dataclass(frozen=True)
class ClientProfile:
    path: Path
    project_root: Path
    data: dict[str, Any]

    @property
    def input_path(self) -> Path:
        candidate = Path(self.data["input"]["path"])
        return candidate if candidate.is_absolute() else self.project_root / candidate

    @property
    def run_dir(self) -> Path:
        candidate = Path(self.data["output"]["run_dir"])
        return candidate if candidate.is_absolute() else self.project_root / candidate

    @property
    def reports_dir(self) -> Path:
        candidate = Path(self.data["output"]["reports_dir"])
        return candidate if candidate.is_absolute() else self.project_root / candidate

    @property
    def ground_truth_path(self) -> Path | None:
        value = self.data.get("evaluation", {}).get("ground_truth_path")
        if value is None:
            return None
        candidate = Path(value)
        return candidate if candidate.is_absolute() else self.project_root / candidate

    @property
    def column_mapping(self) -> dict[str, str | None]:
        return dict(self.data["input"]["column_mapping"])

    @property
    def eligible_precision_prefixes(self) -> tuple[str, ...]:
        return tuple(self.data["verification"]["eligible_precision_prefixes"])

    @property
    def comparison_enabled(self) -> bool:
        return bool(self.data["comparison"]["enabled"])

    @property
    def comparison_sample_size(self) -> int:
        return int(self.data["comparison"]["sample_size"])

    @property
    def comparison_seed(self) -> int:
        return int(self.data["comparison"]["seed"])

    @property
    def remediation_samples(self) -> int:
        return int(self.data["ai"]["remediation"]["samples"])

    @property
    def conformal_alpha(self) -> float:
        return float(self.data["ai"]["remediation"]["conformal_alpha"])

    def ai_enabled(self, step: str) -> bool:
        if step not in {"narratives", "remediation"}:
            raise ValueError(f"Unknown AI step: {step}")
        return bool(self.data["ai"][step]["enabled"])

    def operation_enabled(self, operation: str) -> bool:
        if operation == "preciselyid_by_address":
            return bool(self.data["enrichment"]["preciselyid_by_address"]["enabled"])
        return any(
            item["name"] == operation and bool(item["enabled"])
            for item in self.data["enrichment"]["operations"]
        )

    @property
    def enabled_operations(self) -> tuple[str, ...]:
        names = [
            item["name"]
            for item in self.data["enrichment"]["operations"]
            if item["enabled"]
        ]
        if self.data["enrichment"]["preciselyid_by_address"]["enabled"]:
            names.append("preciselyid_by_address")
        return tuple(names)


def _validate_profile(data: Any, profile_path: Path, project_root: Path) -> ClientProfile:
    if not isinstance(data, dict):
        raise _config_error("Client profile must contain a YAML mapping.")
    _reject_secret_keys(data)
    unknown = set(data) - TOP_LEVEL_KEYS
    if unknown:
        raise _config_error(f"Unknown client profile key(s): {', '.join(sorted(unknown))}.")
    missing_top = (TOP_LEVEL_KEYS - {"evaluation"}) - set(data)
    if missing_top:
        raise _config_error(f"Missing client profile section(s): {', '.join(sorted(missing_top))}.")
    data.setdefault("evaluation", {})
    if not isinstance(data["evaluation"], dict):
        raise _config_error("evaluation must be a mapping.")
    try:
        client = data["client"]
        if not isinstance(client["name"], str) or not client["name"].strip():
            raise _config_error("client.name must be a non-empty string.")
        if not isinstance(client["country"], str) or not client["country"].strip():
            raise _config_error("client.country must be a non-empty string.")
        input_config = data["input"]
        mapping = input_config["column_mapping"]
        if not isinstance(mapping, dict):
            raise _config_error("input.column_mapping must be a mapping.")
        missing_mapping = (set(CANONICAL_COLUMNS) - {"metro"}) - set(mapping)
        if missing_mapping:
            raise _config_error(
                f"Missing mapped column(s): {', '.join(sorted(missing_mapping))}."
            )
        unknown_mapping = set(mapping) - set(CANONICAL_COLUMNS)
        if unknown_mapping:
            raise _config_error(
                f"Unknown canonical column mapping key(s): {', '.join(sorted(unknown_mapping))}."
            )
        for canonical, source_name in mapping.items():
            if canonical in {"address_line2", "metro"} and source_name is None:
                continue
            if not isinstance(source_name, str) or not source_name.strip():
                raise _config_error(
                    f"input.column_mapping.{canonical} must be a column name"
                    + (" or null." if canonical in {"address_line2", "metro"} else ".")
                )
        if input_config["id_column"] != mapping["policy_id"]:
            raise _config_error("input.id_column must match input.column_mapping.policy_id.")
        if not isinstance(input_config["path"], str) or not input_config["path"]:
            raise _config_error("input.path must be a non-empty path.")
        output = data["output"]
        if not isinstance(output["run_dir"], str) or not output["run_dir"]:
            raise _config_error("output.run_dir must be a non-empty path.")
        if not isinstance(output["reports_dir"], str) or not output["reports_dir"]:
            raise _config_error("output.reports_dir must be a non-empty path.")
        prefixes = data["verification"]["eligible_precision_prefixes"]
        if not isinstance(prefixes, list) or not prefixes or not all(
            isinstance(prefix, str) and prefix for prefix in prefixes
        ):
            raise _config_error("verification.eligible_precision_prefixes must be a non-empty list.")
        operations = data["enrichment"]["operations"]
        if not isinstance(operations, list):
            raise _config_error("enrichment.operations must be a list.")
        seen_operations = set()
        for operation in operations:
            name = operation["name"]
            if name in seen_operations:
                raise _config_error(f"Duplicate enrichment operation: {name}.")
            seen_operations.add(name)
            if name not in PRICE_TABLE or name not in ENRICHMENT_OPERATIONS:
                raise _config_error(f"Unknown enrichment operation: {name}.")
            if not isinstance(operation.get("enabled"), bool):
                raise _config_error(f"enrichment operation '{name}' requires a boolean enabled flag.")
            if name == "property_attributes_v2":
                attrs = operation.get("attributes", [])
                if not isinstance(attrs, list) or not all(isinstance(attr, str) for attr in attrs):
                    raise _config_error("property_attributes_v2 attributes must be a list of strings.")
        precise = data["enrichment"]["preciselyid_by_address"]
        if not isinstance(precise.get("enabled"), bool):
            raise _config_error("enrichment.preciselyid_by_address requires a boolean enabled flag.")
        if "preciselyid_by_address" not in PRICE_TABLE:
            raise _config_error("Unknown credit operation: preciselyid_by_address.")
        comparison = data["comparison"]
        if not isinstance(comparison["enabled"], bool):
            raise _config_error("comparison.enabled must be a boolean.")
        if (
            isinstance(comparison["sample_size"], bool)
            or not isinstance(comparison["sample_size"], int)
            or comparison["sample_size"] < 1
        ):
            raise _config_error("comparison.sample_size must be at least 1.")
        if isinstance(comparison["seed"], bool) or not isinstance(comparison["seed"], int):
            raise _config_error("comparison.seed must be an integer.")
        remediation = data["ai"]["remediation"]
        samples = remediation["samples"]
        if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
            raise _config_error("ai.remediation.samples must be at least 1.")
        alpha = remediation["conformal_alpha"]
        if (
            isinstance(alpha, bool)
            or not isinstance(alpha, (int, float))
            or not math.isfinite(alpha)
            or not 0 < alpha < 1
        ):
            raise _config_error("ai.remediation.conformal_alpha must be between 0 and 1.")
        if not isinstance(data["ai"]["narratives"]["enabled"], bool):
            raise _config_error("ai.narratives.enabled must be a boolean.")
        if not isinstance(remediation["enabled"], bool):
            raise _config_error("ai.remediation.enabled must be a boolean.")
        if not isinstance(data["data_handling"]["publish_row_level"], bool):
            raise _config_error("data_handling.publish_row_level must be a boolean.")
        retention_days = data["data_handling"]["retention_days"]
        if (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or retention_days < 1
        ):
            raise _config_error("data_handling.retention_days must be at least 1.")
        note = data["data_handling"].get("private_workbook_note")
        if note is not None and (
            not isinstance(note, list)
            or not note
            or any(not isinstance(line, str) or not line.strip() for line in note)
        ):
            raise _config_error(
                "data_handling.private_workbook_note must be a list of non-empty strings."
            )
    except KeyError as exc:
        raise _config_error(f"Missing client profile field: {exc.args[0]}.") from None
    except (TypeError, ValueError) as exc:
        raise _config_error(f"Invalid client profile value: {exc}.") from None

    profile = ClientProfile(profile_path, project_root, data)
    output_root = (project_root / "data" / "output").resolve()
    try:
        profile.run_dir.resolve().relative_to(output_root)
    except ValueError:
        raise _config_error("output.run_dir must resolve inside data/output/.") from None
    reports_path = profile.reports_dir.resolve()
    if reports_path != (project_root / "reports").resolve():
        try:
            reports_path.relative_to(output_root)
        except ValueError:
            raise _config_error(
                "output.reports_dir must be exactly 'reports' or resolve inside data/output/."
            ) from None
    evaluation = data["evaluation"]
    ground_truth = evaluation.get("ground_truth_path")
    if ground_truth is not None and (
        not isinstance(ground_truth, str) or not ground_truth.strip()
    ):
        raise _config_error("evaluation.ground_truth_path must be a path or null.")
    if not profile.input_path.is_file():
        raise _config_error(f"Client input file does not exist: {profile.input_path}.")
    try:
        with profile.input_path.open(newline="", encoding="utf-8-sig") as source:
            columns = set(next(csv.reader(source), []))
    except OSError as exc:
        raise _config_error(f"Unable to read client input file: {exc}.") from None
    missing_columns = sorted(
        {name for name in mapping.values() if name is not None} - columns
    )
    if missing_columns:
        raise _config_error(
            f"Client input is missing mapped column(s): {', '.join(missing_columns)}."
        )
    return profile


def load_client_profile(
    profile_path: str | Path | None = None,
    *,
    project_root: str | Path = PROJECT_ROOT,
) -> ClientProfile:
    selected = profile_path or os.environ.get("CLIENT_PROFILE") or DEFAULT_PROFILE_PATH
    path = Path(selected)
    root = Path(project_root).resolve()
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        with path.open(encoding="utf-8") as source:
            data = yaml.safe_load(source)
    except OSError as exc:
        raise _config_error(f"Unable to read client profile '{path}': {exc}.") from None
    except yaml.YAMLError as exc:
        raise _config_error(f"Unable to parse client profile '{path}': {exc}.") from None
    profile = _validate_profile(data, path, root)
    _warn_if_run_dir_shared(profile)
    return profile


def _warn_if_run_dir_shared(profile: ClientProfile) -> None:
    config_root = profile.project_root / "config"
    if not config_root.is_dir():
        return
    target = profile.run_dir.resolve()
    for candidate in config_root.rglob("*.yaml"):
        if candidate.resolve() == profile.path.resolve():
            continue
        try:
            with candidate.open(encoding="utf-8") as source:
                data = yaml.safe_load(source)
            run_dir = data.get("output", {}).get("run_dir") if isinstance(data, dict) else None
            if not isinstance(run_dir, str):
                continue
            other = Path(run_dir)
            other = other if other.is_absolute() else profile.project_root / other
            if other.resolve() == target:
                print(
                    f"Warning: client profiles '{profile.path}' and '{candidate}' "
                    f"share run_dir '{target}'."
                )
                return
        except (OSError, yaml.YAMLError):
            continue


def read_client_policies(profile: ClientProfile) -> list[dict[str, str]]:
    with profile.input_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        columns = set(reader.fieldnames or ())
        mapping = profile.column_mapping
        missing = sorted(
            {name for name in mapping.values() if name is not None} - columns
        )
        if missing:
            raise _config_error(
                f"Client input is missing mapped column(s): {', '.join(missing)}."
            )
        rows = []
        for source_row in reader:
            row = dict(source_row)
            for canonical, source_name in mapping.items():
                if source_name is None:
                    row[_INTERNAL_COLUMN_NAMES[canonical]] = ""
                    continue
                row[_INTERNAL_COLUMN_NAMES[canonical]] = source_row[source_name]
            rows.append(row)
        return rows


def estimate_cost(
    profile: ClientProfile,
    records: int,
    eligible_share: float,
) -> dict[str, Decimal]:
    if isinstance(records, bool) or records < 0:
        raise ValueError("records must be a non-negative integer.")
    if not isinstance(records, int):
        raise ValueError("records must be a non-negative integer.")
    if (
        isinstance(eligible_share, bool)
        or not math.isfinite(eligible_share)
        or not 0 <= eligible_share <= 1
    ):
        raise ValueError("eligible_share must be between 0 and 1.")
    geocode = Decimal(records) * PRICE_TABLE["geocode_premium"]
    downstream_rate = sum(
        (PRICE_TABLE[operation] for operation in profile.enabled_operations),
        Decimal("0"),
    )
    downstream = Decimal(records) * Decimal(str(eligible_share)) * downstream_rate
    total = geocode + downstream
    return {
        "geocode_credits": geocode,
        "eligible_operations_credits": downstream,
        "total_credits": total,
        "list_price_usd": total * Decimal("0.01"),
        "credits_per_policy": (
            total / Decimal(records) if records else Decimal("0")
        ),
    }


def estimate_cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Estimate profile-based Precisely credit costs.")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--records", type=int)
    parser.add_argument("--eligible-share", type=float)
    args = parser.parse_args(argv)
    eligible_share = 0.85 if args.eligible_share is None else args.eligible_share
    if args.records is None:
        profile = load_client_profile(args.profile)
        with profile.input_path.open(newline="", encoding="utf-8-sig") as source:
            records = max(0, sum(1 for _ in csv.DictReader(source)))
    else:
        profile = load_client_profile(args.profile)
        records = args.records
    estimate = estimate_cost(profile, records, eligible_share)
    print(f"Client: {profile.data['client']['name']}")
    print(f"Records: {records}")
    print(f"Eligible share: {eligible_share:.1%}")
    if args.eligible_share is None:
        print("Eligible share default: 0.85, measured on the synthetic sample.")
    print(f"Enabled priced operations: geocode_premium, {', '.join(profile.enabled_operations)}")
    print(f"Estimated credits: {estimate['total_credits']:,.2f}")
    print(f"Estimated list price USD: ${estimate['list_price_usd']:.2f}")
    print(f"Credits per policy: {estimate['credits_per_policy']:.2f}")
    print("Indicative list price; enterprise pricing, risk-use fees and integration excluded.")
    return 0
