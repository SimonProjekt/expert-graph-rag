import type { ExpertsResponse } from "@/types/api";
import { ExpertCard } from "./ExpertCard";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { ExpertsSkeleton } from "@/components/shared/LoadingSkeletons";

interface ExpertsTabProps {
  data: ExpertsResponse | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  hasQuery: boolean;
}

export function ExpertsTab({ data, loading, error, onRetry, hasQuery }: ExpertsTabProps) {
  if (!hasQuery) return <EmptyState message="Search for a topic to discover experts." />;
  if (loading) return <ExpertsSkeleton />;
  if (error) return <ErrorState message={error} onRetry={onRetry} />;
  if (!data || data.experts.length === 0)
    return <EmptyState message="No experts found for this query." />;

  return (
    <div className="space-y-4">
      {data.experts.map((e, i) => (
        <ExpertCard key={e.expert_id} expert={e} rank={i + 1} />
      ))}
    </div>
  );
}
