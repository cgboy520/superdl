/**
 * 写操作统一封装:生成的 fetcher 函数 + useMutation。
 * 成功后按 invalidates 声明的域失效相关查询(每个 hook 显式声明,空数组 = 不失效)。
 */

import type { ApiError,
  SshKeyOut,
} from "@superdl/api-client";
import {
  addSshKeyApiV1SshKeysPost,
  appendMessageApiV1TicketsTicketIdMessagesPost,
  cancelDeletionRequestApiV1MeDeletionRequestCancelPost,
  closeTicketApiV1TicketsTicketIdClosePost,
  convertToOnDemandApiV1InstancesUuidToOnDemandPost,
  createApiKeyApiV1ServicesSlugApiKeysPost,
  createDeletionRequestApiV1MeDeletionRequestPost,
  createDiskApiV1DisksPost,
  createInstanceApiV1InstancesPost,
  createInvoiceApiV1BillingInvoicesPost,
  createRechargeApiV1WalletRechargesPost,
  createRefundApiV1WalletRefundsPost,
  createServiceApiV1ServicesPost,
  createTicketApiV1TicketsPost,
  deleteServiceApiV1ServicesSlugDelete,
  deleteDiskApiV1DisksUuidDelete,
  deleteSshKeyApiV1SshKeysKeyIdDelete,
  expandDiskApiV1DisksUuidPatch,
  loginApiV1AuthLoginPost,
  logoutAllApiV1AuthLogoutAllPost,
  logoutApiV1AuthLogoutPost,
  markAllReadApiV1NotificationsReadAllPost,
  markReadApiV1NotificationsNotificationIdReadPost,
  mockWebhookApiV1WebhooksMockPost,
  patchServiceApiV1ServicesSlugPatch,
  registerApiV1AuthRegisterPost,
  resetPasswordApiV1AuthPasswordResetPost,
  releaseInstanceApiV1InstancesUuidDelete,
  renameInstanceApiV1InstancesUuidPatch,
  renewInstanceApiV1InstancesUuidRenewPost,
  resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost,
  restartInstanceApiV1InstancesUuidRestartPost,
  revokeApiKeyApiV1ServicesSlugApiKeysKeyIdDelete,
  sendSmsCodeApiV1AuthSmsCodePost,
  setAutoRenewApiV1InstancesUuidAutoRenewPost,
  setWarnThresholdApiV1MeWarnThresholdPatch,
  submitRealNameApiV1MeRealNamePost,
  startInstanceApiV1InstancesUuidStartPost,
  startServiceApiV1ServicesSlugStartPost,
  stopInstanceApiV1InstancesUuidStopPost,
  stopServiceApiV1ServicesSlugStopPost,
  subscribeInstanceApiV1InstancesUuidSubscribePost,
} from "@superdl/api-client";
import type {
  ApiKeyCreateOut,
  DeletionRequestCreate,
  DiskCreate,
  DiskExpand,
  InstanceCreate,
  InstanceOut,
  InstanceRenew,
  InvoiceCreate,
  LoginRequest,
  RealNameRequest,
  RechargeCreate,
  RefundCreate,
  PasswordResetRequest,
  RegisterRequest,
  RenewOut,
  ServiceCreate,
  ServiceOut,
  ServicePatch,
  SmsCodeRequest,
  TicketCreate,
  TicketMessageCreate,
  TicketOut,
} from "@superdl/api-client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { App } from "antd";
import { useCallback } from "react";

import { useApiErrorText } from "@superdl/ui";
import { authStore } from "../stores/auth";

interface MutationOpts<TData> {
  onSuccess?: (data: TData) => void;
  silentError?: boolean;
  /** 成功后失效的查询键前缀;空数组 = 不失效任何查询 */
  invalidates: readonly string[];
}
/** 页面侧可传的项:失效域由各 hook 自己声明,不暴露给页面 */
type CallerOpts<TData = unknown> = Omit<MutationOpts<TData>, "invalidates">;

