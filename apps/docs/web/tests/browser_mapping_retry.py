"""Real Chrome mapping replacement and retry requests against _testenv.

Retry input is explicitly a SYNTHETIC MIXED-STATUS FIXTURE: after a real 15-file batch,
three isolated result rows are marked failed/cancelled/skipped. Rendering is not mocked;
the browser's retry reruns the actual engine and the remaining rows must stay untouched.
"""
import argparse
import json
import re
from pathlib import Path

from browser_review import load
from playwright.sync_api import expect, sync_playwright
from synwevo_core.store import Store


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=Path, required=True)
    evidence = ap.parse_args().evidence
    evidence.mkdir(parents=True, exist_ok=True)
    env = load("_testenv")
    load("ui_e2e")
    more = load("ui_e2e_more")
    more.SHOTS = evidence
    checks = []

    def done(name):
        checks.append(name)
        (evidence / "checks.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2))
        print("PASS " + name, flush=True)

    with env.isolated(keep=True) as instance, sync_playwright() as pw:
        assert env.health(instance.base)["instance"] == "test"
        more.BASE = instance.base
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN", accept_downloads=True)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            pid = more.flow_a(page)
            jid = page.url.rsplit("/", 1)[-1]
            done("real grouped 15-XLSX batch and downloaded workbook read-back")
            # This mutates only the test-marked throwaway store, never an existing service.
            store = Store(instance.data_dir)
            assert store.instance_kind() == "test"
            rows = store.all("SELECT id,status,attempts FROM job_items WHERE job_id=? ORDER BY seq", (jid,))
            assert len(rows) == 15 and all(r["status"] == "success" for r in rows)
            with store.tx() as tx:
                for row, status in zip(rows, ["failed", "cancelled", "skipped"]):
                    tx.execute("UPDATE job_items SET status=?,issues=? WHERE id=?", (status, json.dumps([{"level": "error", "message": "Synthetic mixed-status retry fixture"}]), row["id"]))
                    tx.execute("DELETE FROM artifacts WHERE item_id=?", (row["id"],))
                tx.execute("UPDATE jobs SET status='completed_with_errors',counts=? WHERE id=?", (json.dumps({"total": 15, "planned": 15, "success": 12, "failed": 1, "cancelled": 1, "skipped": 1}), jid))
            (evidence / "synthetic-retry-fixture.json").write_text(json.dumps({"kind": "synthetic mixed-status fixture after real generation", "data_dir": str(instance.data_dir), "job": jid, "before": rows}, indent=2))
            page.reload()
            expect(page.get_by_role("button", name="仅重试失败项")).to_be_visible()
            page.screenshot(path=str(evidence / "mixed-status-before-retry.png"))
            with page.expect_response(lambda r: r.url.endswith(f"/api/jobs/{jid}/retry") and r.request.method == "POST") as response:
                page.get_by_role("button", name="仅重试失败项").click()
            assert response.value.status == 200, response.value.text()
            body = response.value.request.post_data_json
            assert body["failed_only"] is True
            expect(page.get_by_role("button", name="仅重试失败项")).to_have_count(0, timeout=30000)
            expect(page.get_by_role("button", name="继续未完成项")).to_be_visible(timeout=30000)
            after = store.all("SELECT id,status,attempts FROM job_items WHERE job_id=? ORDER BY seq", (jid,))
            assert after[0]["status"] == "success" and after[0]["attempts"] == rows[0]["attempts"] + 1
            assert after[1]["status"] == "cancelled" and after[1]["attempts"] == rows[1]["attempts"]
            assert after[2]["status"] == "skipped" and after[2]["attempts"] == rows[2]["attempts"]
            assert after[3:] == rows[3:]
            (evidence / "retry-request-and-result.json").write_text(json.dumps({"request": body, "status": response.value.status, "after": after}, indent=2))
            page.screenshot(path=str(evidence / "failed-only-result.png"))
            done("failed_only:true real browser request rerenders exactly failed fixture row; cancelled/skipped/success untouched")

            page.goto(f"{instance.base}/p/{pid}")
            expect(page.locator(".pane.right .gmark .tag")).to_have_count(2)
            page.locator(".pane.right .gmark .tag").nth(1).click()
            first = more.cell_box(page, ".pane.left", "订单号")
            last = more.cell_box(page, ".pane.left", "单价")
            more.drag(page, first, {**last, "y": last["y"] + 22 * 5})
            page.get_by_role("button", name="⇄ 建立映射").click()
            expect(page.get_by_role("dialog")).to_contain_text("将替换")
            page.get_by_role("dialog").get_by_role("button", name="保存映射").click()
            expect(page.get_by_role("dialog")).to_have_count(0)
            project = page.request.get(f"{instance.base}/api/projects/{pid}").json()
            mappings = project["mappings"]["mappings"]
            assert len(mappings) == 2
            assert len({m["target"]["binding"] for m in mappings}) == 2
            page.screenshot(path=str(evidence / "repeat-replacement.png"))
            done("reselecting detail source replaces its existing mapping, no duplicate target")

            page.get_by_role("button", name="👁 预览").click()
            expect(page.locator(".pane.right .gcell").first).to_be_visible(timeout=15000)
            page.locator(".pane.right .gcell").first.click()
            expect(page.locator(".pane.right .gsel")).to_have_count(0)
            page.get_by_role("button", name="返回模板").click()
            page.locator(".pane.right .gcell", has_text=re.compile("^订单确认单（测试）$")).click()
            expect(page.get_by_role("button", name="✎ 设为待填区域")).to_be_visible()
            page.locator(".topbar button.desktop-only").first.click()
            page.locator(".modal input[type=file]").set_input_files(str(more.DATA / "测试_合同模板.docx"))
            expect(page.locator(".pane.right p.doc-p").first).to_be_visible(timeout=15000)
            expect(page.get_by_role("button", name="✎ 设为待填区域")).to_have_count(0)
            done("grid preview does not select; replacing XLSX template with DOCX clears old selection")
            assert not errors, errors
        except Exception:
            page.screenshot(path=str(evidence / "failure.png"))
            (evidence / "failure-dom.txt").write_text(page.locator("body").inner_text())
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    main()
