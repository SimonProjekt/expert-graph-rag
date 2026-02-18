import type { HealthResponse, ClearanceLevel } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Activity, Database, FlaskConical, Shield } from "lucide-react";
import { Link } from "react-router-dom";

interface TopNavProps {
  health: HealthResponse | null;
  clearance: ClearanceLevel;
  onClearanceChange: (c: ClearanceLevel) => void;
}

export function TopNav({ health, clearance, onClearanceChange }: TopNavProps) {
  return (
    <header className="sticky top-0 z-50 flex h-14 items-center justify-between border-b glass-panel px-5">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10">
          <Database className="h-4 w-4 text-primary" />
        </div>
        <div>
        <h1 className="text-sm font-bold tracking-tight">
          IntelliGraph
        </h1>
        <p className="text-[10px] text-muted-foreground leading-none">Enterprise Knowledge Platform</p>
        </div>
        {health?.demo_mode && (
          <Badge variant="secondary" className="text-[9px] uppercase tracking-wider ml-1 bg-warning/10 text-warning border-warning/20">
            Sandbox
          </Badge>
        )}
      </div>

      <div className="flex items-center gap-4">
        <Link
          to="/stitch"
          className="hidden rounded-md border border-border/70 px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted/70 hover:text-foreground sm:inline-flex"
        >
          Stitch Screens
        </Link>

        <div className="hidden items-center gap-3 sm:flex">
          <StatusChip ok={health?.llm_available} label="LLM" icon={<FlaskConical className="h-3 w-3" />} />
          <StatusChip ok={health?.openalex_available} label="OpenAlex" icon={<Database className="h-3 w-3" />} />
        </div>

        <div className="h-5 w-px bg-border hidden sm:block" />

        <Select
          value={clearance}
          onValueChange={(v) => onClearanceChange(v as ClearanceLevel)}
        >
          <SelectTrigger className="h-8 w-[150px] text-xs gap-1.5 border-border/60">
            <Shield className="h-3 w-3 text-muted-foreground" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="PUBLIC">Public Access</SelectItem>
            <SelectItem value="INTERNAL">Internal Only</SelectItem>
            <SelectItem value="CONFIDENTIAL">Confidential</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </header>
  );
}

function StatusChip({ ok, label, icon }: { ok?: boolean; label: string; icon: React.ReactNode }) {
  const color = ok === true
    ? "bg-success/10 text-success border-success/20"
    : ok === false
    ? "bg-destructive/10 text-destructive border-destructive/20"
    : "bg-muted text-muted-foreground border-border";

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-medium ${color}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${
        ok === true ? "bg-success" : ok === false ? "bg-destructive" : "bg-muted-foreground"
      }`} />
      {icon}
      {label}
    </span>
  );
}
