/** 容器日志面板(纯展示):末 N 行 + 关键词过滤 + 换行开关 + 自动刷新开关 + 贴底跟随 + 下载。数据与 tail / 自动刷新状态由调用方持有;实例详情页与服务详情页共用。 */

import { SearchOutlined } from "@ant-design/icons";
import { controlWidth, fontSize, space } from "@superdl/ui";
import { DataErrorAlert } from "@superdl/ui/components";
import { Alert, Button, Input, Select, Space, Switch, theme, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export const LOG_TAIL_OPTIONS = [100, 200, 500, 1000];

export function LogsPanel({
  viewable,
  lines,
  truncated,
  error,
  onRetry,
  tail,
  onTail,
  autoRefresh,
  onAutoRefresh,
  downloadName,
}: {
  /** 为假时渲染说明,不发请求 */
  viewable: boolean;
  lines: string[];
  truncated?: boolean;
  error: unknown;
  onRetry: () => void;
  tail: number;
  onTail: (n: number) => void;
  autoRefresh: boolean;
  onAutoRefresh: (on: boolean) => void;
  /** 下载文件名(不含 .log) */
  downloadName: string;
}) {
  const { t } = useTranslation();
  const { token } = theme.useToken();
  const scrollRef = useRef<HTMLDivElement>(null);
  // 贴底判定:距底 ≤40px;上滚即暂停跟随,新行计数在浮动钮上
  const [pinned, setPinned] = useState(true);
  const [newCount, setNewCount] = useState(0);
  // 关键词过滤只作用于已拉取的末 N 行;换行开关默认开
  const [keyword, setKeyword] = useState("");
  const [wrap, setWrap] = useState(true);
  const kw = keyword.trim().toLowerCase();
  const visible = kw ? lines.filter((l) => l.toLowerCase().includes(kw)) : lines;

  const scrollToBottom = () => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    setPinned(true);
    setNewCount(0);
  };

  // 渲染期派生:非贴底时新行数累计到浮动钮;tail 档变化整体替换内容并复位贴底
  const [prevLen, setPrevLen] = useState(lines.length);
  if (lines.length !== prevLen) {
    const grew = lines.length - prevLen;
    setPrevLen(lines.length);
    if (!pinned && grew > 0) setNewCount((n) => n + grew);
  }
  const [prevTail, setPrevTail] = useState(tail);
  if (tail !== prevTail) {
    setPrevTail(tail);
    setPrevLen(0);
    setPinned(true);
    setNewCount(0);
  }

  // 自动跟随:仅在贴底时滚到底部(纯 DOM 操作)
  useEffect(() => {
    if (!pinned) return;
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines, pinned]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 40;
    setPinned(atBottom);
    if (atBottom) setNewCount(0);
  };

  if (!viewable) {
    return <Alert type="info" showIcon title={t("instances.logsNotRunning")} />;
  }

  const download = () => {
    const blob = new Blob([`${lines.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${downloadName}.log`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Space orientation="vertical" size={space.md} style={{ width: "100%" }}>
      <Space wrap size={space.md}>
        <Typography.Text type="secondary">{t("instances.logsTail")}</Typography.Text>
        <Select
          value={tail}
          style={{ width: 104 }}
          options={LOG_TAIL_OPTIONS.map((n) => ({ value: n, label: String(n) }))}
          onChange={onTail}
        />
        {/* 文字包在 label 里:点文字也切换 */}
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <Switch checked={autoRefresh} onChange={onAutoRefresh} aria-label={t("instances.logsAutoRefresh")} />
          <Typography.Text>{t("instances.logsAutoRefresh")}</Typography.Text>
        </label>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <Switch checked={wrap} onChange={setWrap} aria-label={t("instances.logsWrap")} />
          <Typography.Text>{t("instances.logsWrap")}</Typography.Text>
        </label>
        <Input
          allowClear
          size="small"
          prefix={<SearchOutlined />}
          placeholder={t("instances.logsFilter")}
          aria-label={t("instances.logsFilter")}
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: controlWidth.md }}
        />
        {kw && (
          <Typography.Text type="secondary" style={{ fontSize: fontSize.caption }}>
            {t("instances.logsFilterCount", { shown: visible.length, total: lines.length })}
          </Typography.Text>
        )}
        <Button size="small" disabled={lines.length === 0} onClick={download}>
          {t("instances.logsDownload")}
        </Button>
        {truncated && (
          <Typography.Text type="secondary">{t("instances.logsTruncatedNote", { lines: tail })}</Typography.Text>
        )}
      </Space>
      {error ? (
        <DataErrorAlert onRetry={onRetry} />
      ) : (
        <div style={{ position: "relative" }}>
          <div
            ref={scrollRef}
            onScroll={onScroll}
            style={{
              height: 420,
              overflow: "auto",
              padding: "8px 12px",
              background: token.colorFillQuaternary,
              border: `1px solid ${token.colorBorderSecondary}`,
              borderRadius: token.borderRadius,
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
              fontSize: fontSize.caption,
              lineHeight: 1.7,
              whiteSpace: wrap ? "pre-wrap" : "pre",
              wordBreak: wrap ? "break-all" : "normal",
            }}
          >
            {lines.length === 0 ? (
              <Typography.Text type="secondary">{t("instances.logsEmpty")}</Typography.Text>
            ) : visible.length === 0 ? (
              <Typography.Text type="secondary">{t("instances.logsFilterNoMatch")}</Typography.Text>
            ) : (
              // key 用行内容 + 序号:滚动追加时旧行不重渲染
              visible.map((line, i) => <div key={`${i}:${line}`}>{line}</div>)
            )}
          </div>
          {!pinned && (
            <Button
              size="small"
              type="primary"
              onClick={scrollToBottom}
              style={{ position: "absolute", right: 16, bottom: 12, boxShadow: token.boxShadow }}
            >
              {newCount > 0 ? t("instances.logsBackToBottomNew", { count: newCount }) : t("instances.logsBackToBottom")}
            </Button>
          )}
        </div>
      )}
    </Space>
  );
}
