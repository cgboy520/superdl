/** TableErrorEmpty: the error state renders "load failed + retry", the non-error state falls back to the antd default empty state. */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { TableErrorEmpty } from "./TableErrorEmpty";

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh-CN",
    resources: {
      "zh-CN": {
        shared: { common: { loadFailed: "Load failed, try again", retry: "Retry" } },
      },
    },
    defaultNS: "shared",
    returnNull: false,
  });
});

function renderEmpty(props: { isError: boolean; onRetry?: () => void }) {
  return render(
    <I18nextProvider i18n={i18n}>
      <TableErrorEmpty {...props} />
    </I18nextProvider>,
  );
}

describe("TableErrorEmpty", () => {
  it("renders load failed with a retry button in the error state, click fires onRetry", async () => {
    const onRetry = vi.fn();
    renderEmpty({ isError: true, onRetry });
    expect(screen.getByText("Load failed, try again")).toBeInTheDocument();
    const btn = screen.getByRole("button");
    expect(btn.textContent.replace(/\s/g, "")).toBe("Retry");
    await userEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("falls back to the antd default empty state without error (no failure copy, no retry)", () => {
    renderEmpty({ isError: false });
    expect(screen.queryByText("Load failed, try again")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