export function useApiMutation<TVars, TData>(
  fn: (vars: TVars) => Promise<TData>,
  opts: MutationOpts<TData>,
) {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const errText = useApiErrorText();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: (data) => {
      // 不 await:await 会让「改名成功」这类回调等到相关 refetch 完
      for (const key of opts.invalidates) void queryClient.invalidateQueries({ queryKey: [key] });
      opts.onSuccess?.(data);
    },
    onError: (err) => {
      if (!opts.silentError) message.error(errText(err));
    },
  });
}

// auth
export const useSendSmsCode = (o?: { onSuccess?: () => void; silentError?: boolean }) =>
  useApiMutation((body: SmsCodeRequest) => sendSmsCodeApiV1AuthSmsCodePost(body), { ...o, invalidates: [] });
export const useRegister = (o?: CallerOpts) =>
  useApiMutation((body: RegisterRequest) => registerApiV1AuthRegisterPost(body), { ...o, invalidates: [] });
export const useLogin = (o?: CallerOpts) =>
  useApiMutation((body: LoginRequest) => loginApiV1AuthLoginPost(body), { ...o, invalidates: [] });
/** 设置/修改/找回密码(手机号 + 验证码);成功返回新 token 对,旧会话已被撤销。 */
export const useResetPassword = (o?: CallerOpts) =>
  useApiMutation((body: PasswordResetRequest) => resetPasswordApiV1AuthPasswordResetPost(body), { ...o, invalidates: [] });

/** 登出:current = 撤销本设备 refresh token(cookie 路径,服务端顺带清 Cookie);
 *  all = 服务端撤销该账号全部会话(token_version+1)。
 *  之后清本地并整页刷新;请求失败不阻断本地登出。 */
export function useLogout() {
  return useCallback(async (scope: "current" | "all" = "current") => {
    try {
      if (scope === "all") {
        await logoutAllApiV1AuthLogoutAllPost();
      } else {
        // cookie 路径必须带 CSRF 纵深头(服务端强制);不带请求体,服务端从 cookie 取
        await logoutApiV1AuthLogoutPost({ headers: { "X-Requested-With": "fetch" } });
      }
    } catch {
      // 登出尽力而为,本地清理不依赖远端结果
    }
    authStore.getState().logout();
    // 整页刷新:清干净全部内存态(查询缓存由 main.tsx 的 token 变更订阅兜底清理)
    window.location.assign("/login");
  }, []);
}

// instances
// 实例写操作影响:实例域(列表/详情/事件/账单)与钱包余额(启停即结算)
const INSTANCE_INVALIDATES = ["instances", "wallet", "bills", "bill-daily-summary"] as const;
export const useCreateInstance = (o?: { onSuccess?: (d: unknown) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceCreate; idempotencyKey: string }) =>
      createInstanceApiV1InstancesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES, "skus"] },
  );
export const useStartInstance = () =>
  useApiMutation((uuid: string) => startInstanceApiV1InstancesUuidStartPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES, "skus"],
  });
export const useStopInstance = () =>
  useApiMutation((uuid: string) => stopInstanceApiV1InstancesUuidStopPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES, "skus"],
  });
export const useRestartInstance = () =>
  useApiMutation((uuid: string) => restartInstanceApiV1InstancesUuidRestartPost(uuid), {
    invalidates: [...INSTANCE_INVALIDATES],
  });
export const useReleaseInstance = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => releaseInstanceApiV1InstancesUuidDelete(uuid), {
    ...o,
    invalidates: [...INSTANCE_INVALIDATES, "skus", "disks"],
  });
export const useRenameInstance = () =>
  useApiMutation(
    ({ uuid, name }: { uuid: string; name: string }) =>
      renameInstanceApiV1InstancesUuidPatch(uuid, { name }),
    { invalidates: ["instances"] },
  );
/** 包周期续费必须带幂等键(响应丢失后重提不会扣两次钱,重放回 200 + X-Idempotent-Replay)。
 *  键由续费 modal 每次打开生成一个 uuid:同一次打开内改周期/数量不换键,关掉重开才是新单。 */
