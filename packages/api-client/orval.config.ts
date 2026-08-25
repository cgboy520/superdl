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
      // 只生成裸 fetcher 与 model 类型,不生成 TanStack Query hooks:
      // 两端在各自的 api 层(apps/web/src/api/*.ts、apps/admin/src/api.ts)用 useQuery/useMutation 包 fetcher。
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
