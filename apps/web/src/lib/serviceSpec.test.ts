/** Service spec input validation and submit payload conversion. */
import { describe, expect, it } from "vitest";

import {
  buildRevisionEnv,
  commandToList,
  envRowIssue,
  isPinnedImageRef,
  parseArgBulk,
  parseEnvBulk,
  type EnvRow,
} from "./serviceSpec";

const row = (name: string, id = name): EnvRow => ({ id, name, value: "", secret: false });

describe("isPinnedImageRef", () => {
  it("a fixed tag or digest counts as pinned; latest, no tag and a bare registry port do not", () => {
    expect(isPinnedImageRef("registry.example.com/vllm/vllm-openai:v0.8.2")).toBe(true);
    expect(isPinnedImageRef("docker.io/library/nginx@sha256:abcdef")).toBe(true);
    expect(isPinnedImageRef("registry.example.com/vllm:latest")).toBe(false);
    expect(isPinnedImageRef("registry.example.com/vllm")).toBe(false);
    expect(isPinnedImageRef("registry:5000/img")).toBe(false);
    expect(isPinnedImageRef("registry:5000/img:1.0")).toBe(true);
  });
});

describe("envRowIssue", () => {
  it("bad shape → invalid; platform-reserved names → reserved; duplicate in the table → duplicate; empty names do not report", () => {
    expect(envRowIssue(row("1BAD"), [row("1BAD")])).toBe("invalid");
    expect(envRowIssue(row("JUPYTER_TOKEN"), [row("JUPYTER_TOKEN")])).toBe("reserved");
    expect(envRowIssue(row("AUTHORIZED_KEYS"), [row("AUTHORIZED_KEYS")])).toBe("reserved");
    const dupA = row("HF_TOKEN", "a");
    expect(envRowIssue(dupA, [dupA, row(" HF_TOKEN ", "b")])).toBe("duplicate");
    expect(envRowIssue(row(""), [row("")])).toBeNull();
    expect(envRowIssue(row("MODEL"), [row("MODEL")])).toBeNull();
  });
});

describe("parseEnvBulk", () => {
  it("parses KEY=VALUE per line keeping the text after the equals sign; invalid, reserved and duplicate (existing or same batch) rows are skipped and counted", () => {
    const { rows, skipped } = parseEnvBulk(
      ["A=1", "B=x=y", "", "  C  ", "1BAD=1", "SUPERDL_X=1", "A=2", "EXISTS=1"].join("\n"),
      ["EXISTS"],
    );
    expect(rows.map((r) => [r.name, r.value])).toEqual([
      ["A", "1"],
      ["B", "x=y"],
      ["C", ""],
    ]);
    expect(skipped).toBe(4);
    expect(rows.every((r) => !r.secret)).toBe(true);
  });
});

describe("parseArgBulk / commandToList", () => {
  it("one argument per line, blank lines ignored; the command splits on whitespace into exec form, empty string → empty array", () => {
    expect(parseArgBulk("--a\n\n  --b=1 \n").map((r) => r.value)).toEqual(["--a", "--b=1"]);
    expect(commandToList("  python  -m server ")).toEqual(["python", "-m", "server"]);
    expect(commandToList("   ")).toEqual([]);
  });
});

describe("buildRevisionEnv", () => {
  it("kept secrets go into keep only; overridden values go into env + secret_keys and leave keep; deleted rows appear in neither", () => {
    const rows: EnvRow[] = [
      { id: "1", name: "MODEL", value: "qwen", secret: false },
      { id: "2", name: "HF_TOKEN", value: "hf_new", secret: true },
      { id: "3", name: " ", value: "ignored", secret: false },
    ];
    expect(buildRevisionEnv(rows, ["HF_TOKEN", "API_SECRET"])).toEqual({
      env: { MODEL: "qwen", HF_TOKEN: "hf_new" },
      env_secret_keys: ["HF_TOKEN"],
      env_secret_keep: ["API_SECRET"],
    });
    expect(buildRevisionEnv([], [])).toEqual({ env: null, env_secret_keys: null, env_secret_keep: [] });
  });
});
