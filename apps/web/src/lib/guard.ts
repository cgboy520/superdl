import { redirect, type ParsedLocation } from "@tanstack/react-router";

import { authStore } from "../stores/auth";

/** Protected route beforeLoad: signed out → the login page with the return address (allow-listed on the login side). */
export function requireAuth({ location }: { location: ParsedLocation }) {
  if (!authStore.getState().accessToken) {
    throw redirect({ to: "/login", search: { redirect: location.href } });
  }
}
