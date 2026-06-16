"""Grounding & scope guardrails — catch 越界(out-of-scope) and 臆造(fabrication).

Unlike validate_output.py (output-only), these checks need the SOURCE CONTRACT
(and legal-basis.md) as grounding references. They are **复核 flags, not hard
fails** — deterministic checks have known false positives:
  - 引文 may be a faithful paraphrase rather than a verbatim quote
  - a cited law article may be correct but simply absent from legal-basis.md

What this CAN catch deterministically (high value, low FP):
  - 条款号越界：output cites 合同第N条 where N > the contract's clause count
  - 法条落地：output cites 民法典/司法解释 articles not backed by legal-basis.md
  - 引文落地：a 条款原文 quote that does not appear in the contract (when present)
  - 越界建议：advisory on litigation/tax/etc. beyond contract review
  - 代拟全文：a long run of sequential clause headers (drafting, not reviewing)

What it CANNOT catch (needs the LLM faithfulness judge in run_pipeline --with-llm):
  - semantic fabrication: a claim with no support in the contract that invents
    no clause number (e.g. asserting a party's qualification is dubious)

Usage:
    uv run python evals/validate_grounding.py <output.md> <contract.txt> [legal-basis.md]
"""

import re
import sys
from pathlib import Path

# 越界：advisory topics outside contract review
OUT_OF_SCOPE = ["诉讼策略", "起诉策略", "如何起诉", "上诉", "税务筹划", "节税",
                "税率", "刑事责任", "行政处罚", "商业计划", "融资建议", "投资建议"]

_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
           "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000}


def cjk_to_int(s: str) -> int:
    """Parse Arabic or CJK numerals (1–999). '十二'→12, '506'→506, '二十'→20."""
    if s.isdigit():
        return int(s)
    result, last = 0, 0
    for ch in s:
        if ch in _DIGITS:
            last = _DIGITS[ch]
        elif ch in _UNITS:
            result += (last or 1) * _UNITS[ch]
            last = 0
    return result + last


# ── clause / law reference extraction ──────────────────────────────────────

CLAUSE_RE = re.compile(r"第([0-9一二三四五六七八九十百千零]+)条")
# A law citation = explicit statute marker followed (within a short gap, e.g. a
# 法释〔...〕号 parenthetical) by 第X条. Precise on purpose — bare 第X条 stays a
# contract ref, so we don't misclassify 合同第二条 as a statute article.
LAW_REF_RE = re.compile(
    r"(?:《[^》]{0,28}(?:法|典|解释|规定|条例|办法)》|民法典|合同法|(?:买卖合同)?司法解释)"
    r"[^。；\n《》]{0,18}?第([0-9一二三四五六七八九十百千零]+)条")


def contract_clause_count(contract_text: str) -> int | None:
    """Max clause ordinal in the contract — supports '一、二、' headers and '第X条'."""
    nums = []
    for m in re.finditer(r"(?m)^\s*([一二三四五六七八九十百]+)、", contract_text):
        nums.append(cjk_to_int(m.group(1)))
    for m in CLAUSE_RE.finditer(contract_text):
        nums.append(cjk_to_int(m.group(1)))
    return max(nums) if nums else None


def law_refs_in(output: str) -> tuple[set, list]:
    """Statute articles cited with an explicit marker. Returns (numbers, spans-of-第X条)."""
    refs, spans = set(), []
    for m in LAW_REF_RE.finditer(output):
        refs.add(cjk_to_int(m.group(1)))
        spans.append(m.span(1))
    return refs, spans


def contract_refs_in(output: str, law_spans: list) -> set:
    """Bare 第X条 refs (not part of a law citation), value < 100 to exclude statute
    article numbers that lack a marker."""
    refs = set()
    for m in CLAUSE_RE.finditer(output):
        if any(ls <= m.start(1) <= le for ls, le in law_spans):
            continue
        n = cjk_to_int(m.group(1))
        if n < 100:
            refs.add(n)
    return refs


def legal_basis_articles(legal_basis_text: str) -> set:
    return {cjk_to_int(m.group(1)) for m in CLAUSE_RE.finditer(legal_basis_text)}


