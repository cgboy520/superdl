/** 写操作封装:生成 fetcher + useMutation;成功后按各 hook 声明的 invalidates 失效查询。 */

import type { ApiError, SshKeyOut } from "@superdl/api-client";
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
  createRevisionApiV1ServicesSlugRevisionsPost,
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
  ServiceRevisionCreate,
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
  /** 成功后失效的查询键前缀;空数组 = 不失效 */
  invalidates: readonly string[];
}
/** 页面侧可传的项;失效域由各 hook 声明 */
type CallerOpts<TData = unknown> = Omit<MutationOpts<TData>, "invalidates">;

export function useApiMutation<TVars = void, TData = unknown>(
  fn: (vars: TVars) => Promise<TData>,
  opts: MutationOpts<TData>,
) {
  const queryClient = useQueryClient();
  const { message } = App.useApp();
  const errText = useApiErrorText();
  return useMutation<TData, ApiError, TVars>({
    mutationFn: fn,
    onSuccess: (data) => {
      // 不 await refetch
      for (const key of opts.invalidates) void queryClient.invalidateQueries({ queryKey: [key] });
      opts.onSuccess?.(data);
    },
    onError: (err) => {
      if (!opts.silentError) message.error(errText(err));
    },
  });
}

export const useSendSmsCode = (o?: { onSuccess?: () => void; silentError?: boolean }) =>
  useApiMutation((body: SmsCodeRequest) => sendSmsCodeApiV1AuthSmsCodePost(body), { ...o, invalidates: [] });
export const useRegister = (o?: CallerOpts) =>
  useApiMutation((body: RegisterRequest) => registerApiV1AuthRegisterPost(body), { ...o, invalidates: [] });
export const useLogin = (o?: CallerOpts) =>
  useApiMutation((body: LoginRequest) => loginApiV1AuthLoginPost(body), { ...o, invalidates: [] });
/** 设置/修改/找回密码(手机号 + 验证码);返回新 token 对。 */
export const useResetPassword = (o?: CallerOpts) =>
  useApiMutation((body: PasswordResetRequest) => resetPasswordApiV1AuthPasswordResetPost(body), {
    ...o,
    invalidates: [],
  });

/** 登出:current = 撤销本设备 refresh token;all = 撤销该账号全部会话。之后清本地并整页刷新,请求失败不阻断。 */
export function useLogout() {
  return useCallback(async (scope: "current" | "all" = "current") => {
    try {
      if (scope === "all") {
        await logoutAllApiV1AuthLogoutAllPost();
      } else {
        // cookie 路径必须带 CSRF 头,不带请求体
        await logoutApiV1AuthLogoutPost({ headers: { "X-Requested-With": "fetch" } });
      }
    } catch {
      // 登出尽力而为
    }
    authStore.getState().logout();
    // 整页刷新清全部内存态
    window.location.assign("/login");
  }, []);
}

// instances:失效实例域与钱包余额
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
    ({ uuid, name }: { uuid: string; name: string }) => renameInstanceApiV1InstancesUuidPatch(uuid, { name }),
    { invalidates: ["instances"] },
  );
/** 包周期续费带幂等键;键由 modal 每次打开生成,关掉重开才换。 */
export const useRenewInstance = (uuid: string, o?: CallerOpts<RenewOut>) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceRenew; idempotencyKey: string }) =>
      renewInstanceApiV1InstancesUuidRenewPost(uuid, body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** 按量转包周期:入参/响应与续费同形,起点从现在起算;失效面含账单。 */
export const useSubscribeInstance = (uuid: string, o?: CallerOpts<RenewOut>) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InstanceRenew; idempotencyKey: string }) =>
      subscribeInstanceApiV1InstancesUuidSubscribePost(uuid, body, {
        "Idempotency-Key": idempotencyKey,
      }),
    { ...o, invalidates: [...INSTANCE_INVALIDATES] },
  );
/** 竞价转按量:只翻 market 与单价,不带幂等键;失效面含账单。 */
export const useConvertToOnDemand = (uuid: string, o?: CallerOpts<InstanceOut>) =>
  useApiMutation(() => convertToOnDemandApiV1InstancesUuidToOnDemandPost(uuid), {
    ...o,
    invalidates: [...INSTANCE_INVALIDATES],
  });
/** 自动续费开关:只改订阅行,失效面只有实例域。 */
export const useSetAutoRenew = (uuid: string, o?: CallerOpts<InstanceOut>) =>
  useApiMutation((enabled: boolean) => setAutoRenewApiV1InstancesUuidAutoRenewPost(uuid, { enabled }), {
    ...o,
    invalidates: ["instances"],
  });
export const useResetJupyterToken = () =>
  useApiMutation((uuid: string) => resetJupyterTokenApiV1InstancesUuidResetJupyterTokenPost(uuid), {
    invalidates: ["instances"],
  });

// 在线服务:失效服务域与钱包余额
const SERVICE_INVALIDATES = ["services", "wallet", "bills", "bill-daily-summary"] as const;
/** 部署服务:幂等键按表单快照派生。 */
export const useCreateService = (o?: { onSuccess?: (d: ServiceOut) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: ServiceCreate; idempotencyKey: string }) =>
      createServiceApiV1ServicesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...SERVICE_INVALIDATES, "skus", "disks"] },
  );
