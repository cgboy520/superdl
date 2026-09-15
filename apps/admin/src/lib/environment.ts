/** 环境徽标使用 Vite 构建模式。 */

export type AdminEnvironment = "prod" | "nonprod";

export function useEnvironment(): AdminEnvironment {
  return import.meta.env.MODE === "production" ? "prod" : "nonprod";
}
