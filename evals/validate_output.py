"""Validate contract review output format against skill specification.

Usage:
    uv run python evals/validate_output.py <output_file>

Checks:
    1. Overview table exists with correct columns
    2. Table rows sorted by risk level (严重 > 重要 > 一般 > 提示)
    3. All 12 dimensions covered (even if no risk found)
    4. Each deep-dive finding has all 5 required fields
    5. No vague suggestions (banned phrases)
"""

import re
import sys
from pathlib import Path

RISK_ORDER = {"严重": 0, "重要": 1, "一般": 2, "提示": 3}
DIMENSIONS = [
    "主体信息",
    "标的物描述",
    "价款与支付",
    "交付",
    "验收",
    "风险转移",
    "违约责任",
    "赔偿上限",
    "知识产权与保密",
    "争议解决",
    "合同变更与解除",
    "生效条件",
]
REQUIRED_FINDING_FIELDS = ["条款原文", "风险等级", "法务分析", "业务影响", "修改建议"]
BANNED_VAGUE_PHRASES = [
    "建议完善",
    "建议进一步明确",
    "建议补充完善",
    "建议协商确定",
    "建议予以明确",
]


def validate(content: str) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    # 1. Overview table exists
    table_match = re.search(r"\|[#\s].*审查维度.*风险等级", content)
    results.append((
        "Overview table header",
        bool(table_match),
        "Found table with correct columns" if table_match else "No overview table found",
    ))

    # 2. Table rows sorted by risk level
    rows = re.findall(r"\|\s*\d+\s*\|([^|]+)\|([^|]+)\|", content)
    if rows:
        risk_levels = []
        for _, risk_cell in rows:
            for level in RISK_ORDER:
                if level in risk_cell:
                    risk_levels.append(RISK_ORDER[level])
                    break
        is_sorted = all(a <= b for a, b in zip(risk_levels, risk_levels[1:]))
        results.append((
            "Risk level sort order",
            is_sorted,
            f"Levels: {risk_levels}" if not is_sorted else "Correctly sorted",
        ))
    else:
        results.append(("Risk level sort order", False, "No table rows found"))

    # 3. All 12 dimensions covered
    found_dims = set()
    for dim in DIMENSIONS:
        if dim in content:
            found_dims.add(dim)
    missing = set(DIMENSIONS) - found_dims
    results.append((
        "12 dimensions coverage",
        len(missing) <= 2,
        f"Missing: {missing}" if missing else "All dimensions present",
    ))

    # 4. Each finding has all 5 required fields
    findings = re.split(r"\*\*条款原文\*\*", content)[1:]  # split on finding boundaries
    findings_with_issues = []
    for i, finding in enumerate(findings):
        missing_fields = [f for f in REQUIRED_FINDING_FIELDS if f not in finding]
        if missing_fields:
            findings_with_issues.append(f"Finding #{i+1}: missing {missing_fields}")
    results.append((
        "Finding field completeness",
        len(findings_with_issues) == 0,
        "; ".join(findings_with_issues) if findings_with_issues else f"All {len(findings)} findings complete",
    ))

    # 5. No vague suggestions
    vague_found = [p for p in BANNED_VAGUE_PHRASES if p in content]
    results.append((
        "No vague suggestions",
        len(vague_found) == 0,
        f"Found banned phrases: {vague_found}" if vague_found else "No vague suggestions",
    ))

    # 6. Disclaimer present (output guardrail)
    has_disclaimer = "不构成法律意见" in content or "咨询专业律师" in content
    results.append((
        "Disclaimer present",
        has_disclaimer,
        "Disclaimer found" if has_disclaimer else "Missing mandatory disclaimer (输出护栏)",
    ))

    return results


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    content = path.read_text(encoding="utf-8")
    results = validate(content)

    print(f"\nValidation: {path.name}\n{'='*50}")
    all_pass = True
    for name, passed, detail in results:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  [{status}] {name}: {detail}")

    print(f"\n{'='*50}")
    if all_pass:
        print("All checks passed.")
    else:
        print("Some checks FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    main()
