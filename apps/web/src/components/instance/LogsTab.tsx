/** 日志 Tab(实例详情 / 服务详情共用):subject 决定打哪个端点;viewable 由调用方按状态算。 */

import { POLL } from "@superdl/ui";
import { useMemo, useState } from "react";

import { useWorkloadLogs, type WorkloadSubject } from "../../api/queries";
import { LogsPanel } from "./LogsPanel";

export function LogsTab({ subject, viewable }: { subject: WorkloadSubject; viewable: boolean }) {
  const [tail, setTail] = useState(200);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const { data, error, refetch } = useWorkloadLogs(
    subject,
    { tail_lines: tail },
    // 页面不可见时间隔轮询自动暂停(未开 refetchIntervalInBackground)
    { enabled: viewable, refetchInterval: autoRefresh ? POLL.logs : false, retry: 0 },
  );
  const lines = useMemo(() => data?.lines ?? [], [data]);
  return (
    <LogsPanel
      viewable={viewable}
      lines={lines}
      truncated={data?.truncated}
      error={error}
      onRetry={() => void refetch()}
      tail={tail}
      onTail={setTail}
      autoRefresh={autoRefresh}
      onAutoRefresh={setAutoRefresh}
      downloadName={subject.kind === "instance" ? subject.uuid : subject.slug}
    />
  );
}
