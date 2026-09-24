"""Run Synwevo Docs regression cases (projects frozen with「保存为回归用例」).

    .venv/bin/python apps/docs/scripts/regression.py            # run every case in regression/
    .venv/bin/python apps/docs/scripts/regression.py --list
    .venv/bin/python apps/docs/scripts/regression.py --case 20260923-101500-ab12cd

Exit code 1 when any case differs.  Two kinds of case are run: the committed synthetic ones in
apps/docs/regression/, and the ones captured in the product, which live per workspace under
<data>/regression/<workspace>/ (or $SYNWEVO_DOCS_REGRESSION_DIR). This is the operator's view on
the host, which can read the data directory anyway, so it runs every workspace's cases; the HTTP
API only ever shows a workspace its own.
"""
from __future__ import annotations

import argparse
import sys

from synwevo_docs.regression import RegressionError, list_all, run_any  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", action="append", help="只运行指定用例（可重复）")
    ap.add_argument("--list", action="store_true", help="列出用例")
    ap.add_argument("--data", help="数据目录（默认 $SYNWEVO_DATA_DIR 或仓库根目录 data/）；只读")
    a = ap.parse_args()
    # Case ids are generated per capture and do not repeat across workspaces in practice; one that
    # did would run the first match, which run_any documents.
    cases = list_all(a.data)
    if a.list:
        for c in cases:
            print(f"{c['id']}  [{c['scope']}]  {c['name']}  样本 {c['samples']}  来源 {len(c['sources'])}  模板 {c['template']}")
        return 0
    ids = a.case or [c["id"] for c in cases]
    if not ids:
        print("没有回归用例。在界面中打开项目，点击「保存为回归用例」即可创建。")
        return 0
    failed = 0
    for cid in ids:
        try:
            r = run_any(cid, a.data)
        except RegressionError as e:
            print(f"[失败] {cid}  {e}")
            failed += 1
            continue
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
