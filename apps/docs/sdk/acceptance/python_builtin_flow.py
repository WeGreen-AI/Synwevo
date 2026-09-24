"""Synthetic editor-model acceptance via the installed SDK/CLI and real business APIs.

No server imports: the parent independently reads DOCX/XLSX results using the shared kit.
"""
import copy
import json

from synwevo_docs_sdk import ConflictError


def run(c, cli, kit, out, definition, record):
    results = []
    names = [m["field"] for m in definition["mappings"]]
    for channel in ("sdk", "cli"):
        use_cli = channel == "cli"
        for kind, format_ in (("builtin_doc", "docx"), ("builtin_sheet", "xlsx")):
            label = channel + "-" + kind
            folder = out / label
            folder.mkdir()
            if kind == "builtin_doc":
                editor = {"body": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [
                        {"type": "text", "text": name + ": "},
                        {"type": "field", "attrs": {"id": f"field_{i}", "name": name, "required": True}}
                    ]} for i, name in enumerate(names)]}}
            else:
                editor = {"sheets": [{"name": "合成结果", "cells": {
                    f"A{i + 1}": {"v": name} for i, name in enumerate(names)}, "fields": [
                    {"id": f"field_{i}", "kind": "cell", "name": name, "ref": f"B{i + 1}", "required": True}
                    for i, name in enumerate(names)]}]}
            input_file = folder / "editor.json"
            input_file.write_text(json.dumps(editor, ensure_ascii=False))
            create_key, save_key = label + "-create", label + "-save"
            # CLI creation reads raw JSON on stdin; saving below imports an export envelope.
            def create():
                if use_cli:
                    return cli("templates", "builtin", "create", kind, "-", "--name", label,
                               "--idempotency-key", create_key, stdin=json.dumps(editor))
                return c.create_builtin_template(kind, editor, label, idempotency_key=create_key)
            template = record(label + "_create", create())
            replay = create()
            assert replay["id"] == template["id"] and replay["head"] == 1
            if not use_cli:
                assert replay.replayed
            tid = template["id"]
            exported = (cli("templates", "builtin", "export", tid) if use_cli else c.get_builtin_template(tid))
            assert exported["editor"] == editor and exported["version"] == 1
            assert {f["name"] for f in exported["fields"]} == set(names)
            (folder / "export-v1.json").write_text(json.dumps(exported, ensure_ascii=False, indent=2))
            edited = copy.deepcopy(editor)
            marker = "SYNTHETIC revision 2"
            if kind == "builtin_doc":
                edited["body"]["content"].append({"type": "paragraph", "content": [{"type": "text", "text": marker}]})
            else:
                edited["sheets"][0]["cells"]["A7"] = {"v": marker}
            envelope = {**exported, "editor": edited}
            edited_file = folder / "edited-export.json"
            edited_file.write_text(json.dumps(envelope, ensure_ascii=False, indent=2))
            def save():
                if use_cli:
                    return cli("templates", "builtin", "save", tid, edited_file, "--base-version", 1,
                               "--name", label + " edited", "--idempotency-key", save_key)
                return c.save_builtin_template(tid, edited, base_version=1, name=label + " edited", idempotency_key=save_key)
            saved = record(label + "_save", save())
            replay = save()
            assert saved["head"] == replay["head"] == 2
            if not use_cli:
                assert replay.replayed
            if use_cli:
                error = cli("templates", "builtin", "save", tid, edited_file, "--base-version", 1, code=3)
                assert error["error"]["code"] == "stale_template"
                historical = cli("templates", "builtin", "export", tid, "--version", 1)
                current = cli("templates", "builtin", "export", tid)
            else:
                try:
                    c.save_builtin_template(tid, edited, base_version=1)
                except ConflictError as exc:
                    assert exc.code == "stale_template", exc
                    record(label + "_stale", exc.to_dict())
                else:
                    raise AssertionError("Stale editor update accepted")
                historical, current = c.get_builtin_template(tid, version=1), c.get_builtin_template(tid)
            assert historical["editor"] == editor and historical["version"] == 1
            assert current["editor"] == edited and current["version"] == current["head"] == 2
            assert current["name"] == label + " edited"
            (folder / "export-v2.json").write_text(json.dumps(current, ensure_ascii=False, indent=2))
            # Export envelopes can also be imported as a NEW template; IDs/head are ignored.
            imported = (cli("templates", "builtin", "create", kind, folder / "export-v2.json") if use_cli
                        else c.create_builtin_template(kind, current["editor"]))
            assert imported["id"] != tid and imported["head"] == 1
            assert c.get_builtin_template(imported["id"])["editor"] == edited
            if use_cli:
                project = cli("projects", "create", label)
                pid = project["id"]
                cli("files", "upload", pid, kit / "customers.xlsx")
                cli("projects", "template", pid, tid, "--version", 2)
                cli("rule", "put", pid, kit / "project.json")
                cli("mappings", "put", pid, kit / "project.json")
                cli("definition", "put", pid, kit / "project.json")
                pre = cli("preflight", pid, "--formats", format_)
                preview = cli("preview", pid, folder / ("preview." + format_), "--index", 0, "--signature", pre["signature"])
                job = cli("jobs", "submit", pid, "--formats", format_)
                done = cli("jobs", "wait", job["id"], "--wait-timeout", 120, "--poll-interval", .1)
                downloads = cli("artifacts", "download-all", job["id"], folder / "artifacts")
            else:
                project = c.create_project(label)
                pid = project["id"]
                c.upload_source(pid, kit / "customers.xlsx")
                c.set_project_template(pid, tid, version=2)
                c.put_rule(pid, definition["rule"])
                c.put_mappings(pid, definition["mappings"])
                c.put_definition(pid, definition)
                pre = c.preflight(pid, formats=[format_])
                preview = c.download_preview(pid, folder / ("preview." + format_), index=0, signature=pre["signature"])
                job = c.create_job(pid, formats=[format_])
                done = c.wait_for_job(job["id"], timeout=120, poll_interval=.1)
                downloads = c.download_artifacts(job["id"], folder / "artifacts")
            assert (pre["records"], pre["valid"], pre["invalid"]) == (12, 12, 0)
            assert preview["verified"] == ["X-Content-SHA256"] and preview["template_version"] == "2"
            assert preview["project_signature"] == pre["signature"]
            assert done["status"] == "completed" and done["counts"]["documents"] == 12
            assert len(downloads) == 12 and all(len(a["verified"]) == 2 for a in downloads)
            results.append(record(label + "_chain", {"client": channel, "kind": kind, "format": format_,
                "template_id": tid, "project_id": pid, "job_id": job["id"], "preview": preview,
                "artifacts": downloads, "marker": marker, "status": done["status"]}))
    (out / "builtins.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results
