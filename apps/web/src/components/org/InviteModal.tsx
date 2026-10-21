/** Invite by email: admins invite members, the owner also admins. The answer never tells whether an account exists. */

import { space } from "@superdl/ui";
import { App, Input, Modal, Segmented, Space, Typography } from "antd";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useInvite } from "../../api/mutations";
import { Field } from "../Field";

type Role = "member" | "admin";

export function InviteModal({ open, owner, onClose }: { open: boolean; owner: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [remark, setRemark] = useState("");
  const invite = useInvite({
    onSuccess: () => {
      message.success(t("org.invitations.sent"));
      setEmail("");
      setRole("member");
      setRemark("");
      onClose();
    },
  });
  const trimmed = email.trim();
  return (
    <Modal
      title={t("org.invitations.inviteTitle")}
      open={open}
      onCancel={onClose}
      onOk={() => invite.mutate({ email: trimmed, role, remark: remark.trim() || null })}
      okText={t("org.invitations.send")}
      okButtonProps={{ disabled: !trimmed.includes("@") }}
      confirmLoading={invite.isPending}
    >
      <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
        <Typography.Text type="secondary">{t("org.invitations.inviteHint")}</Typography.Text>
        <Field label={t("org.invitations.email")} required>
          <Input
            type="email"
            value={email}
            maxLength={254}
            onChange={(e) => setEmail(e.target.value)}
            aria-label={t("org.invitations.email")}
          />
        </Field>
        <Field label={t("org.invitations.role")} required>
          <div>
            <Segmented<Role>
              value={role}
              onChange={setRole}
              options={[
                { value: "member", label: t("org.role.member") },
                { value: "admin", label: t("org.role.admin"), disabled: !owner },
              ]}
            />
          </div>
        </Field>
        <Field label={t("org.members.remark")} hint={t("org.members.remarkHint")}>
          <Input
            value={remark}
            maxLength={64}
            onChange={(e) => setRemark(e.target.value)}
            aria-label={t("org.members.remark")}
          />
        </Field>
      </Space>
    </Modal>
  );
}
