/** The environment badge uses the Vite build mode. */

export type AdminEnvironment = "prod" | "nonprod";

export function useEnvironment(): AdminEnvironment {
  return import.meta.env.MODE === "production" ? "prod" : "nonprod";
}
