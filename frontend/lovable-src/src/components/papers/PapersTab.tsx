import { useState, useMemo } from "react";
import type { SearchResponse } from "@/types/api";
import { PaperCard } from "./PaperCard";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { PapersSkeleton } from "@/components/shared/LoadingSkeletons";
import { Button } from "@/components/ui/button";
import { ArrowUpDown } from "lucide-react";

interface PapersTabProps {
  data: SearchResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  hasQuery: boolean;
}

type SortMode = "relevance" | "recency";

export function PapersTab({ data, loading, error, onRetry, hasQuery }: PapersTabProps) {
  const [sort, setSort] = useState<SortMode>("relevance");

  const sorted = useMemo(() => {
    if (!data?.results) return [];
    const copy = [...data.results];
    if (sort === "recency") {
      copy.sort(
        (a, b) =>
          new Date(b.published_date).getTime() -
          new Date(a.published_date).getTime()
      );
    }
    return copy;
  }, [data, sort]);

  if (!hasQuery) return <EmptyState />;
  if (loading) return <PapersSkeleton />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!data || data.results.length === 0)
    return <EmptyState message="No papers found for this query." />;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button
          variant="ghost"
          size="sm"
          className="text-xs gap-1"
          onClick={() => setSort(sort === "relevance" ? "recency" : "relevance")}
        >
          <ArrowUpDown className="h-3 w-3" />
          {sort === "relevance" ? "Sort by recency" : "Sort by relevance"}
        </Button>
      </div>
      {sorted.map((p) => (
        <PaperCard key={p.paper_id} paper={p} />
      ))}
    </div>
  );
}
