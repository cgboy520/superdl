/** InstanceActions / ReleaseModal:状态驱动的禁用态、关机确认弹窗、释放多级防护。写操作 hooks 全 mock。 */
import type { InstanceOut } from "@superdl/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InstanceActions, ReleaseModal } from "./InstanceActions";

const {
  startMutate,
  stopMutateAsync,
  restartMutateAsync,
  releaseMutate,
  autoRenewMutate,
  toOnDemandMutateAsync,
} = vi.hoisted(() => ({
  startMutate: vi.fn(),
  stopMutateAsync: vi.fn().mockResolvedValue(undefined),
  restartMutateAsync: vi.fn().mockResolvedValue(undefined),
  releaseMutate: vi.fn(),
  autoRenewMutate: vi.fn(),
  toOnDemandMutateAsync: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("../api/mutations", () => ({
  useStartInstance: () => ({ mutate: startMutate, isPending: false }),
  useStopInstance: () => ({ mutateAsync: stopMutateAsync, isPending: false }),
  useRestartInstance: () => ({ mutateAsync: restartMutateAsync, isPending: false }),
  useReleaseInstance: () => ({ mutate: releaseMutate, isPending: false }),
  useSetAutoRenew: () => ({ mutate: autoRenewMutate, isPending: false }),
  useConvertToOnDemand: () => ({ mutateAsync: toOnDemandMutateAsync, isPending: false }),
  // RenewModal 的两个提交 hook 也要有桩
  useRenewInstance: () => ({ mutate: vi.fn(), isPending: false }),
  useSubscribeInstance: () => ({ mutate: vi.fn(), isPending: false }),
}));

// 无 Router 上下文:Link 降级为原生 <a>,navigate 为空实现
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
  useNavigate: () => vi.fn(),
}));

