import assert from "node:assert/strict";
import test from "node:test";

import { sourceLabel } from "../lib/presentation.ts";
import { isCursorTrace, isExecutingRun, isIdleOpenRun } from "../lib/types.ts";

test("Codex surfaces have distinct labels and legacy cli stays Codex CLI", () => {
  assert.equal(sourceLabel("codex-cli"), "Codex CLI");
  assert.equal(sourceLabel("codex-exec"), "Codex exec");
  assert.equal(sourceLabel("codex-ide"), "Codex IDE");
  assert.equal(sourceLabel("codex"), "Codex");
  assert.equal(sourceLabel("cli"), "Codex CLI");
  assert.equal(sourceLabel("cursor"), "Cursor");
  assert.equal(sourceLabel(null, "codex"), "Codex");
  assert.equal(sourceLabel(undefined, undefined), "Source not reported");
  assert.equal(sourceLabel("something-new"), "something-new");
});

test("Codex IDE runs are never classified as Cursor-native Agent runs", () => {
  const ide = {
    source: "codex-ide",
    agent_framework: "codex",
    attributes: { "nm5.source": "codex-ide", "nm5.agent": "codex", "agent.framework": "codex" },
  };
  assert.equal(isCursorTrace(ide), false);
});

test("dashboard buckets: idle codex-ide session and completed codex-exec", () => {
  const idle = { lifecycle: "running", status: "unset", execution_state: "idle", source: "codex-ide", agent_framework: "codex" };
  assert.equal(isIdleOpenRun(idle), true);
  assert.equal(isExecutingRun(idle), false);
  const done = { lifecycle: "completed", status: "ok", execution_state: "idle", source: "codex-exec", agent_framework: "codex" };
  assert.equal(isIdleOpenRun(done), false);
  assert.equal(isExecutingRun(done), false);
});
