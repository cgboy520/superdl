/** 将 TanStack Query 的全局默认错误类型设为 ApiError。 */

import type { ApiError } from "@superdl/api-client";

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: ApiError;
  }
}
