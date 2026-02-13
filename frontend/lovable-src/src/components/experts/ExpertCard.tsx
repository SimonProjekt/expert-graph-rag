import type { Expert } from "@/types/api";
import { ScoreBar } from "@/components/shared/ScoreBar";
import { User } from "lucide-react";

interface ExpertCardProps {
  expert: Expert;
  rank: number;
}

export function ExpertCard({ expert, rank }: ExpertCardProps) {
  return (
    <div className="card-elevated p-5 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-xs font-bold text-primary-foreground">
            {rank}
          </span>
          <div>
            <span className="text-sm font-semibold">{expert.name}</span>
            {expert.affiliation && (
              <p className="text-[10px] text-muted-foreground mt-0.5">{expert.affiliation}</p>
            )}
          </div>
        </div>
        <span className="shrink-0 rounded-md bg-primary/10 px-2.5 py-1 text-xs font-bold text-primary tabular-nums glow-border">
          {expert.score.toFixed(2)}
        </span>
      </div>

      <p className="text-xs text-muted-foreground leading-relaxed">
        {expert.explanation}
      </p>

      <div className="space-y-1.5 pt-1">
        <p className="section-label">Score Breakdown</p>
        <ScoreBar label="Semantic" value={expert.score_breakdown.semantic_relevance} />
        <ScoreBar label="Authority" value={expert.score_breakdown.graph_authority} />
        <ScoreBar label="Centrality" value={expert.score_breakdown.graph_centrality} />
      </div>
    </div>
  );
}
