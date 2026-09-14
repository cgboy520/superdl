/** 节点池标识 → 文案键(与后端 pool_label 一致;admin.json nodes.pool*)。 */
export const POOL_LABEL_KEY = {
  kata: "nodes.poolKata",
  hami: "nodes.poolHami",
  mig: "nodes.poolMig",
  cpu: "nodes.poolCpu",
} as const;

/** 池标识联合;新增池只改 POOL_LABEL_KEY 一处。 */
export type Pool = keyof typeof POOL_LABEL_KEY;
