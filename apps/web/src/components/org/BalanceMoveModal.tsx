/** Tenant admins move balance: allocate from their own available balance to a member, or reclaim from an account the tenant created (up to its available balance). Moved money can only be spent: the recipient cannot refund or invoice it. */

import type { MemberOut } from "@superdl/api-client";
import { compareAmounts, idemKeyOf, space, useFormat } from "@superdl/ui";
import { Alert, App, InputNumber, Modal, Segmented, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useMoveMemberBalance } from "../../api/mutations";
import { useWallet } from "../../api/queries";
import { Field } from "../Field";

type Kind = "allocate" | "reclaim";

export function BalanceMoveModal({
  member,
  memberBalance,
  onClose,
}: {
  member: MemberOut | null;
  /** The member's available balance (finance view) when known; bounds a reclaim. */
  memberBalance?: string | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { formatMoney, minorUnits } = useFormat();
  const { data: wallet } = useWallet();
  const [kind, setKind] = useState<Kind>("allocate");
  const [amount, setAmount] = useState<string | null>(null);
  const [seq] = useState(() => crypto.randomUUID());
  const move = useMoveMemberBalance({
    onSuccess: (out) => {
      message.success(
        out.kind === "allocate"
          ? t("org.balance.allocated", { amount: formatMoney(out.amount), name: out.member_label })
          : t("org.balance.reclaimed", { amount: formatMoney(out.amount), name: out.member_label }),
      );
      setAmount(null);
      onClose();
    },
  });
  const canReclaim = Boolean(member?.managed);
  const effectiveKind: Kind = canReclaim ? kind : "allocate";
  const limit = effectiveKind === "allocate" ? wallet?.balance : (memberBalance ?? undefined);
  const over = amount != null && limit != null && compareAmounts(amount, limit) > 0;
  const valid = amount != null && Number(amount) > 0 && !over;
  return (
    <Modal
      title={t("org.balance.title", { name: member?.label ?? "" })}
      open={member != null}
      onCancel={onClose}
      onOk={() =>
        member &&
        amount &&
        move.mutate({
          memberId: member.member_id,
          body: { kind: effectiveKind, amount },
          idempotencyKey: idemKeyOf("org-balance", [seq, member.member_id, effectiveKind, amount]),
        })
      }
      okText={effectiveKind === "allocate" ? t("org.balance.allocate") : t("org.balance.reclaim")}
      okButtonProps={{ disabled: !valid }}
      confirmLoading={move.isPending}
      destroyOnHidden
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        {canReclaim && (
          <Segmented<Kind>
            value={effectiveKind}
            onChange={setKind}
            options={[
              { value: "allocate", label: t("org.balance.allocate") },
              { value: "reclaim", label: t("org.balance.reclaim") },
            ]}
          />
        )}
        <Field
          label={t("org.balance.amount")}
          required
          error={over ? t("org.balance.overLimit") : undefined}
          hint={
            limit != null
              ? effectiveKind === "allocate"
                ? t("org.balance.yourBalance", { amount: formatMoney(limit) })
                : t("org.balance.theirBalance", { amount: formatMoney(limit) })
              : undefined
          }
        >
          <InputNumber<string>
            stringMode
            min="0"
            step={minorUnits === 0 ? "1" : "0.01"}
            precision={minorUnits === 0 ? 0 : 2}
            value={amount}
            onChange={(v) => setAmount(v)}
            style={{ width: 220 }}
            aria-label={t("org.balance.amount")}
          />
        </Field>
        <Typography.Text type="secondary">
          {effectiveKind === "allocate" ? t("org.balance.allocateNote") : t("org.balance.reclaimNote")}
        </Typography.Text>
        {effectiveKind === "allocate" && <Alert type="info" showIcon title={t("org.balance.spendOnly")} />}
      </Space>
    </Modal>
  );
}
