/** Set TanStack Query's global default error type to ApiError. */

import type { ApiError } from "@superdl/api-client";

declare module "@tanstack/react-query" {
  interface Register {
    defaultError: ApiError;
  }
}
