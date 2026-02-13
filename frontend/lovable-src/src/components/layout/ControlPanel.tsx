import { Input } from "@/components/ui/input";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Search, ShieldAlert, Clock, Hash, Zap } from "lucide-react";

interface ControlPanelProps {
  query: string;
  onQueryChange: (q: string) => void;
  resultCount?: number;
  tookMs?: number;
  hiddenCount?: number;
  loading?: boolean;
}

export function ControlPanel({
  query,
  onQueryChange,
  resultCount,
  tookMs,
  hiddenCount,
  loading,
}: ControlPanelProps) {
  return (
    <aside className="flex w-full flex-col gap-4 border-b p-4 lg:w-72 lg:shrink-0 lg:border-b-0 lg:border-r lg:h-[calc(100vh-3.5rem)] bg-card/50">
      <div>
        <p className="section-label mb-2">Search Query</p>
        <div className="relative">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Search papers, experts…"
            className="pl-9 bg-background/80 border-border/60 focus:border-primary/40 focus:ring-primary/20"
            aria-label="Search query"
          />
        </div>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-xs text-primary animate-pulse">
          <Zap className="h-3 w-3" />
          Querying knowledge graph…
        </div>
      )}

      {resultCount !== undefined && !loading && (
        <div className="card-elevated p-3 space-y-2">
          <p className="section-label">Results</p>
          <div className="flex flex-wrap gap-3 text-xs text-foreground">
            <span className="flex items-center gap-1.5 font-medium">
              <Hash className="h-3 w-3 text-primary" /> {resultCount} results
            </span>
            {tookMs !== undefined && (
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <Clock className="h-3 w-3" /> {tookMs}ms
              </span>
            )}
          </div>
        </div>
      )}

      {(hiddenCount ?? 0) > 0 && (
        <Alert variant="destructive" className="text-xs border-destructive/30 bg-destructive/5">
          <ShieldAlert className="h-4 w-4" />
          <AlertDescription>
            {hiddenCount} result{hiddenCount! > 1 ? "s" : ""} redacted due to
            clearance level.
          </AlertDescription>
        </Alert>
      )}
    </aside>
  );
}
