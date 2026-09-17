/** Transitional polling: first baseline, status-change invalidation and cleanup after leaving. */
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
  it("the first poll only records the baseline, no list invalidation", async () => {
    const fetchOne = vi.fn<FetchOne>().mockResolvedValue({ status: "creating" });
    const { qc, invalidateSpy } = setup(fetchOne, [row("a", "creating")]);
    await waitFor(() => expect(qc.getQueryData(["t", "a"])).toEqual({ status: "creating" }));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("a status transition seen by the poll invalidates the list prefix", async () => {
    const fetchOne = vi.fn<FetchOne>().mockResolvedValue({ status: "creating" });
    const { qc, invalidateSpy } = setup(fetchOne, [row("a", "creating")]);
    await waitFor(() => expect(qc.getQueryData(["t", "a"])).toEqual({ status: "creating" }));

    fetchOne.mockResolvedValue({ status: "running" });
    await qc.refetchQueries({ queryKey: ["t", "a"] });

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ["t", "pages"] })),
    );
  });

  it("an item leaving the transitional set clears its baseline; re-entering with a new status reports no false change", async () => {
    const fetchOne = vi.fn<FetchOne>().mockResolvedValue({ status: "creating" });
    const { qc, view, invalidateSpy } = setup(fetchOne, [row("a", "creating")]);
    await waitFor(() => expect(qc.getQueryData(["t", "a"])).toEqual({ status: "creating" }));

    view.rerender({ rs: [row("a", "running")] });
    qc.setQueryData(["t", "a"], { status: "running" });
    view.rerender({ rs: [row("a", "creating")] });

    await waitFor(() => expect(qc.isFetching({ queryKey: ["t", "a"] })).toBe(0));
    expect(invalidateSpy).not.toHaveBeenCalled();
  });
});