// RenewModal 会拉钱包与策略,给最小数据;ConnectMenu 打开后拉 access
vi.mock("../api/queries", () => ({
  useInstanceAccess: () => ({
    data: { ssh_command: "ssh -p 30022 root@gpu.example.com", jupyter_url: "https://j.example.com" },
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
  useWallet: () => ({ data: { balance: "3000.00" } }),
  usePolicies: () => ({
    data: {
      period_discount_day: 95,
      period_discount_week: 90,
      period_discount_month: 80,
      period_discount_year: 70,
      period_expire_warn_days: 3,
      spot_discount_pct: 40,
      spot_grace_seconds: 60,
    },
  }),
}));

function makeInstance(status: string): InstanceOut {
  return {
    uuid: "u-1",
    name: "demo-vm",
    status,
    market: "on_demand",
    gpu_count: 1,
    price_hourly: "3.9900",
  } as InstanceOut;
}

/** 竞价实例:price_hourly 已是折后价 */
function makeSpot(status: string): InstanceOut {
  return {
    uuid: "u-3",
    name: "cheap-vm",
    status,
    market: "spot",
    gpu_count: 1,
    price_hourly: "1.5960",
  } as InstanceOut;
}

/** 包周期实例:market 与 subscription 一起给,缺一不判包周期 */
function makeSubscription(
  status: string,
  sub: { expiresAt: string; subStatus?: string; autoRenew?: boolean },
): InstanceOut {
  return {
    uuid: "u-2",
    name: "my-vllm",
    status,
    market: "subscription",
    gpu_count: 1,
    price_hourly: "3.1920",
    subscription: {
      period: "month",
      period_count: 1,
      started_at: "2026-08-04T04:00:00Z",
      expires_at: sub.expiresAt,
      status: sub.subStatus ?? "active",
      auto_renew: sub.autoRenew ?? false,
      amount_paid: "2298.24",
      unit_price: "3.9900",
    },
  } as InstanceOut;
}

const FUTURE = new Date(Date.now() + 20 * 86_400_000).toISOString();
const PAST = new Date(Date.now() - 86_400_000).toISOString();

function renderWithApp(ui: React.ReactElement) {
  return render(<App>{ui}</App>);
}

// hoisted mock 调用历史逐用例清零
beforeEach(() => {
  vi.clearAllMocks();
});

// antd 两字按钮插空格,可访问名是「开 机」
const BTN_START = /开\s*机/;
const BTN_STOP = /关\s*机/;
const BTN_MORE = /更\s*多/;

describe("InstanceActions", () => {
  it("stopped 实例:开机可用,关机禁用(灰置而非隐藏)", () => {
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
    expect(screen.getByRole("button", { name: BTN_STOP })).toBeDisabled();
  });

  it("running 实例:主动作是「连接 ▾」而不是开机;菜单里有复制 SSH / 打开 JupyterLab", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    expect(screen.queryByRole("button", { name: BTN_START })).toBeNull();
    await user.click(screen.getByRole("button", { name: /连\s*接/ }));
    expect(await screen.findByText("复制 SSH 命令")).toBeInTheDocument();
    expect(screen.getByText("打开 JupyterLab")).toBeInTheDocument();
  });

  it("failed 实例:主动作是「重新创建」并链到该规格的创建页", () => {
    renderWithApp(<InstanceActions instance={{ ...makeInstance("failed"), sku_id: 7 } as InstanceOut} />);
    const link = screen.getByRole("link", { name: /重新创建/ });
    expect(link).toHaveAttribute("href", "/market/create/$skuId");
  });

  it("running 实例点关机:弹确认框,确认后触发 stop", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_STOP }));
    const dialog = await screen.findByRole("dialog");
    // antd v6 confirm 标题渲染两处(.ant-modal-title 与 .ant-modal-confirm-title)
    expect(within(dialog).getAllByText("确认关机?").length).toBeGreaterThan(0);
    await user.click(within(dialog).getByRole("button", { name: BTN_STOP }));
    expect(stopMutateAsync).toHaveBeenCalledWith("u-1");
  });

  it("frozen 实例:开机禁用(欠费冻结前置条件)", () => {
    renderWithApp(<InstanceActions instance={makeInstance("frozen")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeDisabled();
    expect(screen.getByRole("button", { name: BTN_STOP })).toBeDisabled();
  });

  it("释放全链路:更多 → 释放实例 → 键入名称 + 勾选清盘才解锁 → 触发 release", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByText("释放实例"));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认释放" });
    expect(confirm).toBeDisabled();
    // 两道闸缺一不可(ui-ux-spec 规则 4)
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(releaseMutate).toHaveBeenCalledWith("u-1");
  });
});

