/** 节点池标识 → 文案键(集群页/添加节点/SKU 表单三处共用;取值与后端 pool_label 一致,值在 admin.json nodes.pool*)。 */
export const POOL_LABEL_KEY = {
  kata: "nodes.poolKata",
  hami: "nodes.poolHami",
  mig: "nodes.poolMig",
} as const;
