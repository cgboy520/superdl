/** Node pool label → locale key (matches the backend pool_label; admin.json nodes.pool*). */
export const POOL_LABEL_KEY = {
  kata: "nodes.poolKata",
  hami: "nodes.poolHami",
  mig: "nodes.poolMig",
  cpu: "nodes.poolCpu",
} as const;

/** Union type of the registered pool labels. */
export type Pool = keyof typeof POOL_LABEL_KEY;

/** Pools switchable online (same set as the backend core/gpu_adapter.SWITCHABLE_POOLS); cpu is the physical property of GPU-less machines.
 *  Whether a model can enter mig / kata comes from the backend NodeOut.supports_mig / supports_passthrough; the frontend holds no family table. */
export const SWITCHABLE_POOLS = ["kata", "hami", "mig"] as const;
export type SwitchablePool = (typeof SWITCHABLE_POOLS)[number];
