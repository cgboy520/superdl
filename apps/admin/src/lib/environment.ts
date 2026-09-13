/** 环境徽标的数据源。规格要的是运行时 environment,但后端 OpenAPI 眼下没有任何端点暴露它
 *  (/api/v1/site-config 的 SiteConfigOut 与 /api/admin/v1/me 的 AdminOut 都没有该字段),
 *  所以暂取构建模式。后端补上字段后,只改本文件换成读接口。 */

export type AdminEnvironment = "prod" | "nonprod";

export function useEnvironment(): AdminEnvironment {
  return import.meta.env.MODE === "production" ? "prod" : "nonprod";
}
