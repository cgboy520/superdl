/** Logs tab (shared by instance / service details): subject picks the endpoint; viewable is computed by the caller from the status. */

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
