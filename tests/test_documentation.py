import json
import re
from pathlib import Path

from tagger.app import create_app
from tagger.errors import ERRORS

ROOT = Path(__file__).resolve().parents[1]


def test_plan_error_table_matches_public_contract():
    plan = (ROOT / "docs/plan/v_1/PLAN.md").read_text()
    rows = re.findall(r"^\| .*? \| (\d{3}) \| `([a-z_]+)` \| (true|false) \|$", plan, re.M)
    assert {code: (int(status), retryable == "true") for status, code, retryable in rows} == {
        code: (status, retryable) for code, (status, retryable, _) in ERRORS.items()
    }


def test_generated_openapi_is_current(settings):
    expected = create_app(settings).openapi()
    assert json.loads((ROOT / "docs/openapi.json").read_text()) == expected


def test_documentation_relative_links_and_json_examples():
    paths = [
        ROOT / "README.md",
        ROOT / "docs/usage.md",
        ROOT / "docs/backends.md",
        ROOT / "docs/validation.md",
        ROOT / "docs/release-readiness.md",
        ROOT / "docs/plan/v_1/history/PLAN_v6.md",
        ROOT / "docs/plan/v_1/PLAN.md",
        ROOT / "docs/plan/v_1/PHASE_1.md",
    ]
    for path in paths:
        text = path.read_text()
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if not target.startswith(("https://", "http://", "#")):
                assert (path.parent / target.split("#", 1)[0]).exists(), (path, target)
        for example in re.findall(r"```json\n(.*?)\n```", text, re.S):
            json.loads(example)
