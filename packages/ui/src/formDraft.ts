/** 表单草稿(sessionStorage)。仅非敏感新建表单;密码/凭据/支付字段禁入;提交成功后 clear。 */

import { useMemo } from "react";

export interface FormDraft<T extends object> {
  /** 读出草稿(无或损坏 → undefined) */
  load: () => Partial<T> | undefined;
  /** 覆盖写 */
  save: (values: Partial<T>) => void;
  clear: () => void;
}

export function useFormDraft<T extends object>(key: string): FormDraft<T> {
  const storageKey = `superdl-form-draft:${key}`;
  // 返回值按 key 记忆:effect 依赖 draft 时不会每渲染重放
  return useMemo(
    () => ({
      load: () => {
        try {
          const raw = sessionStorage.getItem(storageKey);
          return raw ? (JSON.parse(raw) as Partial<T>) : undefined;
        } catch {
          return undefined;
        }
      },
      save: (values: Partial<T>) => {
        try {
          sessionStorage.setItem(storageKey, JSON.stringify(values));
        } catch {
          // 存储不可用时静默失败
        }
      },
      clear: () => {
        sessionStorage.removeItem(storageKey);
      },
    }),
    [storageKey],
  );
}
