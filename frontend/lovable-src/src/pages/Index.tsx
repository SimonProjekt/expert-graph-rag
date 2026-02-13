import { useState } from "react";
import type { ClearanceLevel } from "@/types/api";
import { useHealth } from "@/hooks/use-health";
import { useDebouncedSearch } from "@/hooks/use-debounced-search";
import { useExperts } from "@/hooks/use-experts";
import { TopNav } from "@/components/layout/TopNav";
import { ControlPanel } from "@/components/layout/ControlPanel";
import { PapersTab } from "@/components/papers/PapersTab";
import { ExpertsTab } from "@/components/experts/ExpertsTab";
import { GraphTab } from "@/components/graph/GraphTab";
import { AskTab } from "@/components/ask/AskTab";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { FileText, Users, Share2, MessageSquare } from "lucide-react";

const Index = () => {
  const [query, setQuery] = useState("");
  const [clearance, setClearance] = useState<ClearanceLevel>("PUBLIC");
  const health = useHealth();
  const search = useDebouncedSearch(query, clearance);
  const experts = useExperts(query, clearance);
  const hasQuery = query.trim().length > 0;

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <TopNav health={health} clearance={clearance} onClearanceChange={setClearance} />

      <div className="flex flex-1 flex-col lg:flex-row">
        <ControlPanel
          query={query}
          onQueryChange={setQuery}
          resultCount={search.data?.result_count}
          tookMs={search.data?.took_ms}
          hiddenCount={search.data?.hidden_count}
          loading={search.loading}
        />

        <main className="flex-1 overflow-auto p-4 lg:p-6">
          <Tabs defaultValue="papers" className="w-full">
            <TabsList className="mb-5 glass-panel p-1 h-auto">
              <TabsTrigger value="papers" className="gap-1.5 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
                <FileText className="h-3.5 w-3.5" /> Papers
              </TabsTrigger>
              <TabsTrigger value="experts" className="gap-1.5 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
                <Users className="h-3.5 w-3.5" /> Experts
              </TabsTrigger>
              <TabsTrigger value="graph" className="gap-1.5 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
                <Share2 className="h-3.5 w-3.5" /> Graph
              </TabsTrigger>
              <TabsTrigger value="ask" className="gap-1.5 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
                <MessageSquare className="h-3.5 w-3.5" /> Ask
              </TabsTrigger>
            </TabsList>

            <TabsContent value="papers">
              <PapersTab
                data={search.data}
                loading={search.loading}
                error={search.error}
                onRetry={search.retry}
                hasQuery={hasQuery}
              />
            </TabsContent>

            <TabsContent value="experts">
              <ExpertsTab
                data={experts.data}
                loading={experts.loading}
                error={experts.error}
                onRetry={experts.retry}
                hasQuery={hasQuery}
              />
            </TabsContent>

            <TabsContent value="graph">
              <GraphTab data={search.data} hasQuery={hasQuery} />
            </TabsContent>

            <TabsContent value="ask">
              <AskTab clearance={clearance} />
            </TabsContent>
          </Tabs>
        </main>
      </div>
    </div>
  );
};

export default Index;
