/** Login page: field labels, placeholders, password reset and the market summary. */
import type { SkuMarketOut } from "@superdl/api-client";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Route } from "./login";

const LoginPage = (Route as unknown as { component: ComponentType }).component;

const LAZY = { timeout: 10_000 };

const { skusState, breakpoints } = vi.hoisted(() => {
  const skusState = {
    current: { data: undefined as SkuMarketOut[] | undefined, isLoading: false, isError: false },
  };
  const breakpoints: { current: Record<string, boolean> } = { current: {} };
  return { skusState, breakpoints };
});

vi.mock("../api/queries", () => ({ useSkus: () => skusState.current, useSiteConfig: () => ({ data: undefined }) }));

vi.mock("../api/mutations", () => {
  const stub = () => ({ mutate: vi.fn(), isPending: false });
  return {
    useLogin: stub,
    useRegister: stub,
    useResetPassword: stub,
    useSendVerificationCode: stub,
    useRequestHandleCode: stub,
  };
});

vi.mock("../components/layout/AppTopBar", () => ({ ThemeToggle: () => null }));

vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return { ...antd, Grid: { ...antd.Grid, useBreakpoint: () => breakpoints.current } };
});

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: object) => ({ ...opts, useSearch: () => ({}) }),
  lazyRouteComponent: (loader: () => Promise<Record<string, ComponentType>>, name: string) =>
    lazy(async () => ({ default: (await loader())[name] as ComponentType })),
  useNavigate: () => vi.fn(),
  useRouter: () => ({ history: { push: vi.fn() } }),
  Link: ({ to, children }: { to: string; children: ReactNode }) => <a href={to}>{children}</a>,
}));

function makeSku(over: Partial<SkuMarketOut>): SkuMarketOut {
  return {
    id: 1,
    name: "sku",
    gpu_model: "H100 SXM",
    pool_label: "dedicated",
    tier: "dedicated",
    price_hourly: "2.5000",
    available_count: 12,
    cuda_max: null,
    disk_gb: 100,
    gpu_cores_pct: 100,
    max_gpus_per_instance: 8,
    mem_gb: 256,
    mig_profile: null,
    period_enabled: true,
    spot_enabled: false,
    vcpu: 32,
    vram_gb: 80,
    ...over,
  };
}

function renderLogin() {
  return render(
    <App>
      <Suspense fallback={null}>
        <LoginPage />
      </Suspense>
    </App>,
  );
}

describe("login page form", () => {
  beforeEach(() => {
    breakpoints.current = {};
    skusState.current = { data: undefined, isLoading: false, isError: false };
  });

  it("handle and code fields carry visible labels and keep the placeholders e2e locates", async () => {
    renderLogin();
    expect(await screen.findByLabelText("邮箱或手机号", undefined, LAZY)).toBeInTheDocument(); // cjk-ok
    expect(screen.getByPlaceholderText("邮箱或 +86 开头的手机号")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByLabelText("验证码")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByPlaceholderText("验证码")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByRole("button", { name: "获取验证码" })).toBeInTheDocument(); // cjk-ok
    expect(screen.getByRole("button", { name: /^登\s*录$/ })).toBeInTheDocument(); // cjk-ok
  });

  it("register mode asks for email + email code (phone only when the profile requires it)", async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.click(await screen.findByRole("button", { name: "免费注册" }, LAZY)); // cjk-ok
    expect(screen.getByLabelText("邮箱")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByPlaceholderText("邮箱验证码")).toBeInTheDocument(); // cjk-ok
    expect(screen.queryByLabelText("手机号")).not.toBeInTheDocument(); // cjk-ok
  });

  it("forgot password is visible in both code and password login, the password field is labelled", async () => {
    const user = userEvent.setup();
    renderLogin();
    expect(await screen.findByRole("button", { name: "忘记密码?" }, LAZY)).toBeInTheDocument(); // cjk-ok

    await user.click(screen.getByText("密码登录")); // cjk-ok
    expect(screen.getByLabelText("密码")).toBeInTheDocument(); // cjk-ok
    expect(screen.getByRole("button", { name: "忘记密码?" })).toBeInTheDocument(); // cjk-ok
  });
});

describe("login page market summary", () => {
  beforeEach(() => {
    breakpoints.current = { lg: true };
  });

  it("takes the lowest per-card GPU hourly price and the deduplicated available count (CPU specs do not compete)", async () => {
    skusState.current = {
      data: [
        makeSku({ id: 1, price_hourly: "2.5000", available_count: 12 }),
        makeSku({ id: 2, gpu_model: "RTX 4090", pool_label: "shared", price_hourly: "0.9900", available_count: 8 }),
        makeSku({ id: 3, gpu_model: "", price_hourly: "0.1000", available_count: 99 }),
      ],
      isLoading: false,
      isError: false,
    };
    renderLogin();
    expect(await screen.findByText("GPU 时价低至 ¥0.99/时", undefined, LAZY)).toBeInTheDocument(); // cjk-ok
    expect(screen.getByText("当前可开 20 台实例")).toBeInTheDocument(); // cjk-ok
  });

  it("falls back to the three static facts when the API fails, leaving no empty column", async () => {
    skusState.current = { data: undefined, isLoading: false, isError: true };
    renderLogin();
    expect(await screen.findByText("数据盘独立于实例,释放实例不丢数据", undefined, LAZY)).toBeInTheDocument(); // cjk-ok
    expect(screen.queryByText(/GPU 时价低至/)).not.toBeInTheDocument(); // cjk-ok
  });
});
