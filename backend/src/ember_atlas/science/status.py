"""Calibration status: what the app is allowed to claim.

The label shown with any harmonised number is decided here, from data origin,
coverage mode, sufficiency checks and held-out validation gates. The words
"validated" appear only for code ``validated_holdout`` (real data, real
coverage exposure, all gates passed) and even then mean *agreement with the
reference platform on held-out blocks*, not accuracy against ground truth.
"""

from __future__ import annotations


def sufficiency(info: dict, cfg: dict) -> dict:
    c = cfg["calibration"]
    checks = [
        ("paired active cell-weeks", info.get("n_paired_active_units", 0), c["min_paired_units"]),
        (f"{info.get('reference')} quality detections in overlap", info.get("total_ref", 0), c["min_detections_per_platform"]),
        (f"{info.get('source')} quality detections in overlap", info.get("total_src", 0), c["min_detections_per_platform"]),
        ("overlap years", len(info.get("overlap_years", [])), c["min_years"]),
        ("spatial blocks", info.get("n_spatial_blocks", 0), c["min_spatial_blocks"]),
    ]
    rows = [{"check": n, "value": v, "required": r, "passed": v >= r} for n, v, r in checks]
    return {"checks": rows, "passed": all(r["passed"] for r in rows)}


def decide(data_origin: str, mode: str, suff: dict, evaluation: dict | None) -> dict:
    if not suff["passed"]:
        failed = [r["check"] for r in suff["checks"] if not r["passed"]]
        return {
            "code": "insufficient_data",
            "label": "Calibration refused: not enough overlapping evidence",
            "show_harmonized": False,
            "explanation": "Only sensor-specific raw series are shown. Insufficient: " + ", ".join(failed) + ".",
        }
    if evaluation is None:
        return {
            "code": "not_evaluated",
            "label": "Unvalidated candidate calibration",
            "show_harmonized": True,
            "explanation": "The calibration has been fitted but blocked hold-out validation has not been run for this selection. Treat converted values as provisional.",
        }
    if not evaluation["gates"]["passed"]:
        failed = [c["check"] for c in evaluation["gates"]["checks"] if not c["passed"]]
        return {
            "code": "failed_validation",
            "label": "Calibration did not pass held-out validation",
            "show_harmonized": False,
            "explanation": "The candidate did not beat the baselines or its intervals were miscalibrated ("
            + "; ".join(failed)
            + "). Raw sensor-specific series are shown; the candidate is available only as a diagnostic.",
        }
    if data_origin == "synthetic":
        return {
            "code": "synthetic_demo_verified",
            "label": "Synthetic demo: method checks passed on synthetic data",
            "show_harmonized": True,
            "explanation": "Held-out checks passed on SYNTHETIC data with a known injected sensor effect. This shows the machinery works; it is not evidence about real MODIS/VIIRS behaviour.",
        }
    if mode != "exposure":
        return {
            "code": "research_estimate",
            "label": "Research estimate (coverage-unadjusted, not validated)",
            "show_harmonized": True,
            "explanation": "Held-out agreement checks passed, but no valid-observation coverage was available, so differences in cloud/swath coverage are absorbed into the ratio (confounded). Use as an exploratory estimate only.",
        }
    return {
        "code": "validated_holdout",
        "label": "Held-out cross-sensor agreement validated",
        "show_harmonized": True,
        "explanation": "Passed blocked temporal and spatial hold-out checks against the reference platform with coverage exposure. This validates agreement with the reference sensor scale, not accuracy against independent ground truth.",
    }
