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
      // 只生成裸 fetcher 与 model 类型;TanStack Query hooks 由两端在各自 api 层自建
      client: "fetch",
      clean: true,
      indexFiles: true,
      // 把 spec 的 header 参数(Idempotency-Key 等)生成进函数签名,免调用方手搓 options.headers
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
