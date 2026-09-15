import { useEffect, useState } from "react";

import { useDebouncedValue } from "./useDebouncedValue";

/** 输入防抖后调用 commit,空白转为 undefined;committed 返回 urlValue。非 undefined 的 URL 变化同步到输入框。 */
export function useUrlCommittedInput(
  urlValue: string | undefined,
  commit: (next: string | undefined) => void,
  debounceMs = 300,
): { value: string; setValue: (v: string) => void; committed: string | undefined } {
  const [value, setValue] = useState(urlValue ?? "");
  const [prevUrl, setPrevUrl] = useState(urlValue);
  if (urlValue !== prevUrl) {
    setPrevUrl(urlValue);
    if (urlValue !== undefined) setValue(urlValue);
  }
  const debounced = useDebouncedValue(value, debounceMs);
  const committed = debounced.trim() || undefined;
  useEffect(() => {
    if ((committed ?? "") === (urlValue ?? "")) return;
    commit(committed);
  }, [committed, urlValue, commit]);
  return { value, setValue, committed: urlValue };
}
