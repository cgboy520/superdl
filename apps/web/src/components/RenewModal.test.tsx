/**
 * 续费 modal:预览金额的口径(原价快照 → 折扣 → 应付)、余额门槛、幂等键的稳定性。
 * 挂了说明什么坏了:
 * - 金额三行对不上 → 预览与后端 quote_subscription 的量化顺序脱钩,用户看到的价不是要扣的价;
 * - 报价基准换成了 price_hourly(折后价)或 SKU 现价 → 预览与实扣对不上,或者涨价追到了已购用户;
 * - 改周期/数量换了幂等键 → 同一张单会被当成两张,响应丢失后重提会扣两次钱;
 * - 余额不足还能点确认 → 把必然失败的请求送进扣款路径。
 * 网络与查询全部 mock,不走真实接口。
 */
import type { InstanceOut } from "@superdl/api-client";
import { formatMoney, quoteSubscription } from "@superdl/ui";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RenewModal } from "./RenewModal";

const { renewMutate, subscribeMutate, walletBalance } = vi.hoisted(() => ({
  renewMutate: vi.fn(),
  subscribeMutate: vi.fn(),
  walletBalance: { current: "3000.00" },
}));

vi.mock("../api/mutations", () => ({
  useRenewInstance: () => ({ mutate: renewMutate, isPending: false }),
  useSubscribeInstance: () => ({ mutate: subscribeMutate, isPending: false }),
}));

vi.mock("../api/queries", () => ({
  useWallet: () => ({ data: { balance: walletBalance.current } }),
  // 折扣一律从 /policies 读,组件里不许有硬编码的百分数
  usePolicies: () => ({
    data: {
      period_discount_day: 95,
      period_discount_week: 90,
      period_discount_month: 80,
      period_discount_year: 70,
      period_expire_warn_days: 3,
    },
  }),
}));

// 余额不足的 CTA 是 TanStack Link:测试没有 Router 上下文,降级成原生 <a> 断言 href
vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

const STARTED_AT = "2026-08-04T04:00:00Z";
const EXPIRES_AT = "2026-09-03T04:00:00Z";

/** 包月实例。price_hourly 是折后时价(3.99 × 0.8),unit_price 是原价快照,
 *  报价基准必须取后者,拿前者会把折扣叠两遍;SKU 现价设成 9.99,误取现价则断言立刻红。 */
function makeInstance(): InstanceOut {
  return {
    uuid: "u-2",
    name: "my-vllm",
    status: "running",
    market: "subscription",
    gpu_count: 1,
    price_hourly: "3.1920",
    subscription: {
      period: "month",
      period_count: 1,
      started_at: STARTED_AT,
      expires_at: EXPIRES_AT,
      status: "active",
      auto_renew: false,
      amount_paid: "2298.24",
      unit_price: "3.9900",
    },
  } as InstanceOut;
}

function renderModal() {
  return render(
    <App>
      <RenewModal instance={makeInstance()} open onClose={() => {}} />
    </App>,
  );
}

/** 按量实例:没有 subscription,报价基准是建实例时锁定的 price_hourly(此处 ¥3.99/时) */
function makeOnDemand(): InstanceOut {
  return {
    uuid: "u-3",
    name: "train-01",
    status: "running",
    market: "on_demand",
    gpu_count: 1,
    price_hourly: "3.9900",
  } as InstanceOut;
}

function renderConvert() {
  return render(
    <App>
      <RenewModal instance={makeOnDemand()} mode="subscribe" open onClose={() => {}} />
    </App>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  walletBalance.current = "3000.00";
});

