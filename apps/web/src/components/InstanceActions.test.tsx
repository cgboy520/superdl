/** InstanceActions / ReleaseModal: the three slots follow the status (primary / secondary / "More ▾" expands, entries role=menuitem), stop confirmation dialog, multi-level release protection. Write hooks are all mocked. */
import type { InstanceOut } from "@superdl/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InstanceActions, ReleaseModal } from "./InstanceActions";

const { startMutate, stopMutateAsync, restartMutateAsync, releaseMutate, autoRenewMutate, toOnDemandMutateAsync } =
  vi.hoisted(() => ({
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
  useRenewInstance: () => ({ mutate: vi.fn(), isPending: false }),
  useSubscribeInstance: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
  useNavigate: () => vi.fn(),
}));

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

/** Spot instance: price_hourly is already the discounted price */
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

/** Subscription instance: market and subscription given together, either missing means not a subscription */
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

beforeEach(() => {
  vi.clearAllMocks();
});

const BTN_START = /开\s*机/; // cjk-ok
const BTN_STOP = /关\s*机/; // cjk-ok
const BTN_MORE = /更\s*多/; // cjk-ok
const BTN_EVENTS = "事件记录"; // cjk-ok
const MENU_RENEW = /^续\s*费$/; // cjk-ok

describe("InstanceActions", () => {
  it("stopped instance: start available, the secondary action is Events (stop appears only while running)", () => {
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
    expect(screen.queryByRole("button", { name: BTN_STOP })).toBeNull();
    expect(screen.getByRole("button", { name: BTN_EVENTS })).toBeEnabled();
  });

  it("running instance: the primary action is Connect ▾, not start; the menu has copy SSH / open JupyterLab", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    expect(screen.queryByRole("button", { name: BTN_START })).toBeNull();
    await user.click(screen.getByRole("button", { name: /连\s*接/ })); // cjk-ok
    expect(await screen.findByText("复制 SSH 命令")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByText("打开 JupyterLab")).toBeInTheDocument(); // cjk-ok
  });

  it("failed instance: the primary action is Recreate and links to the create page of that spec", () => {
    renderWithApp(<InstanceActions instance={{ ...makeInstance("failed"), sku_id: 7 }} />);
    const link = screen.getByRole("link", { name: /重新创建/ }); // cjk-ok
    expect(link).toHaveAttribute("href", "/market/create/$skuId");
  });

  it("running instance stop: a confirmation dialog, confirm fires stop", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_STOP }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText("确认关机?").length).toBeGreaterThan(0); // cjk-ok
    await user.click(within(dialog).getByRole("button", { name: BTN_STOP }));
    expect(stopMutateAsync).toHaveBeenCalledWith("u-1");
  });

  it("frozen instance: start is gated (arrears freeze precondition), click does not fire start", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("frozen")} />);
    const start = screen.getByRole("button", { name: BTN_START });
    expect(start).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByRole("button", { name: BTN_STOP })).toBeNull();
    expect(screen.getByRole("button", { name: BTN_EVENTS })).toBeEnabled();
    await user.click(start);
    expect(startMutate).not.toHaveBeenCalled();
  });

  it("release end to end: more → release instance (dangerous item last) → type the name + tick the wipe box to unlock → release fires", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    const items = await screen.findAllByRole("menuitem");
    expect(items.at(-1)).toHaveTextContent("释放实例"); // cjk-ok
    await user.click(await screen.findByText("释放实例")); // cjk-ok
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认释放" }); // cjk-ok
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirm).toBeDisabled();
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeEnabled();
    await user.click(confirm);
    expect(releaseMutate).toHaveBeenCalledWith("u-1");
  });
});

