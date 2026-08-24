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
      client: "react-query",
      httpClient: "fetch",
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
        // 不开 query.useMutation:否则无参 GET 会被生成为 useMutation 变体
        query: {
          useQuery: true,
          signal: true,
        },
      },
    },
  },
});
