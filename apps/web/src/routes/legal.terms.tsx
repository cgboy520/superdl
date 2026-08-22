/** 用户协议(公开)。模板文案 —— 正式上线前须经法务审定后替换。英文界面暂显示中文文本(待法务译本)。 */

import { createFileRoute, Link } from "@tanstack/react-router";
import { Alert, Typography } from "antd";

import { AppTopBar } from "../components/layout/AppTopBar";
import { SiteFooter } from "../components/layout/SiteFooter";

export const Route = createFileRoute("/legal/terms")({
  component: TermsPage,
});

const SECTIONS: { title: string; body: string }[] = [
  {
    title: "一、服务说明",
    body: "SuperDL(下称「本平台」)向用户提供 GPU 容器实例租赁与配套存储服务,按量计费、按秒累计、账单可自查。实例创建、计费起止均以平台实例事件流水为准。",
  },
  {
    title: "二、账号与实名",
    body: "用户须以本人手机号注册,并根据《网络安全法》要求在使用付费功能前完成实名认证。账号仅限本人使用,因转借、出售账号造成的损失由用户自行承担。",
  },
  {
    title: "三、使用限制",
    body: "禁止利用本平台从事任何违法活动;明确禁止虚拟货币挖矿、网络攻击、流量代理等行为,一经发现平台有权立即停止服务并冻结账号,情节严重的将报告有关部门。",
  },
  {
    title: "四、计费与退款",
    body: "实例按秒累计、按小时出账;关机即停 GPU 计费,数据盘按日计费。创建失败不产生费用。余额不足时实例将自动关机并进入冻结与回收流程,具体参数以「费用中心」公示为准。",
  },
  {
    title: "五、数据与备份",
    body: "数据盘在实例释放后独立保留;欠费超过公示宽限期后平台有权回收并删除数据。用户应自行对重要数据保留副本,平台对因用户欠费回收导致的数据损失不承担责任。",
  },
  {
    title: "六、服务变更与终止",
    body: "平台因维护、升级需要暂停服务的,将提前公告。用户可随时释放实例并申请余额处理。本协议未尽事宜以平台公告为准。",
  },
];

function TermsPage() {
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <AppTopBar variant="public" />
      <div style={{ flex: 1, maxWidth: 800, width: "100%", margin: "0 auto", padding: "48px 24px" }}>
        <Alert
          type="warning"
          showIcon
          title="本页为协议模板,正式上线前须经法务审定后替换。"
          style={{ marginBottom: 24 }}
        />
        <Typography.Title level={2}>SuperDL 用户协议</Typography.Title>
        <Typography.Paragraph type="secondary">版本 v0.1(草案) · 2026-08-19</Typography.Paragraph>
        {SECTIONS.map((s) => (
          <div key={s.title} style={{ marginBottom: 20 }}>
            <Typography.Title level={4}>{s.title}</Typography.Title>
            <Typography.Paragraph>{s.body}</Typography.Paragraph>
          </div>
        ))}
        <Typography.Paragraph>
          <Link to="/legal/privacy">《隐私政策》</Link> · <Link to="/">返回首页</Link>
        </Typography.Paragraph>
      </div>
      <SiteFooter />
    </div>
  );
}