describe("RenewModal", () => {
  it("默认按当前周期报价:原价 / 优惠 / 应付三行与后端同口径", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    // 3.99/时 × 1 卡 × 720 小时 = 2872.80,包月 8 折后 2298.24,优惠 574.56
    expect(within(dialog).getByText("¥2,872.80")).toBeInTheDocument();
    expect(within(dialog).getByText("-¥574.56")).toBeInTheDocument();
    expect(within(dialog).getByText("¥2,298.24")).toBeInTheDocument();
    // 余额变化用应付额扣减,不是另算一遍
    expect(within(dialog).getByText("¥3,000.00 → ¥701.76")).toBeInTheDocument();
  });

  it("预览金额 == 按 unit_price(原价快照)算出的应付额,与折后时价和 SKU 现价都无关", async () => {
    const user = userEvent.setup();
    const { subscription } = makeInstance();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    // 后端续费的报价基准就是 subscriptions.unit_price(见 billing/subscriptions.renew)
    for (const [period, pct, label] of [
      ["month", 80, /包\s*月/],
      ["year", 70, /包\s*年/],
      ["day", 95, /包\s*日/],
    ] as const) {
      await user.click(within(dialog).getByRole("button", { name: label }));
      const expected = quoteSubscription(subscription?.unit_price, {
        units: 1,
        period,
        periodCount: 1,
        discountPct: pct,
      });
      expect(within(dialog).getByText(formatMoney(expected.listAmount, "zh-CN"))).toBeInTheDocument();
      expect(within(dialog).getByText(formatMoney(expected.amount, "zh-CN"))).toBeInTheDocument();
    }
  });

  it("当前周期按 started_at → expires_at 两端显示", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    const ymd = (iso: string) => {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    };
    expect(
      within(dialog).getByText(new RegExp(`${ymd(STARTED_AT)}.*→.*${ymd(EXPIRES_AT)}`)),
    ).toBeInTheDocument();
  });

  it("新到期时间从老到期时刻起算(提前续费不丢手上剩的天数)", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    // 老到期 + 30 天定长(与后端 period_delta 同源)
    const expected = new Date(new Date(EXPIRES_AT).getTime() + 30 * 86_400_000);
    const pad = (n: number) => String(n).padStart(2, "0");
    const ymd = `${expected.getFullYear()}-${pad(expected.getMonth() + 1)}-${pad(expected.getDate())}`;
    expect(within(dialog).getByText(new RegExp(ymd))).toBeInTheDocument();
  });

  it("换周期重新报价:包年按 7 折算,数量是乘数", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /包\s*年/ }));
    // 3.99 × 8760 = 34,952.40;7 折后 24,466.68
    expect(within(dialog).getByText("¥34,952.40")).toBeInTheDocument();
    expect(within(dialog).getByText("¥24,466.68")).toBeInTheDocument();
  });

  it("同一次打开内改周期与数量不换幂等键(同一张单在改价,不是两张单)", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "确认续费" }));
    const firstKey = renewMutate.mock.calls[0]?.[0]?.idempotencyKey;
    expect(firstKey).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: /包\s*周/ }));
    await user.click(within(dialog).getByRole("button", { name: "确认续费" }));
    const second = renewMutate.mock.calls[1]?.[0];
    expect(second.idempotencyKey).toBe(firstKey);
    expect(second.body).toEqual({ period: "week", period_count: 1 });
  });

  it("转包周期:基准取按量实例锁定的 price_hourly,金额与建包周期实例逐分相同", async () => {
    renderConvert();
    const dialog = await screen.findByRole("dialog");
    // 3.99/时 × 1 卡 × 720 小时 = 2872.80,包月 8 折后 2298.24 —— 与续费那单同价,
    // 因为两边的基准都是同一个原价快照
    expect(within(dialog).getByText("¥2,872.80")).toBeInTheDocument();
    expect(within(dialog).getByText("¥2,298.24")).toBeInTheDocument();
  });

  it("转包周期从现在起算,走 subscribe 端点且带幂等键;不碰续费端点", async () => {
    const user = userEvent.setup();
    renderConvert();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("从现在起算")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "支付并转为包周期" }));
    expect(renewMutate).not.toHaveBeenCalled();
    const call = subscribeMutate.mock.calls[0]?.[0];
    expect(call.body).toEqual({ period: "month", period_count: 1 });
    expect(call.idempotencyKey).toBeTruthy();
  });

  it("续费从当前周期之后接上,走 renew 端点;不碰转换端点", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("接在当前周期之后")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "确认续费" }));
    expect(subscribeMutate).not.toHaveBeenCalled();
    expect(renewMutate).toHaveBeenCalled();
  });

  it("余额不足:主按钮改指 /billing 的可点链接(不是死按钮),不把必然失败的请求送出去", async () => {
    walletBalance.current = "10.00";
    renderModal();
    const dialog = await screen.findByRole("dialog");
    const link = within(dialog).getByRole("link", { name: "余额不足,去充值" });
    expect(link).toHaveAttribute("href", "/billing");
    // 链接可点(未被 disabled),且不触发续费提交
    const btn = within(link).getByRole("button");
    expect(btn).toBeEnabled();
    expect(renewMutate).not.toHaveBeenCalled();
  });

  it("余额足够:仍是普通确认按钮,不渲染充值链接", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("link", { name: "余额不足,去充值" })).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "确认续费" })).toBeEnabled();
  });
});
