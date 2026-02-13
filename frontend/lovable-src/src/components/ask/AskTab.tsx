import { useState } from "react";
import type { ClearanceLevel } from "@/types/api";
import { useAsk } from "@/hooks/use-ask";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { AskSkeleton } from "@/components/shared/LoadingSkeletons";
import { ExpertCard } from "@/components/experts/ExpertCard";
import { Send, BookOpen, Users } from "lucide-react";

interface AskTabProps {
  clearance: ClearanceLevel;
}

export function AskTab({ clearance }: AskTabProps) {
  const [question, setQuestion] = useState("");
  const { data, loading, error, ask, retry } = useAsk(clearance);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    ask(question);
  };

  return (
    <div className="space-y-6">
      <form onSubmit={handleSubmit} className="flex gap-2">
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask a question about the research domain…"
          className="flex-1 bg-background/80 border-border/60"
          aria-label="Ask a question"
        />
        <Button type="submit" size="icon" disabled={loading || !question.trim()} className="shrink-0">
          <Send className="h-4 w-4" />
        </Button>
      </form>

      {loading && <AskSkeleton />}
      {error && <ErrorState message={error} onRetry={() => retry(question)} />}

      {data && (
        <div className="space-y-6">
          <div className="card-elevated p-5">
            <p className="section-label mb-3">Answer</p>
            <p className="text-sm leading-relaxed whitespace-pre-wrap">{data.answer}</p>
          </div>

          {data.citations.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <BookOpen className="h-3.5 w-3.5 text-primary" />
                <p className="section-label">Citations</p>
              </div>
              {data.citations.map((c) => (
                <div key={c.paper_id} className="card-elevated p-4 space-y-1">
                  <p className="text-xs font-semibold">{c.title}</p>
                  <p className="text-[10px] text-muted-foreground leading-relaxed">{c.snippet}</p>
                </div>
              ))}
            </div>
          )}

          {data.recommended_experts.length > 0 && (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Users className="h-3.5 w-3.5 text-primary" />
                <p className="section-label">Recommended Experts</p>
              </div>
              {data.recommended_experts.map((e, i) => (
                <ExpertCard key={e.expert_id} expert={e} rank={i + 1} />
              ))}
            </div>
          )}
        </div>
      )}

      {!loading && !error && !data && (
        <EmptyState message="Ask any question — answers are grounded in the paper corpus with full citations." />
      )}
    </div>
  );
}
