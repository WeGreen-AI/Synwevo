import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError } from "@synwevo/auth-client/api";
import { objectConversationPath, resolveObjectConversation } from "../src/lib/objects.ts";

test("the object route asks the server for the conversation at the contract's address, on either API prefix", () => {
  assert.equal(objectConversationPath("/api/messaging", "docweave", "project", "p 1/x"), "/api/messaging/objects/docweave/project/p%201%2Fx/conversation");
  assert.equal(objectConversationPath("/api/auth/messaging", "docweave", "job", "j1"), "/api/auth/messaging/objects/docweave/job/j1/conversation");
});

test("a conversation id opens it; a 404 is 'missing'; anything else is an error, including an answer without an id", async () => {
  const calls: string[] = [];
  const ok = await resolveObjectConversation(async (p) => { calls.push(p); return { id: "cv_9" }; }, "/api/messaging", "docweave", "project", "p1");
  assert.deepEqual(ok, { kind: "open", id: "cv_9" });
  assert.deepEqual(calls, ["/api/messaging/objects/docweave/project/p1/conversation"]);
  const missing = await resolveObjectConversation(async () => { throw new ApiError(404, "no_object", "找不到"); }, "/api/messaging", "docweave", "project", "p2");
  assert.deepEqual(missing, { kind: "missing" });
  const forbidden = await resolveObjectConversation(async () => { throw new ApiError(403, "forbidden", "no"); }, "/api/messaging", "docweave", "project", "p3");
  assert.equal(forbidden.kind, "error");
  assert.equal((forbidden as any).error.status, 403);
  const malformed = await resolveObjectConversation(async () => ({}), "/api/messaging", "docweave", "project", "p4");
  assert.equal(malformed.kind, "error");
  assert.equal((malformed as any).error.code, "invalid_response");
  const network = await resolveObjectConversation(async () => { throw new TypeError("offline"); }, "/api/messaging", "docweave", "project", "p5");
  assert.equal(network.kind, "error");
});