describe("InstanceActions · subscription", () => {
  it("on-demand running instance: shows convert to subscription, not renew / auto-renew / convert to on-demand (those three do not exist for it)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByRole("menuitem", { name: "转包周期" })).toBeInTheDocument(); // cjk-ok
    expect(screen.queryByRole("menuitem", { name: MENU_RENEW })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "开启自动续费" })).toBeNull(); // cjk-ok
    expect(screen.queryByRole("menuitem", { name: "转按量" })).toBeNull(); // cjk-ok
  });

  it("on-demand stopped instance: convert to subscription stays available (the backend accepts both states)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("stopped")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByRole("menuitem", { name: "转包周期" })).not.toHaveAttribute("aria-disabled", "true"); // cjk-ok
  });

  it("on-demand transitional / frozen instance: convert to subscription visible but greyed (the backend would 409, catch it first)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("frozen")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByRole("menuitem", { name: "转包周期" })).toHaveAttribute("aria-disabled", "true"); // cjk-ok
  });

  it("subscription instance: no convert to subscription (already on one, renewal is the path)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE })} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    await screen.findByRole("menuitem", { name: MENU_RENEW });
    expect(screen.queryByRole("menuitem", { name: "转包周期" })).toBeNull(); // cjk-ok
  });

  it("clicking convert to subscription opens the payment confirmation (title with the instance name, button says pay)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeInstance("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByRole("menuitem", { name: "转包周期" })); // cjk-ok
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getAllByText(/转包周期 · demo-vm/).length).toBeGreaterThan(0); // cjk-ok
    expect(within(dialog).getByRole("button", { name: "支付并转为包周期" })).toBeEnabled(); // cjk-ok
    expect(within(dialog).getByText("从现在起算")).toBeInTheDocument(); // cjk-ok
  });

  it("subscription instance: the menu shows renew and auto-renew, the switch item inverts the current state", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE })} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByRole("menuitem", { name: MENU_RENEW })).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "开启自动续费" })); // cjk-ok
    expect(autoRenewMutate).toHaveBeenCalledWith(true);
  });

  it("with auto-renew on the menu item becomes disable auto-renew and passes false", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE, autoRenew: true })} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByRole("menuitem", { name: "关闭自动续费" })); // cjk-ok
    expect(autoRenewMutate).toHaveBeenCalledWith(false);
  });

  it("subscription expired: start greyed (the backend assert_active would 409, the button catches it first)", () => {
    renderWithApp(
      <InstanceActions instance={makeSubscription("stopped", { expiresAt: PAST, subStatus: "expired" })} />,
    );
    expect(screen.getByRole("button", { name: BTN_START })).toHaveAttribute("aria-disabled", "true");
  });

  it("subscription covered and stopped: start available as usual", () => {
    renderWithApp(<InstanceActions instance={makeSubscription("stopped", { expiresAt: FUTURE })} />);
    expect(screen.getByRole("button", { name: BTN_START })).toBeEnabled();
  });

  it("subscription instance stop: the confirmation says no refund but stock kept, not the on-demand stock warning", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSubscription("running", { expiresAt: FUTURE })} />);
    await user.click(screen.getByRole("button", { name: BTN_STOP }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/周期内关机不退费/)).toBeInTheDocument(); // cjk-ok
    expect(within(dialog).queryByText(/已租完/)).toBeNull(); // cjk-ok
    await user.click(within(dialog).getByRole("button", { name: BTN_STOP }));
    expect(stopMutateAsync).toHaveBeenCalledWith("u-2");
  });
});

describe("InstanceActions · spot", () => {
  it("spot running instance: shows convert to on-demand, not convert to subscription (market is single-valued, the two paths exclude each other)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSpot("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByRole("menuitem", { name: "转按量" })).toBeInTheDocument(); // cjk-ok
    expect(screen.queryByRole("menuitem", { name: "转包周期" })).toBeNull(); // cjk-ok
  });

  it("spot transitional instance: convert to on-demand visible but greyed (the backend accepts running / stopped only)", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSpot("creating")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    expect(await screen.findByRole("menuitem", { name: "转按量" })).toHaveAttribute("aria-disabled", "true"); // cjk-ok
  });

  it("the convert-to-on-demand confirmation must state the current-hour repricing and no more reclamation, dispatched only after confirm", async () => {
    const user = userEvent.setup();
    renderWithApp(<InstanceActions instance={makeSpot("running")} />);
    await user.click(screen.getByRole("button", { name: BTN_MORE }));
    await user.click(await screen.findByRole("menuitem", { name: "转按量" })); // cjk-ok
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/当前整点小时将整体改按按量价结算/)).toBeInTheDocument(); // cjk-ok
    expect(within(dialog).getByText(/不再被回收/)).toBeInTheDocument(); // cjk-ok
    expect(toOnDemandMutateAsync).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "确认转按量" })); // cjk-ok
    expect(toOnDemandMutateAsync).toHaveBeenCalled();
  });
});

describe("ReleaseModal", () => {
  it("the confirm button stays disabled while the typed name does not match", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReleaseModal instance={makeInstance("stopped")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "确认释放" }); // cjk-ok
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm-typo");
    await user.click(within(dialog).getByRole("checkbox"));
    expect(confirm).toBeDisabled();
    expect(releaseMutate).not.toHaveBeenCalled();
  });

  it("creating cancel: nothing on disk yet, no wipe checkbox, typing the name unlocks", async () => {
    const user = userEvent.setup();
    renderWithApp(<ReleaseModal instance={makeInstance("creating")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    const confirm = within(dialog).getByRole("button", { name: "确认取消" }); // cjk-ok
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByPlaceholderText("demo-vm"), "demo-vm");
    expect(confirm).toBeEnabled();
  });

  it("subscription instance release: the body also states no prepayment refund and forfeited days", async () => {
    renderWithApp(
      <ReleaseModal instance={makeSubscription("stopped", { expiresAt: FUTURE })} open onClose={() => {}} />,
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/预付费用不退款,剩余 20 天将一并作废/)).toBeInTheDocument(); // cjk-ok
  });

  it("on-demand instance release: no subscription-specific hint", async () => {
    renderWithApp(<ReleaseModal instance={makeInstance("stopped")} open onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/预付费用不退款/)).toBeNull(); // cjk-ok
  });
});