describe("InstanceActions · 包周期", () => {
  it("按量 running 实例:出「转包周期」,不出续费/自动续费/转按量(那三项对它不存在)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByText("转包周期")).toBeInTheDocument();
    expect(screen.queryByText("续费")).toBeNull();
    expect(screen.queryByText("开启自动续费")).toBeNull();
    expect(screen.queryByText("转按量")).toBeNull();
  });

  it("按量 stopped 实例:「转包周期」照常可用(后端两种状态都收)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    const item = await screen.findByText("转包周期");
    expect(item.closest("li")).not.toHaveAttribute("aria-disabled", "true");
  });

  it("按量在途/冻结实例:「转包周期」可见但灰置(后端会 409,先拦一道)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("frozen")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    const item = await screen.findByText("转包周期");
    expect(item.closest("li")).toHaveAttribute("aria-disabled", "true");
  });

  it("包周期实例:不出「转包周期」(它已经在包周期里,该走续费)", async () => {
    const user = userEvent.setup();
    renderWithApp(
      <InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE })} />,
    );
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    await screen.findByText("续费");
    expect(screen.queryByText("转包周期")).toBeNull();
  });

  it("点「转包周期」弹的是支付确认(标题带实例名,按钮写明是支付)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByText("转包周期"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText(/转包周期 · demo-vm/).length).toBeGreaterThan(0);
    expect(within(dialog).getByRole("button", { name: "支付并转为包周期" })).toBeEnabled();
    expect(within(dialog).getByText("从现在起算")).toBeInTheDocument();
  });

  it("包周期实例:菜单出续费与自动续费,开关项按当前状态取反", async () => {
    const user = userEvent.setup();
    renderWithApp(
      <InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE })} />,
    );
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByText("续费")).toBeInTheDocument();
    await user.click(screen.getByText("开启自动续费"));
    expect(autoRenewMutate).toHaveBeenCalledWith(true);
  });

  it("已开自动续费的实例菜单项变成「关闭自动续费」,点它传 false", async () => {
    const user = userEvent.setup();
    renderWithApp(
      <InstanceActions
        instance={makeSubscription("running", { expiresAt: FUTURE, autoRenew: true })}
      />,
    );
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByText("关闭自动续费"));
    expect(autoRenewMutate).toHaveBeenCalledWith(false);
  });

  it("包周期已到期:开机灰置(后端 assert_active 会 409,按钮先拦一道)", () => {
    renderWithApp(
      <InstanceActions
        instance={makeSubscription("stopped", { expiresAt: PAST, subStatus: "expired" })}
      />,
    );
    expect(screen.getByRole("button", { name: BTN_START })).toBeDisabled();
  });

  it("包周期在保且已关机:开机照常可用", () => {
    renderWithApp(
      <InstanceActions instance={makeSubscription("stopped", { expiresAt: FUTURE })} />,
    );
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
  });

  it("包周期实例点关机:确认文案是「不退费但保留库存」,不是按量那句「再开机可能没库存」", async () => {
    const user = userEvent.setup();
    renderWithApp(
      <InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE })} />,
    );
    await user.click(screen.getByRole("button", { name: BTN_STOP }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/周期内关机不退费/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/已租完/)).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: BTN_STOP }));
    expect(stopMutateAsync).toHaveBeenCalledWith("u-2");
  });
});

describe("InstanceActions · 竞价", () => {
  it("竞价 running 实例:出「转按量」,不出「转包周期」(market 是单值,两条路互斥)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSpot("running")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByText("转按量")).toBeInTheDocument();
    expect(screen.queryByText("转包周期")).toBeNull();
  });

  it("竞价在途实例:「转按量」可见但灰置(后端只收 running / stopped)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSpot("creating")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    const item = await screen.findByText("转按量");
    expect(item.closest("li")).toHaveAttribute("aria-disabled", "true");
  });

  it("点「转按量」的确认框必须写明重算当前整点小时与不再被回收,确认后才下发", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSpot("running")} />);
    await user.hover(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByText("转按量"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/当前整点小时将整体改按按量价结算/)).toBeInTheDocument();
    expect(within(dialog).getByText(/不再被回收/)).toBeInTheDocument();
    expect(toOnDemandMutateAsync).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "确认转按量" }));
    expect(toOnDemandMutateAsync).toHaveBeenCalled();
  });
});

describe("ReleaseModal", () => {
  it("键入名不匹配时确认按钮保持禁用", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReleaseModal instance={makeInstance("stopped")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认释放" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm-typo");
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeDisabled();
    expect(releaseMutate).not.toHaveBeenCalled();
  });

  it("creating 取消创建:尚未落盘,不弹清盘勾选,键入名字即解锁", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReleaseModal instance={makeInstance("creating")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    const confirm = within(dialog).getByRole("button", { name: "确认取消" });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirm).toBeEnabled();
  });

  it("包周期实例释放:正文额外写明「预付不退款、剩余天数作废」", async () => {
    renderWithApp(
      <ReleaseModal
        instance={makeSubscription("stopped", { expiresAt: FUTURE })}
        open
        onClose={() => {}}
      />,
    );
    const dialog = await screen.findByRole("dialog");
    // FUTURE = 20 天后到期
    expect(within(dialog).getByText(/预付费用不退款,剩余 20 天将一并作废/)).toBeInTheDocument();
  });

  it("按量实例释放:不出现包周期专属提示", async () => {
    renderWithApp(<ReleaseModal instance={makeInstance("stopped")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/预付费用不退款/)).toBeNull();
  });
});
