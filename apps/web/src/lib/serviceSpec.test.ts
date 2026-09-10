/**
 * 服务规格校验的前端判据必须与后端同源。这几条挂了 = 创建页 / 部署页会放过后端必拒的请求
 * (用户等到 4xx 才知道),或把合法输入拦在前端。
 */
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
  it("固定 tag 与 digest 算钉死;latest、无 tag、只有仓库端口都不算", () => {
    expect(isPinnedImageRef("registry.example.com/vllm/vllm-openai:v0.8.2")).toBe(true);
    expect(isPinnedImageRef("docker.io/library/nginx@sha256:abcdef")).toBe(true);
    expect(isPinnedImageRef("registry.example.com/vllm:latest")).toBe(false);
    expect(isPinnedImageRef("registry.example.com/vllm")).toBe(false);
    expect(isPinnedImageRef("registry:5000/img")).toBe(false);
    expect(isPinnedImageRef("registry:5000/img:1.0")).toBe(true);
  });
});

describe("envRowIssue", () => {
  it("形态错 → invalid;平台保留名段 → reserved;整表重名 → duplicate;空名不报", () => {
    expect(envRowIssue(row("1BAD"), [row("1BAD")])).toBe("invalid");
    expect(envRowIssue(row("JUPYTER_TOKEN"), [row("JUPYTER_TOKEN")])).toBe("reserved");
    expect(envRowIssue(row("AUTHORIZED_KEYS"), [row("AUTHORIZED_KEYS")])).toBe("reserved");
    const dup = [row("HF_TOKEN", "a"), row(" HF_TOKEN ", "b")];
    expect(envRowIssue(dup[0]!, dup)).toBe("duplicate");
    expect(envRowIssue(row(""), [row("")])).toBeNull();
    expect(envRowIssue(row("MODEL"), [row("MODEL")])).toBeNull();
  });
});

describe("parseEnvBulk", () => {
  it("按行解析 KEY=VALUE,值保留等号后原文;非法名、保留名、与已有或本批重名的行跳过并计数", () => {
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
  it("参数一行一个、空行忽略;命令按空白拆成 exec 形式,空串给空数组", () => {
    expect(parseArgBulk("--a\n\n  --b=1 \n").map((r) => r.value)).toEqual(["--a", "--b=1"]);
    expect(commandToList("  python  -m server ")).toEqual(["python", "-m", "server"]);
    expect(commandToList("   ")).toEqual([]);
  });
});

describe("buildRevisionEnv", () => {
  it("沿用的密文只进 keep;覆盖为新值进 env + secret_keys 且退出 keep;删行两边都不带", () => {
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
