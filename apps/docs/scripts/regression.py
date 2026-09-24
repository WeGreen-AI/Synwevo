"""Run Synwevo Docs regression cases (projects frozen with「保存为回归用例」).

    python scripts/regression.py            # run every case in regression/
    python scripts/regression.py --list
    python scripts/regression.py --case 20260923-101500-ab12cd

Exit code 1 when any case differs.  Cases live in regression/ (or $SYNWEVO_DOCS_REGRESSION_DIR)
and can be committed together with the code; the pytest suite runs them too.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


from synwevo_docs.regression import list_cases, run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", action="append", help="只运行指定用例（可重复）")
    ap.add_argument("--list", action="store_true", help="列出用例")
    a = ap.parse_args()
    cases = list_cases()
    if a.list:
        for c in cases:
            print(f"{c['id']}  {c['name']}  样本 {c['samples']}  来源 {len(c['sources'])}  模板 {c['template']}")
        return 0
    ids = a.case or [c["id"] for c in cases]
    if not ids:
        print("没有回归用例。在界面中打开项目，点击「保存为回归用例」即可创建。")
        return 0
    failed = 0
    for cid in ids:
        r = run(cid)
        mark = "通过" if r["ok"] else "失败"
        print(f"[{mark}] {r['name']} ({cid})  {r.get('passed', 0)}/{len(r['samples'])} 个样本一致  {r['seconds']} 秒")
        for e in r["errors"]:
            print(f"    ✖ {e}")
        for s in r["samples"]:
            if not s["ok"]:
                print(f"    ✖ {s['label']}")
                for d in s["diffs"]:
                    print("        " + d.replace("\n", "\n        "))
        failed += 0 if r["ok"] else 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
