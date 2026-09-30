import { test } from "node:test";
import assert from "node:assert/strict";
import { branchMessages, type Message } from "../src/apps/agent/types.ts";

const message = (id: string, parent_id: string | null) => ({ id, parent_id }) as Message;
test("restoring a branch includes its ancestors and excludes sibling answers and their effects", () => {
  const rows = [message("question", null), message("first-answer", "question"), message("regenerated", "question"), message("followup", "first-answer")];
  assert.deepEqual(branchMessages(rows, "regenerated").map((m) => m.id), ["question", "regenerated"]);
  assert.deepEqual(branchMessages(rows, "followup").map((m) => m.id), ["question", "first-answer", "followup"]);
});
test("missing ancestors and damaged cycles do not trap the conversation renderer", () => {
  assert.deepEqual(branchMessages([message("answer", "missing")], "answer").map((m) => m.id), ["answer"]);
  assert.equal(branchMessages([message("a", "b"), message("b", "a")], "a").length, 2);
  assert.deepEqual(branchMessages([], null), []);
});