# ── quote grounding ─────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[\s，。、；：,.;:\"'“”‘’\-—_（）()【】\[\]]+", "", s)


def extract_quotes(output: str) -> list[str]:
    """Pull text under 条款原文 markers (markdown blockquotes that follow)."""
    quotes = []
    for m in re.finditer(r"条款原文[:：]?\s*\n((?:>.*\n?)+)", output):
        q = re.sub(r"^>\s?", "", m.group(1), flags=re.M).strip()
        if len(_norm(q)) >= 8:
            quotes.append(q)
    return quotes


# ── main validation ─────────────────────────────────────────────────────────

def validate_grounding(output: str, contract: str, legal_basis: str = "") -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    # 1. 条款号越界（range-based grounding）
    basis = legal_basis_articles(legal_basis) if legal_basis else set()
    cmax = contract_clause_count(contract)
    law_refs, law_spans = law_refs_in(output)
    contract_refs = contract_refs_in(output, law_spans)
    if cmax:
        over = sorted(n for n in contract_refs if n > cmax)
        results.append((
            "条款号落地",
            not over,
            f"合同共 {cmax} 条，越界引用: {['第%d条' % n for n in over]}" if over
            else f"合同 {cmax} 条，引用 {len(contract_refs)} 处均在范围内",
        ))
    else:
        results.append(("条款号落地", True, "合同无可识别条款编号，跳过"))

    # 2. 法条落地（cited laws backed by legal-basis.md）
    if legal_basis:
        ungrounded = sorted(n for n in law_refs if n not in basis)
        results.append((
            "法条落地",
            not ungrounded,
            f"引用了 legal-basis 之外的法条(复核：补充或核实): {['第%d条' % n for n in ungrounded]}"
            if ungrounded else f"{len(law_refs)} 处法条引用均在 legal-basis 内",
        ))
    else:
        results.append(("法条落地", True, "未提供 legal-basis，跳过"))

    # 3. 引文落地（quotes must appear in contract）— only if quotes exist
    quotes = extract_quotes(output)
    if quotes:
        cnorm = _norm(contract)
        missing = [q[:40] for q in quotes if _norm(q)[:30] not in cnorm]
        results.append((
            "引文落地",
            not missing,
            f"引文未在合同中找到(疑似篡改/编造): {missing}" if missing
            else f"{len(quotes)} 处引文均可在合同定位",
        ))
    else:
        results.append(("引文落地", True, "无条款原文引用块（总览阶段），跳过"))

    # 4. 越界建议
    hits = [k for k in OUT_OF_SCOPE if k in output]
    results.append((
        "输出范围(越界)",
        not hits,
        f"出现越界建议关键词(复核): {hits}" if hits else "无越界建议",
    ))

    # 5. 代拟全文检测（heuristic: many sequential clause headers）
    headers = re.findall(r"(?m)^\s*第[0-9一二三四五六七八九十百]+条[：:\s]", output)
    results.append((
        "未代拟全文",
        len(headers) < 8,
        f"出现 {len(headers)} 个条款标题，疑似代拟整份合同(复核)" if len(headers) >= 8
        else "无代拟整份合同迹象",
    ))

    return results


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    out_path, contract_path = Path(sys.argv[1]), Path(sys.argv[2])
    legal_path = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    for p in (out_path, contract_path):
        if not p.exists():
            print(f"File not found: {p}")
            sys.exit(1)

    legal_basis = legal_path.read_text(encoding="utf-8") if legal_path and legal_path.exists() else ""
    results = validate_grounding(out_path.read_text(encoding="utf-8"),
                                 contract_path.read_text(encoding="utf-8"), legal_basis)

    print(f"\nGrounding/Scope: {out_path.name}\n{'=' * 55}")
    all_clean = True
    for name, ok, detail in results:
        if not ok:
            all_clean = False
        print(f"  [{'OK ' if ok else '复核'}] {name}: {detail}")
    print(f"{'=' * 55}\n{'无复核项' if all_clean else '存在需复核项（非硬失败）'}\n")


if __name__ == "__main__":
    main()