/** 版本更新(重建):幂等键按表单快照派生。 */
export const useCreateRevision = (slug: string, o?: { onSuccess?: (d: ServiceOut) => void; silentError?: boolean }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: ServiceRevisionCreate; idempotencyKey: string }) =>
      createRevisionApiV1ServicesSlugRevisionsPost(slug, body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: [...SERVICE_INVALIDATES, "skus"] },
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
/** 改名 / 鉴权开关:失效面只有服务域。 */
export const useUpdateService = (slug: string, o?: CallerOpts<ServiceOut>) =>
  useApiMutation((body: ServicePatch) => patchServiceApiV1ServicesSlugPatch(slug, body), {
    ...o,
    invalidates: ["services"],
  });
/** 新建服务访问 Key:明文 key 只在响应露面一次,只交给一次性展示的成功态,禁止入缓存或日志。 */
export const useCreateServiceApiKey = (slug: string, o?: CallerOpts<ApiKeyCreateOut>) =>
  useApiMutation((name: string) => createApiKeyApiV1ServicesSlugApiKeysPost(slug, { name }), {
    ...o,
    invalidates: ["services"],
  });
/** 吊销 Key:写 revoked_at 不删行。 */
export const useRevokeServiceApiKey = (slug: string, o?: CallerOpts) =>
  useApiMutation((keyId: number) => revokeApiKeyApiV1ServicesSlugApiKeysKeyIdDelete(slug, keyId), {
    ...o,
    invalidates: ["services"],
  });

export const useCreateRecharge = (o?: { onSuccess?: (d: unknown) => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RechargeCreate; idempotencyKey: string }) =>
      createRechargeApiV1WalletRechargesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["wallet", "ledger"] },
  );
export const useMockPay = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    (vars: { order_no: string; amount: string }) => mockWebhookApiV1WebhooksMockPost({ body: JSON.stringify(vars) }),
    { ...o, invalidates: ["wallet", "recharge", "ledger"] },
  );
/** 申请退款:幂等键按表单快照派生。 */
export const useCreateRefund = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: RefundCreate; idempotencyKey: string }) =>
      createRefundApiV1WalletRefundsPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["refunds", "refundable-orders", "wallet", "ledger"] },
  );
/** 申请开票:金额由服务端按账期计算;幂等键重放返回既有单。 */
export const useCreateInvoice = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: InvoiceCreate; idempotencyKey: string }) =>
      createInvoiceApiV1BillingInvoicesPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["invoices", "invoice-eligible"] },
  );
export const useSubmitRealName = (o?: { onSuccess?: () => void }) =>
  useApiMutation((body: RealNameRequest) => submitRealNameApiV1MeRealNamePost(body), { ...o, invalidates: ["me"] });
export const useSetWarnThreshold = (o?: { onSuccess?: () => void }) =>
  useApiMutation((hours: number) => setWarnThresholdApiV1MeWarnThresholdPatch({ low_balance_warn_hours: hours }), {
    ...o,
    invalidates: ["me"],
  });

/** 申请注销:服务端按 (user_id, pending) 幂等。 */
export const useCreateDeletionRequest = (o?: { onSuccess?: () => void }) =>
  useApiMutation((body: DeletionRequestCreate) => createDeletionRequestApiV1MeDeletionRequestPost(body), {
    ...o,
    invalidates: ["deletion-request"],
  });
export const useCancelDeletionRequest = (o?: { onSuccess?: () => void }) =>
  useApiMutation(() => cancelDeletionRequestApiV1MeDeletionRequestCancelPost(), {
    ...o,
    invalidates: ["deletion-request"],
  });

/** 建盘带幂等键。 */
export const useCreateDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(
    ({ body, idempotencyKey }: { body: DiskCreate; idempotencyKey: string }) =>
      createDiskApiV1DisksPost(body, { "Idempotency-Key": idempotencyKey }),
    { ...o, invalidates: ["disks", "wallet"] },
  );
export const useExpandDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation(({ uuid, body }: { uuid: string; body: DiskExpand }) => expandDiskApiV1DisksUuidPatch(uuid, body), {
    ...o,
    invalidates: ["disks", "wallet"],
  });
export const useDeleteDisk = (o?: { onSuccess?: () => void }) =>
  useApiMutation((uuid: string) => deleteDiskApiV1DisksUuidDelete(uuid), {
    ...o,
    invalidates: ["disks", "wallet", "instances", "services"],
  });

export const useAddSshKey = (o?: { onSuccess?: (key: SshKeyOut) => void }) =>
  useApiMutation((body: { name: string; public_key: string }) => addSshKeyApiV1SshKeysPost(body), {
    ...o,
    invalidates: ["ssh-keys"],
  });
export const useDeleteSshKey = () =>
  useApiMutation((keyId: number) => deleteSshKeyApiV1SshKeysKeyIdDelete(keyId), {
    invalidates: ["ssh-keys"],
  });
export const useMarkNotificationRead = () =>
  useApiMutation((id: number) => markReadApiV1NotificationsNotificationIdReadPost(id), {
    invalidates: ["notifications"],
  });
export const useMarkAllNotificationsRead = () =>
  useApiMutation(() => markAllReadApiV1NotificationsReadAllPost(), {
    invalidates: ["notifications"],
  });

/** 新建工单:幂等键按表单快照派生。 */
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
