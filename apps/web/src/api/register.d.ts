/** TanStack Query 全局默认错误类型注册:useQuery/useMutation 的 error 一律是 ApiError,不再逐处手写泛型。 */

import type { ApiError } from "@superdl/api-client";

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: ApiError;
  }
}
