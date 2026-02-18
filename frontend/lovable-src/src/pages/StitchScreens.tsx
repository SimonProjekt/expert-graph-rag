import { useEffect, useMemo, useState } from "react";

type StitchScreen = {
  id: string;
  zip_name: string;
  title: string;
  has_code: boolean;
  code_path: string;
  image_path: string;
};

function resolveAssetPath(path: string): string {
  const normalized = path.startsWith("/") ? path.slice(1) : path;
  if (typeof window !== "undefined" && window.location.pathname.startsWith("/app")) {
    return `/app/${normalized}`;
  }
  return `/${normalized}`;
}

const StitchScreens = () => {
  const [screens, setScreens] = useState<StitchScreen[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const loadManifest = async () => {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(resolveAssetPath("stitch-screens/manifest.json"), {
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`Failed to load manifest: HTTP ${response.status}`);
        }
        const payload = (await response.json()) as StitchScreen[];
        if (!cancelled) {
          setScreens(payload);
          setSelectedId(payload[0]?.id ?? "");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load stitch screens.");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    void loadManifest();
    return () => {
      cancelled = true;
    };
  }, []);

  const selected = useMemo(
    () => screens.find((item) => item.id === selectedId) ?? screens[0] ?? null,
    [screens, selectedId],
  );

  return (
    <div className="min-h-screen bg-background text-foreground p-4 lg:p-6">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold tracking-tight">Expert Finder UI</h1>
          <p className="text-sm text-muted-foreground">
            Stitch multi-screen UI imported from your ZIP exports.
          </p>
        </div>
      </header>

      {loading ? (
        <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">
          Loading imported screens...
        </div>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      {!loading && !error ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="rounded-lg border border-border bg-card p-3">
            <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Imported Screens ({screens.length})
            </h2>
            <div className="space-y-2">
              {screens.map((screen) => {
                const active = selected?.id === screen.id;
                return (
                  <button
                    key={screen.id}
                    type="button"
                    onClick={() => setSelectedId(screen.id)}
                    className={`w-full rounded-md border px-3 py-2 text-left transition-colors ${
                      active
                        ? "border-primary bg-primary/10"
                        : "border-border hover:bg-muted/60"
                    }`}
                  >
                    <p className="text-xs font-semibold">{screen.title}</p>
                    <p className="text-[11px] text-muted-foreground">{screen.zip_name}</p>
                  </button>
                );
              })}
            </div>
          </aside>

          <section className="space-y-4">
            {selected ? (
              <>
                <div className="rounded-lg border border-border bg-card p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">{selected.title}</h3>
                      <p className="text-xs text-muted-foreground">{selected.zip_name}</p>
                    </div>
                    <a
                      href={resolveAssetPath(selected.code_path)}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
                    >
                      Open Raw HTML
                    </a>
                  </div>
                  <div className="h-[560px] overflow-hidden rounded-md border border-border bg-black">
                    <iframe
                      title={selected.title}
                      src={resolveAssetPath(selected.code_path)}
                      className="h-full w-full"
                    />
                  </div>
                </div>

                <div className="rounded-lg border border-border bg-card p-4">
                  <h3 className="mb-2 text-sm font-semibold">Exported Screenshot</h3>
                  <img
                    src={resolveAssetPath(selected.image_path)}
                    alt={`${selected.title} screenshot`}
                    className="w-full rounded-md border border-border"
                    loading="lazy"
                  />
                </div>
              </>
            ) : (
              <div className="rounded-lg border border-border p-6 text-sm text-muted-foreground">
                No screens found. Run <code>./scripts/import_stitch_screens.sh &lt;folder&gt;</code>.
              </div>
            )}
          </section>
        </div>
      ) : null}
    </div>
  );
};

export default StitchScreens;
