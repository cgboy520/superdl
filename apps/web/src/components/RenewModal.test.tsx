/** Renewal modal: quote, balance gate and idempotency key. */
import type { InstanceOut } from "@superdl/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RenewModal } from "./RenewModal";

interface MutateCall {
  body: { period: string; period_count: number };
  idempotencyKey: string;
}

const { renewMutate, subscribeMutate, walletBalance } = vi.hoisted(() => ({
  renewMutate: vi.fn<(c: MutateCall) => void>(),
  subscribeMutate: vi.fn<(c: MutateCall) => void>(),
  walletBalance: { current: "3000.00" },
}));

vi.mock("../api/mutations", () => ({
  useRenewInstance: () => ({ mutate: renewMutate, isPending: false }),
  useSubscribeInstance: () => ({ mutate: subscribeMutate, isPending: false }),
}));

vi.mock("../api/queries", () => ({
  useWallet: () => ({ data: { balance: walletBalance.current } }),
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

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

const STARTED_AT = "2026-08-04T04:00:00Z";
const EXPIRES_AT = "2026-09-03T04:00:00Z";

/** Monthly instance: discounted hourly price, list-price snapshot and current SKU price all differ. */
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

/** On-demand instance: no subscription, the quote basis is price_hourly (¥3.99/h) */
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
  vi.setSystemTime(new Date("2026-08-20T00:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("RenewModal", () => {
  it("quotes the current period by default: list / discount / payable match the backend", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("¥2,872.80")).toBeInTheDocument();
    expect(within(dialog).getByText("-¥574.56")).toBeInTheDocument();
    expect(within(dialog).getByText("¥2,298.24")).toBeInTheDocument();
    expect(within(dialog).getByText("¥3,000.00 → ¥701.76")).toBeInTheDocument();
  });

  it("the current period shows started_at → expires_at at both ends", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    const ymd = (iso: string) => {
      const d = new Date(iso);
      const pad = (n: number) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    };
    expect(within(dialog).getByText(new RegExp(`${ymd(STARTED_AT)}.*→.*${ymd(EXPIRES_AT)}`))).toBeInTheDocument();
  });

  it("the new expiry counts from the old expiry (early renewal keeps the remaining days)", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    const expected = new Date(new Date(EXPIRES_AT).getTime() + 30 * 86_400_000);
    const pad = (n: number) => String(n).padStart(2, "0");
    const ymd = `${expected.getFullYear()}-${pad(expected.getMonth() + 1)}-${pad(expected.getDate())}`;
    expect(within(dialog).getByText(new RegExp(ymd))).toBeInTheDocument();
  });

  it("re-quotes on a period change: yearly at 30 % off, quantity is a multiplier", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /包\s*年/ })); // cjk-ok
    expect(within(dialog).getByText("¥34,952.40")).toBeInTheDocument();
    expect(within(dialog).getByText("¥24,466.68")).toBeInTheDocument();
  });

  it("changing period and quantity within one opening keeps the idempotency key (one order being repriced, not two)", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "确认续费" })); // cjk-ok
    const firstKey = renewMutate.mock.calls[0]?.[0]?.idempotencyKey;
    expect(firstKey).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: /包\s*周/ })); // cjk-ok
    await user.click(within(dialog).getByRole("button", { name: "确认续费" })); // cjk-ok
    const second = renewMutate.mock.calls[1]?.[0];
    if (!second) throw new Error("expected a second renew call");
    expect(second.idempotencyKey).toBe(firstKey);
    expect(second.body).toEqual({ period: "week", period_count: 1 });
  });

  it("convert to subscription: the basis is the on-demand instance's locked price_hourly, amounts equal a fresh subscription instance to the cent", async () => {
    renderConvert();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("¥2,872.80")).toBeInTheDocument();
    expect(within(dialog).getByText("¥2,298.24")).toBeInTheDocument();
  });

  it("convert to subscription counts from now, hits the subscribe endpoint with an idempotency key; never the renew endpoint", async () => {
    const user = userEvent.setup();
    renderConvert();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("从现在起算")).toBeInTheDocument(); // cjk-ok
    await user.click(within(dialog).getByRole("button", { name: "支付并转为包周期" })); // cjk-ok
    expect(renewMutate).not.toHaveBeenCalled();
    const call = subscribeMutate.mock.calls[0]?.[0];
    if (!call) throw new Error("expected a subscribe call");
    expect(call.body).toEqual({ period: "month", period_count: 1 });
    expect(call.idempotencyKey).toBeTruthy();
  });

  it("renewal continues after the current period, hits the renew endpoint; never the convert endpoint", async () => {
    const user = userEvent.setup();
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("接在当前周期之后")).toBeInTheDocument(); // cjk-ok
    await user.click(within(dialog).getByRole("button", { name: "确认续费" })); // cjk-ok
    expect(subscribeMutate).not.toHaveBeenCalled();
    expect(renewMutate).toHaveBeenCalled();
  });

  it("insufficient balance: the primary button becomes a clickable link to /billing (not a dead button), the doomed request is not sent", async () => {
    walletBalance.current = "10.00";
    renderModal();
    const dialog = await screen.findByRole("dialog");
    const link = within(dialog).getByRole("link", { name: "余额不足,去充值" }); // cjk-ok
    expect(link).toHaveAttribute("href", "/billing");
    const btn = within(link).getByRole("button");
    expect(btn).toBeEnabled();
    expect(renewMutate).not.toHaveBeenCalled();
  });

  it("sufficient balance: still a plain confirm button, no top-up link rendered", async () => {
    renderModal();
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("link", { name: "余额不足,去充值" })).not.toBeInTheDocument(); // cjk-ok
    expect(within(dialog).getByRole("button", { name: "确认续费" })).toBeEnabled(); // cjk-ok
  });
});
