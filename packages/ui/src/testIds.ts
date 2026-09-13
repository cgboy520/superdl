/** 全仓允许的 testid 白名单(只此两处)。
 *
 * 口径:e2e 一律用 role + 可见文案定位,唯独这两个值本身没有可定位语义 —— 一串 MFA 密钥、一条服务 URL,
 * 同页往往还有别的 <code>(curl 示例 / SSH 命令),靠标签选择器会抢错元素。新增 testid 前先想清楚为什么 role + 文案不行。
 * e2e 侧按字面量用(`getByTestId("mfa-secret")`),两边改名要同一提交,见 docs/reference/i18n.md。
 */
export const TEST_IDS = {
  /** 管理端首登强制绑定 MFA 页的「手动录入密钥」 */
  mfaSecret: "mfa-secret",
  /** 在线服务详情端点卡的完整访问地址 */
  endpointUrl: "endpoint-url",
} as const;
