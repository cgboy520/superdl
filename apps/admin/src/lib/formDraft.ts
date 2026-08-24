/** 表单草稿(sessionStorage,报告3#21):误关弹窗/页面刷新不丢输入。
 * 仅用于非敏感新建表单 —— 密码/凭据/支付字段禁止入草稿(会话级存储也是泄露面)。
 * 提交成功后必须 clear,否则下一次打开会复活旧值。 */

export interface FormDraft<T extends object> {
  /** 读出草稿(无草稿或 JSON 损坏 → undefined) */
  load: () => Partial<T> | undefined;
  /** 覆盖写(antd Form onValuesChange 的全量值直接传入) */
  save: (values: Partial<T>) => void;
  clear: () => void;
}

export function useFormDraft<T extends object>(key: string): FormDraft<T> {
  const storageKey = `superdl-form-draft:${key}`;
  return {
    load: () => {
      try {
        const raw = sessionStorage.getItem(storageKey);
        return raw ? (JSON.parse(raw) as Partial<T>) : undefined;
      } catch {
        return undefined;
      }
    },
    save: (values) => {
      try {
        sessionStorage.setItem(storageKey, JSON.stringify(values));
      } catch {
        // 存储满/隐私模式:草稿是体验增强,静默失败不阻塞表单
      }
    },
    clear: () => {
      sessionStorage.removeItem(storageKey);
    },
  };
}
