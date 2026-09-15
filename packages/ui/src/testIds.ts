/** MFA 手动密钥与服务端点的 testid 白名单。 */
export const TEST_IDS = {
  /** 管理端首登强制绑定 MFA 页的「手动录入密钥」 */
  mfaSecret: "mfa-secret",
  /** 在线服务详情端点卡的完整访问地址 */
  endpointUrl: "endpoint-url",
} as const;
