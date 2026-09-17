/** Shared polling periods in milliseconds. */
export const POLL = {
  /** Payment order waiting for the callback */
  payment: 2_000,
  /** Transitional states (creating / starting / stopping / releasing / deploying) polled lightly per row */
  transient: 5_000,
  /** Container log auto-refresh */
  logs: 10_000,
  /** Ticket conversation (in progress) */
  ticket: 15_000,
  /** Badges and steady-state details (unread count / alert count / running instance detail / market stock) */
  steady: 30_000,
  /** Batch metric summary (sparklines) */
  metrics: 45_000,
  /** Daily consumption / finance summaries */
  daily: 60_000,
  /** Public price board and price wall (anonymous, throttled to one minute) */
  publicBoard: 60_000,
} as const;
