import { useEffect, useState } from "react";

import { useDebouncedValue } from "./useDebouncedValue";

/** Debounce the input then call commit, blanks become undefined; committed returns urlValue. Non-undefined URL changes sync into the input. */
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
