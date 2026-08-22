/** 隐私政策(公开)。模板文案 —— 正式上线前须经法务审定后替换(PIPL 合规)。英文界面暂显示中文文本(待法务译本)。 */

import { createFileRoute, Link } from "@tanstack/react-router";
import { Alert, Typography } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";

export const Route = createFileRoute("/legal/privacy")({
  component: PrivacyPage,
});

const SECTIONS: { title: string; body: string }[] = [
  {
    title: "一、我们收集的信息",
    body: "注册与登录:手机号、登录 IP、设备信息;实名认证:姓名与身份证号(仅用于三要素核验,身份证号只保存脱敏形态,不存储原文);服务使用:SSH 公钥、实例操作记录、账单与资金流水。",
  },
  {
    title: "二、信息的使用",
    body: "上述信息仅用于:身份核验与账号安全(登录风控、验证码)、计费与开票、依法配合监管要求。我们不会将您的个人信息用于本政策载明目的之外的用途,不向第三方出售个人信息。",
  },
  {
    title: "三、信息的存储与保护",
    body: "数据存储于中华人民共和国境内。密码以 bcrypt 加盐哈希存储;身份证号仅存脱敏串;全部管理操作留存审计日志。我们采取访问控制、传输加密(TLS)等措施保护您的信息。",
  },
  {
    title: "四、信息的保留与删除",
    body: "账号注销后,法律法规要求留存的日志与交易记录(如资金流水)将按法定期限保留,其余个人信息将删除或匿名化。验证码等临时数据在过期后定期清除。",
  },
  {
    title: "五、您的权利",
    body: "您可以查询、更正个人信息,设置余额预警阈值,或联系平台申请注销账号。对个人信息处理有异议的,可通过页脚联系方式与我们联系。",
  },
];

function PrivacyPage() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="public" />
      <div style={{ flex: 1, maxWidth: 800, width: "100%", margin: "0 auto", padding: "48px 24px" }}>
        <Alert
          type="warning"
          showIcon
          title="本页为政策模板,正式上线前须经法务审定后替换。"
          style={{ marginBottom: 24 }}
        />
        <Typography.Title level={2}>SuperDL 隐私政策</Typography.Title>
        <Typography.Paragraph type="secondary">版本 v0.1(草案) · 2026-08-19</Typography.Paragraph>
        {SECTIONS.map((s) => (
          <div key={s.title} style={{ marginBottom: 20 }}>
            <Typography.Title level={4}>{s.title}</Typography.Title>
            <Typography.Paragraph>{s.body}</Typography.Paragraph>
          </div>
        ))}
        <Typography.Paragraph>
          <Link to="/legal/terms">《用户协议》</Link> · <Link to="/">返回首页</Link>
        </Typography.Paragraph>
      </div>
      <SiteFooter />
    </div>
  );
}
