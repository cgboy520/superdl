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
      client: "fetch",
      clean: true,
      indexFiles: true,
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