export const useRenewInstance = (uuid: string, o?: CallerOpts<RenewOut>) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceRenew; idempotencyKey: string }) =>
      renewInstanceApiV1InstancesUuidRenewPost(uuid, body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** 按量转包周期:与续费同一入参/响应形态,区别只在起点从现在起算。
 *  后端会先结清转换前那段按量账再翻 market,失效面必须连账单一起。 */
export const useSubscribeInstance = (uuid: string, o?: CallerOpts<RenewOut>) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceRenew; idempotencyKey: string }) =>
      subscribeInstanceApiV1InstancesUuidSubscribePost(uuid, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** 竞价转按量(免被回收):不动 Pod、不重调度,只翻 market 与单价。不带幂等键(后端重放天然安全)。
 *  转换会把当前整点小时整体改按按量价重算,失效面必须连账单一起。 */
export const useConvertToOnDemand = (uuid: string, o?: CallerOpts<InstanceOut>) =>
  useApiMutation(
    (_v: void) => convertToOnDemandApiV1InstancesUuidToOnDemandPost(uuid),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** 自动续费开关:只改订阅行,不动实例状态,失效面只有实例域。 */
export const useSetAutoRenew = (uuid: string, o?: CallerOpts<InstanceOut>) =>
  useApiMutation(
    (enabled: boolean) => setAutoRenewApiV1InstancesUuidAutoRenewPost(uuid, { enabled }),
    { ...o, invalidates: ["instances"] },
  );
export const useResetJupyterToken = () =>
  useApiMutation((uuid: string) => resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost(uuid), {
    invalidates: ["instances"],
  });

// 在线服务
// 服务写操作影响:服务域(列表 / 详情 / 事件 / 账单)与钱包余额(启停即结算)
const SERVICE_INVALIDATES = ["services", "wallet", "bills", "bill-daily-summary"] as const;
/** 部署服务:必须带幂等键(按表单快照派生,重放回同一个服务而不是再部署一个)。 */
export const useCreateService = (o?: { onSuccess?: (d: ServiceOut) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: ServiceCreate; idempotencyKey: string }) =>
      createServiceApiV1ServicesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...SERVICE_INVALIDATES, "skus", "disks"] },
  );
export const useStopService = (o?: CallerOpts<ServiceOut>) =>
  useApiMutation((slug: string) => stopServiceApiV1ServicesSlugStopPost(slug), {
    ...o,
    invalidates: [...SERVICE_INVALIDATES, "skus"],
  });
export const useStartService = (o?: CallerOpts<ServiceOut>) =>
  useApiMutation((slug: string) => startServiceApiV1ServicesSlugStartPost(slug), {
    ...o,
    invalidates: [...SERVICE_INVALIDATES, "skus"],
  });
export const useDeleteService = (o?: CallerOpts<ServiceOut>) =>
  useApiMutation((slug: string) => deleteServiceApiV1ServicesSlugDelete(slug), {
    ...o,
    invalidates: [...SERVICE_INVALIDATES, "skus", "disks"],
  });
/** 改名 / 鉴权开关:只改 services 行,失效面只有服务域。 */
export const useUpdateService = (slug: string, o?: CallerOpts<ServiceOut>) =>
  useApiMutation((body: ServicePatch) => patchServiceApiV1ServicesSlugPatch(slug, body), {
    ...o,
    invalidates: ["services"],
  });
/** 新建服务访问 Key:响应里的明文 key 只露面这一次(库里只有 HMAC 摘要),
 *  调用方必须把它交给一次性展示的成功态,禁止入缓存或日志。 */
export const useCreateServiceApiKey = (slug: string, o?: CallerOpts<ApiKeyCreateOut>) =>
  useApiMutation((name: string) => createApiKeyApiV1ServicesSlugApiKeysPost(slug, { name }), {
    ...o,
    invalidates: ["services"],
  });
/** 吊销 Key:写 revoked_at 不删行,列表仍看得到这把 Key 存在过。 */
export const useRevokeServiceApiKey = (slug: string, o?: CallerOpts) =>
  useApiMutation((keyId: number) => revokeApiKeyApiV1ServicesSlugApiKeysKeyIdDelete(slug, keyId), {
    ...o,
    invalidates: ["services"],
  });

// wallet / billing
export const useCreateRecharge = (o?: { onSuccess?: (d: unknown) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RechargeCreate; idempotencyKey: string }) =>
      createRechargeApiV1WalletRechargesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["wallet", "ledger"] },
  );
