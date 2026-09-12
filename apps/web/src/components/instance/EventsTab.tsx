/** 事件时间线 Tab(实例详情 / 服务详情共用):游标分页,不挂 refetchInterval;外层轮询检测到 status 迁移后失效事件查询(立即 + 3s 延迟各一次)。 */

import { flattenPages } from "@superdl/ui";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { keys } from "../../api/keys";
import { useWorkloadEventPages, type WorkloadSubject } from "../../api/queries";
import { EventsPanel } from "./EventsPanel";

export function EventsTab({ subject, status }: { subject: WorkloadSubject; status?: string }) {
  const queryClient = useQueryClient();
  const subjectKind = subject.kind;
  const subjectId = subject.kind === "instance" ? subject.uuid : subject.slug;
  const prevStatus = useRef(status);
  useEffect(() => {
    if (prevStatus.current === status) return;
    prevStatus.current = status;
    const key = subjectKind === "instance" ? keys.instances.events(subjectId) : keys.services.events(subjectId);
    void queryClient.invalidateQueries({ queryKey: key });
    const timer = setTimeout(() => void queryClient.invalidateQueries({ queryKey: key }), 3_000);
    return () => clearTimeout(timer);
  }, [status, subjectKind, subjectId, queryClient]);
  const { data, isLoading, isError, refetch, hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } =
    useWorkloadEventPages(subject);
  const events = useMemo(() => flattenPages(data), [data]);
  return (
    <EventsPanel
      events={events}
      isLoading={isLoading}
      isError={isError}
      onRetry={() => void refetch()}
      hasNextPage={hasNextPage}
      isFetchingNextPage={isFetchingNextPage}
      isFetchNextPageError={isFetchNextPageError}
      onLoadMore={() => void fetchNextPage()}
    />
  );
}
