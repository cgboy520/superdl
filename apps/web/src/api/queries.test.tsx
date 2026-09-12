/** useTransientRefresh 的失效语义。挂了说明:首轮误失效列表(无谓抖动)/ 轮询发现状态迁移后列表不刷新(实例卡在过渡态)/ 退出过渡态的条目基线残留(再进入时误报变化)。 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi, type Mock } from "vitest";

import { useTransientRefresh } from "./queries";

interface Row {
  id: string;
  status: string;
}
const row = (id: string, status: string): Row => ({ id, status });

type FetchOne = (id: string) => Promise<{ status: string }>;

function setup(fetchOne: Mock<FetchOne>, rows: Row[]) {
  // staleTime: Infinity —— 重挂载不自动重取,测试时序可控(初始无缓存仍会取一次)
  const qc = new QueryClient({ defaultOptions: { queries: { staleTime: Number.POSITIVE_INFINITY } } });
  const invalidateSpy = vi.spyOn(qc, "invalidateQueries");
  const view = renderHook(
    ({ rs }: { rs: Row[] }) =>
      useTransientRefresh({
        rows: rs,
        idOf: (r) => r.id,
        statusOf: (r) => r.status,
        isTransient: (s) => s === "creating",
        detailKey: (id) => ["t", id],
        fetchOne,
        listPrefix: ["t", "pages"],
      }),
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      ),
      initialProps: { rs: rows },
    },
  );
  return { qc, view, invalidateSpy };
}

describe("useTransientRefresh", () => {
  it("首轮轮询只落基线,不失效列表", async () => {
    const fetchOne = vi.fn<FetchOne>().mockResolvedValue({ status: "creating" });
    const { qc, invalidateSpy } = setup(fetchOne, [row("a", "creating")]);
    await waitFor(() => expect(qc.getQueryData(["t", "a"])).toEqual({ status: "creating" }));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("轮询发现 status 迁移后失效列表前缀", async () => {
    const fetchOne = vi.fn<FetchOne>().mockResolvedValue({ status: "creating" });
    const { qc, invalidateSpy } = setup(fetchOne, [row("a", "creating")]);
    await waitFor(() => expect(qc.getQueryData(["t", "a"])).toEqual({ status: "creating" }));

    fetchOne.mockResolvedValue({ status: "running" });
    await qc.refetchQueries({ queryKey: ["t", "a"] });

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ["t", "pages"] })),
    );
  });

  it("条目退出过渡态集合后清基线;带着新状态再进入时不误报变化", async () => {
    const fetchOne = vi.fn<FetchOne>().mockResolvedValue({ status: "creating" });
    const { qc, view, invalidateSpy } = setup(fetchOne, [row("a", "creating")]);
    await waitFor(() => expect(qc.getQueryData(["t", "a"])).toEqual({ status: "creating" }));

    // a 到终态,从过渡态集合退出
    view.rerender({ rs: [row("a", "running")] });
    // 离开期间它其实又变过一次(例如失败后重建),缓存被详情页写成 running
    qc.setQueryData(["t", "a"], { status: "running" });
    // a 再次以过渡态出现在列表里:重挂载读到 running,若基线残留(creating)会误判为「发生变化」
    view.rerender({ rs: [row("a", "creating")] });

    await waitFor(() => expect(qc.isFetching({ queryKey: ["t", "a"] })).toBe(0));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