export const useMockPay = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (vars: { order_no: string; amount: string }) =>
      mockWebhookApiV1WebhooksMockPost({ body: JSON.stringify(vars) }),
    { ...o, invalidates: ["wallet", "recharge", "ledger"] },
  );
/** 申请退款:必须带幂等键(按表单快照派生,重放返回既有单)。 */
export const useCreateRefund = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RefundCreate; idempotencyKey: string }) =>
      createRefundApiV1WalletRefundsPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["refunds", "refundable-orders", "wallet", "ledger"] },
  );
/** 申请开票:金额由服务端按账期计算(客户端不提交金额);幂等键重放返回既有单。 */
export const useCreateInvoice = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InvoiceCreate; idempotencyKey: string }) =>
      createInvoiceApiV1BillingInvoicesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["invoices", "invoice-eligible"] },
  );
export const useSubmitRealName = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (body: RealNameRequest) => submitRealNameApiV1MeRealNamePost(body),
    { ...o, invalidates: ["me"] },
  );
export const useSetWarnThreshold = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (hours: number) => setWarnThresholdApiV1MeWarnThresholdPatch({ low_balance_warn_hours: hours }),
    { ...o, invalidates: ["me"] },
  );

// 账号注销
/** 申请注销:服务端按 (user_id, pending) 幂等,重复提交返回既有申请。 */
export const useCreateDeletionRequest = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (body: DeletionRequestCreate) => createDeletionRequestApiV1MeDeletionRequestPost(body),
    { ...o, invalidates: ["deletion-request"] },
  );
export const useCancelDeletionRequest = (o?: { onSuccess?: () => void }) =>
  useApiMutation((_v: void) => cancelDeletionRequestApiV1MeDeletionRequestCancelPost(), {
    ...o,
    invalidates: ["deletion-request"],
  });

// disks
/** 建盘同样要幂等键:响应丢失后重提不会多出一块按日计费的盘。 */
export const useCreateDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: DiskCreate; idempotencyKey: string }) =>
      createDiskApiV1DisksPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["disks", "wallet"] },
  );
export const useExpandDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ uuid, body }: { uuid: string; body: DiskExpand }) => expandDiskApiV1DisksUuidPatch(uuid, body),
    { ...o, invalidates: ["disks", "wallet"] },
  );
export const useDeleteDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => deleteDiskApiV1DisksUuidDelete(uuid), {
    ...o,
    invalidates: ["disks", "wallet", "instances", "services"],
  });

// ssh keys / notify
export const useAddSshKey = (o?: { onSuccess?: (key: SshKeyOut) => void }) =>
  useApiMutation(
    (body: { name: string; public_key: string }) => addSshKeyApiV1SshKeysPost(body),
    { ...o, invalidates: ["ssh-keys"] },
  );
export const useDeleteSshKey = () =>
  useApiMutation((keyId: number) => deleteSshKeyApiV1SshKeysKeyIdDelete(keyId), {
    invalidates: ["ssh-keys"],
  });
export const useMarkNotificationRead = () =>
  useApiMutation((id: number) => markReadApiV1NotificationsNotificationIdReadPost(id), {
    invalidates: ["notifications"],
  });
export const useMarkAllNotificationsRead = () =>
  useApiMutation((_: void) => markAllReadApiV1NotificationsReadAllPost(), {
    invalidates: ["notifications"],
  });

// tickets
/** 新建工单:必须带幂等键(按表单快照派生,重放返回既有单)。 */
export const useCreateTicket = (o?: { onSuccess?: (d: TicketOut) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: TicketCreate; idempotencyKey: string }) =>
      createTicketApiV1TicketsPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["tickets"] },
  );
export const useAppendTicketMessage = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ ticketId, body }: { ticketId: number; body: TicketMessageCreate }) =>
      appendMessageApiV1TicketsTicketIdMessagesPost(ticketId, body),
    { ...o, invalidates: ["tickets"] },
  );
export const useCloseTicket = (o?: { onSuccess?: () => void }) =>
  useApiMutation((ticketId: number) => closeTicketApiV1TicketsTicketIdClosePost(ticketId), {
    ...o,
    invalidates: ["tickets"],
  });
