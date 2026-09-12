/** 输入框 ↔ URL 参数双向同步:输入即时进本地 state,防抖 300ms 回写 URL(replace);URL 外部变化(深链 / 返回)同步回输入框。
 *  URL 里的值是「已提交查询」的事实源(检索落审计的页面只在 URL 变化时发请求)。 */

import { useDebouncedValue } from "@superdl/ui";
import { useEffect, useState } from "react";

export function useUrlCommittedInput(
  urlValue: string | undefined,
  commit: (next: string | undefined) => void,
  debounceMs = 300,
): { value: string; setValue: (v: string) => void; committed: string | undefined } {
  const [value, setValue] = useState(urlValue ?? "");
  // URL 变化同步进输入框(渲染期派生态)
  const [prevUrl, setPrevUrl] = useState(urlValue);
  if (urlValue !== prevUrl) {
    setPrevUrl(urlValue);
    if (urlValue !== undefined) setValue(urlValue);
  }
  const debounced = useDebouncedValue(value, debounceMs);
  // 空白即缺省:trim 后空串 → undefined(与 web useCursorList 同口径)
  const committed = debounced.trim() || undefined;
  useEffect(() => {
    if ((committed ?? "") === (urlValue ?? "")) return;
    commit(committed);
  }, [committed, urlValue, commit]);
  return { value, setValue, committed: urlValue };
}
