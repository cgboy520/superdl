/**
 * 公开层(主页 / 登录 / 页脚)展示文案。全部 SuperDL 原创措辞,禁止照抄竞品(ui-ux-spec 红线)。
 * 结构性文案在此集中;运营/合规/交互文案仍在 copy.ts。
 */

export const marketing = {
  hero: {
    title: "SuperDL GPU 算力云",
    subtitle: "即开即用 · 按量计费 · 数据无忧",
    ctaPrimary: "免费注册",
    ctaSecondary: "查看算力市场",
  },
  quickEntries: [
    {
      key: "start",
      title: "快速开始",
      desc: "注册后一分钟开出第一台 GPU 实例",
    },
    {
      key: "gpu",
      title: "GPU 选型",
      desc: "按算力与显存横向对比,选对卡不花冤枉钱",
    },
    {
      key: "billing",
      title: "透明计费",
      desc: "按量计费,开机费用与日常费用分栏摊开",
    },
    {
      key: "data",
      title: "数据无忧",
      desc: "数据盘独立于实例,释放实例不丢数据",
    },
  ],
  pricing: {
    title: "GPU 租用价格",
    subtitle: "价格即库存:按钮上的可租卡数与控制台实时一致",
    tabDedicated: "整卡独享",
    tabShared: "切分与共享",
    fallbackCta: "前往算力市场",
    moreLink: "查看全部规格与筛选",
  },
  ranking: {
    title: "GPU 算力排名",
    subtitle: "横向对比常见加速卡的理论峰值算力",
    tabFp16: "半精 FP16",
    tabFp32: "单精 FP32",
    onSaleTag: "在售",
    footnote: "理论峰值算力,数据来自各厂商公开规格,实际性能因负载而异。",
  },
  ctaBanner: {
    /** n 为实时空闲卡数;n 为空或 0 时用 fallback */
    withStock: (n: number) => `当前 ${n} 卡空闲可租,注册即开`,
    fallback: "弹性 GPU 算力,注册即开",
    button: "免费注册",
  },
  loginBullets: [
    "秒级开机 · 按量计费",
    "关机不删数据 · 数据盘独立保留",
    "价格透明 · 计费依据可自查",
  ],
  loginSlogan: "GPU 算力,即开即用",
  footer: {
    product: {
      title: "产品",
      links: [
        { label: "算力市场", to: "/market" },
        { label: "GPU 价格", to: "/#pricing" },
        { label: "算力排名", to: "/#ranking" },
      ],
    },
    support: {
      title: "支持",
      links: [
        { label: "帮助文档(即将上线)", to: "" },
        { label: "服务状态(即将上线)", to: "" },
      ],
    },
    compliance: {
      title: "合规",
      links: [
        { label: "用户协议", to: "/legal/terms" },
        { label: "隐私政策", to: "/legal/privacy" },
      ],
    },
    copyright: "© 2026 SuperDL",
  },
} as const;
