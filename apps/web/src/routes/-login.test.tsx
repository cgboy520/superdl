/** 登录页:字段可见标签 + e2e 依赖的 placeholder、「忘记密码」两种登录模式都在、左栏行情摘要取真实 /skus(失败回落静态三条)。
 *  挂了说明:表单字段丢了标签(a11y 扫描红)或丢了 placeholder(e2e 定位失效)、验证码登录的人找不到找回密码入口、左栏又变回口号墙或接口失败时白屏。 */
import type { SkuMarketOut } from "@superdl/api-client";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "antd";
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Route } from "./login";

const LoginPage = (Route as unknown as { component: ComponentType }).component;

/** lazyRouteComponent 首次解析要过一次 vite transform,给足超时 */
const LAZY = { timeout: 10_000 };

const { skusState, breakpoints } = vi.hoisted(() => {
  const skusState = {
    current: { data: undefined as SkuMarketOut[] | undefined, isLoading: false, isError: false },
  };
  const breakpoints: { current: Record<string, boolean> } = { current: {} };
  return { skusState, breakpoints };
});

vi.mock("../api/queries", () => ({ useSkus: () => skusState.current }));

vi.mock("../api/mutations", () => {
  const stub = () => ({ mutate: vi.fn(), isPending: false });
  return { useLogin: stub, useRegister: stub, useResetPassword: stub, useSendSmsCode: stub };
});

// 顶栏主题钮另有归属;这里只看登录表单与左栏
vi.mock("../components/layout/AppTopBar", () => ({ ThemeToggle: () => null }));

// 断点由用例控制:BrandPane 只在 lg 以上渲染
vi.mock("antd", async (importOriginal) => {
  const antd = await importOriginal<typeof import("antd")>();
  return { ...antd, Grid: { ...antd.Grid, useBreakpoint: () => breakpoints.current } };
});

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (opts: object) => ({ ...opts, useSearch: () => ({}) }),
  // autoCodeSplitting 后组件经 lazyRouteComponent 异步加载:测试里还原成 React.lazy
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

describe("登录页表单", () => {
  beforeEach(() => {
    breakpoints.current = {};
    skusState.current = { data: undefined, isLoading: false, isError: false };
  });

  it("手机号与验证码都有可见标签,且保留 e2e 定位用的 placeholder", async () => {
    renderLogin();
    expect(await screen.findByLabelText("手机号", undefined, LAZY)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("手机号")).toBeInTheDocument();
    expect(screen.getByLabelText("短信验证码")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("短信验证码")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "获取验证码" })).toBeInTheDocument();
    // antd 给两字按钮插了字间空格,断言放宽到中间可有空白
    expect(screen.getByRole("button", { name: /^登\s*录$/ })).toBeInTheDocument();
  });

  it("「忘记密码」在验证码登录与密码登录两种模式都可见,密码字段带标签", async () => {
    const user = userEvent.setup();
    renderLogin();
    expect(await screen.findByRole("button", { name: "忘记密码?" }, LAZY)).toBeInTheDocument();

    await user.click(screen.getByText("密码登录"));
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "忘记密码?" })).toBeInTheDocument();
  });
});

describe("登录页左栏行情摘要", () => {
  beforeEach(() => {
    breakpoints.current = { lg: true };
  });

  it("取 GPU 规格最低单卡时价与去重可开台数(CPU 规格不参与比价)", async () => {
    skusState.current = {
      data: [
        makeSku({ id: 1, price_hourly: "2.5000", available_count: 12 }),
        makeSku({ id: 2, gpu_model: "RTX 4090", pool_label: "shared", price_hourly: "0.9900", available_count: 8 }),
        // CPU 规格没有 gpu_model:既不参与最低价,也不计入可开台数
        makeSku({ id: 3, gpu_model: "", price_hourly: "0.1000", available_count: 99 }),
      ],
      isLoading: false,
      isError: false,
    };
    renderLogin();
    expect(await screen.findByText("GPU 时价低至 ¥0.99/时", undefined, LAZY)).toBeInTheDocument();
    expect(screen.getByText("当前可开 20 台实例")).toBeInTheDocument();
  });

  it("接口失败回落静态三条,不留空栏", async () => {
    skusState.current = { data: undefined, isLoading: false, isError: true };
    renderLogin();
    expect(await screen.findByText("数据盘独立于实例,释放实例不丢数据", undefined, LAZY)).toBeInTheDocument();
    expect(screen.queryByText(/GPU 时价低至/)).not.toBeInTheDocument();
  });
});
