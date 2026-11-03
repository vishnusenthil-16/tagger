"""Gate A: validate and freeze evaluation inputs; no inference or network calls."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess

TARGETS = {
    "accepted_precision_min": 0.95,
    "review_rate_max": 0.30,
    "no_match_precision_min": 0.90,
    "no_match_recall_min": 0.70,
    "warm_p95_seconds_max": 2.0,
    "concurrency": 1,
    "local_total_peak_rss_bytes_max": 8 * 1024**3,
}
REQUIRED_CASES = {"clear", "ambiguous", "out_of_category", "instruction_like", "long_tail", "misleading_ids"}
SPLITS = ("tuning", "held_out")


def digest(value):
    """Canonical JSON; array order is significant, object key order is not."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(taxonomy, rows, multilingual=False):
    require(isinstance(taxonomy, list) and 2 <= len(taxonomy) <= 19, "taxonomy needs 2–19 options")
    labels = set()
    for option in taxonomy:
        require(isinstance(option, dict) and set(option) == {"id", "description"}, "invalid option fields")
        label = option["id"]
        require(isinstance(label, str) and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", label), "invalid option ID")
        require(label not in labels, "duplicate option ID")
        require(isinstance(option["description"], str) and option["description"].strip(), "empty description")
        labels.add(label)
    require(isinstance(rows, list) and rows, "dataset is empty")
    ids = set()
    groups = {}
    counts = Counter()
    cases = {split: set() for split in SPLITS}
    for row in rows:
        require(isinstance(row, dict), "invalid dataset row")
        for field in ("id", "thread_id", "source_id", "text", "label", "split"):
            require(isinstance(row.get(field), str) and row[field].strip(), f"missing/empty {field}")
        require(row["id"] not in ids, "duplicate sample ID")
        ids.add(row["id"])
        require(row["label"] in labels | {"__none__"}, "unknown ground-truth label")
        require(row["split"] in SPLITS, "invalid split")
        require(isinstance(row.get("cases"), list) and row["cases"] and
                all(isinstance(case, str) for case in row["cases"]), "invalid cases")
        require(set(row["cases"]) <= REQUIRED_CASES | {"multilingual"}, "unknown case tag")
        # Linking on either source OR thread prevents indirect cross-split leakage too.
        for key in (("source", row["source_id"]), ("thread", row["thread_id"]),
                    ("text", hashlib.sha256(row["text"].strip().encode()).hexdigest())):
            require(key not in groups or groups[key] == row["split"], "cross-split source/thread/text leakage")
            groups[key] = row["split"]
        counts[row["split"]] += 1
        cases[row["split"]].update(row["cases"])
    required = REQUIRED_CASES | ({"multilingual"} if multilingual else set())
    for split in SPLITS:
        require(counts[split] > 0, f"empty {split} split")
        require(required <= cases[split], f"missing case coverage in {split}: {sorted(required - cases[split])}")
        require({row["label"] for row in rows if row["split"] == split} == labels | {"__none__"},
                f"all labels including __none__ required in {split}")
    return dict(counts)


def hardware():
    result = {"os": platform.system(), "os_release": platform.release(),
              "architecture": platform.machine(), "python": platform.python_version()}
    if platform.system() == "Darwin":
        for key, name in (("cpu", "machdep.cpu.brand_string"), ("memory_bytes", "hw.memsize"), ("logical_cpus", "hw.ncpu")):
            value = subprocess.check_output(["sysctl", "-n", name], text=True).strip()
            result[key] = value if key == "cpu" else int(value)
    else:
        result["cpu"] = platform.processor() or "unknown"
        result["memory_bytes"] = None
        result["logical_cpus"] = __import__("os").cpu_count()
    return result


def prepare(taxonomy, rows, *, synthetic, target_confirmed, multilingual=False):
    counts = validate(taxonomy, rows, multilingual)
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "fixture_only" if synthetic else "prepared_pending_representativeness_review",
        "synthetic": synthetic,
        "hardware": hardware(),
        "hardware_role": "target" if target_confirmed else "development_host_unconfirmed",
        "taxonomy_sha256": digest(taxonomy),
        "dataset_sha256": digest(rows),
        "split_sha256": {split: digest([row for row in rows if row["split"] == split]) for split in SPLITS},
        "counts": counts,
        "label_counts": {split: dict(Counter(row["label"] for row in rows if row["split"] == split)) for split in SPLITS},
        "multilingual_in_scope": multilingual,
        "targets": TARGETS,
        "targets_sha256": digest(TARGETS),
        "quality_gates_passed": False,
        "held_out_runs": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--taxonomy", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True, help="JSONL with explicit tuning/held_out assignments")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-kind", choices=("synthetic", "representative"), required=True)
    parser.add_argument("--confirm-target-hardware", action="store_true")
    parser.add_argument("--multilingual", action="store_true")
    args = parser.parse_args()
    try:
        taxonomy = json.loads(args.taxonomy.read_text())
        rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
        manifest = prepare(taxonomy, rows, synthetic=args.data_kind == "synthetic",
                           target_confirmed=args.confirm_target_hardware, multilingual=args.multilingual)
        # Never overwrite an existing preregistration after seeing results.
        with args.output.open("x") as file:
            json.dump(manifest, file, indent=2, allow_nan=False)
            file.write("\n")
    except (ValueError, OSError) as error:
        # Avoid echoing email content from parsing exceptions.
        parser.exit(2, f"Preparation failed ({type(error).__name__}); check input schema and output path.\n")
    print(f"Prepared {sum(manifest['counts'].values())} samples; status={manifest['status']}")


if __name__ == "__main__":
    main()
