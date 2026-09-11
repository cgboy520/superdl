import { defineConfig } from "orval";

export default defineConfig({
  superdl: {
    input: {
      target: "./openapi.json",
    },
    output: {
      mode: "tags-split",
      target: "src/generated/endpoints",
      schemas: "src/generated/model",
      // 只生成 fetcher 与 model 类型;hooks 两端自建
      client: "fetch",
      clean: true,
      indexFiles: true,
      // header 参数(Idempotency-Key 等)生成进函数签名
      headers: true,
      override: {
        mutator: {
          path: "src/mutator.ts",
          name: "customFetch",
        },
        fetch: {
          includeHttpResponseReturnType: false,
        },
      },
    },
  },
});
