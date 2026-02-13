import { Search, Database } from "lucide-react";

interface EmptyStateProps {
  message?: string;
}

export function EmptyState({
  message = "Enter a query to search papers, explore experts, and ask questions.",
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/5 border border-primary/10">
        <Database className="h-7 w-7 text-primary/40" />
      </div>
      <p className="max-w-xs text-sm text-muted-foreground leading-relaxed">{message}</p>
    </div>
  );
}
