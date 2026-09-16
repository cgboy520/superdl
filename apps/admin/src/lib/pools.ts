/** 节点池标识 → 文案键(与后端 pool_label 一致;admin.json nodes.pool*)。 */
export const POOL_LABEL_KEY = {
  kata: "nodes.poolKata",
  hami: "nodes.poolHami",
  mig: "nodes.poolMig",
  cpu: "nodes.poolCpu",
} as const;

/** 已登记池标识的联合类型。 */
export type Pool = keyof typeof POOL_LABEL_KEY;

/** 可在线互切的池(与后端 core/gpu_adapter.SWITCHABLE_POOLS 同口径);cpu 是无卡机的物理属性。
 *  机型能否进 mig / kata 由后端 NodeOut.supports_mig / supports_passthrough 给出,前端不再持有家族表。 */
export const SWITCHABLE_POOLS = ["kata", "hami", "mig"] as const;
export type SwitchablePool = (typeof SWITCHABLE_POOLS)[number];
