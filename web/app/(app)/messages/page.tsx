"use client";

import { Inbox } from "lucide-react";

import { NoticeList } from "@/components/ops";
import { Loading, Notice, PageHeader, Panel } from "@/components/ui";
import { api } from "@/lib/api";
import type { Notice as NoticeT } from "@/lib/types";
import { useLoad } from "@/lib/use-load";

export default function MessagesPage() {
  const { data, error } = useLoad(() => api<{ notices: NoticeT[] }>("/api/notices?limit=30"));
  return (
    <div className="animate-rise">
      <PageHeader title="Messages" lede="Callout offers and roster changes sent to you, newest first." />
      <Panel flush title="Inbox" icon={<Inbox size={15} />} action={data ? `${data.notices.length} latest` : undefined}>
        {error ? <div className="p-4"><Notice tone="error">{error}</Notice></div> : !data ? <Loading /> : <NoticeList notices={data.notices} />}
      </Panel>
    </div>
  );
}
