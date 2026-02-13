import { useState } from "react";
import type { PaperResult } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { ScoreBar } from "@/components/shared/ScoreBar";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronDown, ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";

interface PaperCardProps {
  paper: PaperResult;
}

export function PaperCard({ paper }: PaperCardProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="card-elevated p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-sm font-semibold leading-snug">{paper.title}</h3>
        <span className="shrink-0 rounded-md bg-primary/10 px-2.5 py-1 text-xs font-bold text-primary tabular-nums glow-border">
          {paper.relevance_score.toFixed(2)}
        </span>
      </div>

      <p className="text-xs text-muted-foreground leading-relaxed">
        {paper.snippet}
      </p>

      <div className="flex flex-wrap gap-1.5">
        {paper.authors.map((a) => (
          <Badge key={a} variant="secondary" className="text-[10px] font-medium">
            {a}
          </Badge>
        ))}
        {paper.topics.map((t) => (
          <Badge key={t} variant="outline" className="text-[10px] border-primary/20 text-primary">
            {t}
          </Badge>
        ))}
      </div>

      <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
        <span>{paper.published_date}</span>
        <span className="h-0.5 w-0.5 rounded-full bg-muted-foreground" />
        <span className="flex items-center gap-1">
          <ExternalLink className="h-2.5 w-2.5" />
          {paper.source}
        </span>
      </div>

      <div className="space-y-1.5 pt-1">
        <p className="section-label">Score Breakdown</p>
        <ScoreBar label="Semantic" value={paper.score_breakdown.semantic_relevance} />
        <ScoreBar label="Authority" value={paper.score_breakdown.graph_authority} />
        <ScoreBar label="Centrality" value={paper.score_breakdown.graph_centrality} />
      </div>

      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex items-center gap-1.5 text-xs text-primary font-medium hover:text-primary/80 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded">
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform duration-200",
              open && "rotate-180"
            )}
          />
          Why this paper?
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-2 text-xs text-muted-foreground space-y-2">
          <p className="leading-relaxed">{paper.why_matched}</p>
          {paper.graph_path.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap font-mono text-[10px]">
              <span className="section-label">Path:</span>
              {paper.graph_path.map((node, i) => (
                <span key={i} className="flex items-center gap-1">
                  <span className="rounded bg-primary/10 px-1.5 py-0.5 text-primary">{node}</span>
                  {i < paper.graph_path.length - 1 && <span className="text-muted-foreground">→</span>}
                </span>
              ))}
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
