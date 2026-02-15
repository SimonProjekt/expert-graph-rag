(function () {
  "use strict";

  const bootstrapNode = document.getElementById("ui-bootstrap");
  if (!bootstrapNode) {
    return;
  }

  const bootstrap = parseBootstrap(bootstrapNode.textContent);
  if (!bootstrap) {
    return;
  }

  const TAB_ORDER = ["papers", "experts", "graph", "ask"];
  const CACHE_LIMIT = 10;
  const DEBOUNCE_MS = 300;
  const REQUEST_TIMEOUT_MS = 25000;
  const GRAPH_MAX_EXPANSION = 4;

  const state = {
    query: (bootstrap.initialQuery || "").trim(),
    askQuery: (bootstrap.initialAskQuery || "").trim(),
    clearance: bootstrap.initialClearance || "PUBLIC",
    sortOrder: bootstrap.initialSort || "relevance",
    activeTab: TAB_ORDER.includes(bootstrap.initialTab) ? bootstrap.initialTab : "papers",
    cache: new Map(),
    cacheOrder: [],
    controllers: {},
    lastPayloads: {
      papers: null,
      experts: null,
      graph: null,
      ask: null,
    },
    graph: {
      network: null,
      fullNodes: [],
      fullEdges: [],
      nodeLookup: new Map(),
      focusNodeId: null,
      density: "balanced",
      expansionDepth: 2,
      pathOnly: false,
      clusterMode: false,
      truncatedAuthors: 0,
      truncatedTopics: 0,
      collaboratorEdges: 0,
    },
  };

  const dom = {
    queryInput: document.getElementById("query-input"),
    clearanceSelect: document.getElementById("clearance-select"),
    sortSelect: document.getElementById("paper-sort"),
    runSearchButton: document.getElementById("run-search"),
    exampleButtons: Array.from(document.querySelectorAll("#example-queries .chip")),
    tabButtons: Array.from(document.querySelectorAll(".tab-button")),
    tabPanels: {
      papers: document.getElementById("panel-papers"),
      experts: document.getElementById("panel-experts"),
      graph: document.getElementById("panel-graph"),
      ask: document.getElementById("panel-ask"),
    },
    emptyPanel: document.getElementById("panel-empty"),
    statusBanner: document.getElementById("ui-status"),
    redactionBanner: document.getElementById("redaction-banner"),
    redactionCount: document.getElementById("redaction-count"),

    papers: {
      loading: document.getElementById("papers-loading"),
      error: document.getElementById("papers-error"),
      empty: document.getElementById("papers-empty"),
      results: document.getElementById("papers-results"),
      meta: document.getElementById("papers-meta"),
    },

    experts: {
      loading: document.getElementById("experts-loading"),
      error: document.getElementById("experts-error"),
      empty: document.getElementById("experts-empty"),
      results: document.getElementById("experts-results"),
      meta: document.getElementById("experts-meta"),
    },

    graph: {
      loading: document.getElementById("graph-loading"),
      error: document.getElementById("graph-error"),
      empty: document.getElementById("graph-empty"),
      shell: document.getElementById("graph-shell"),
      canvas: document.getElementById("graph-canvas"),
      meta: document.getElementById("graph-meta"),
      fitButton: document.getElementById("graph-fit"),
      resetButton: document.getElementById("graph-reset"),
      densitySelect: document.getElementById("graph-density"),
      expansionRange: document.getElementById("graph-expansion"),
      expansionValue: document.getElementById("graph-expansion-value"),
      togglePathOnly: document.getElementById("toggle-path-only"),
      toggleClusterMode: document.getElementById("toggle-cluster-mode"),
      toggleAuthors: document.getElementById("toggle-authors"),
      togglePapers: document.getElementById("toggle-papers"),
      toggleTopics: document.getElementById("toggle-topics"),
      toggleCollaborators: document.getElementById("toggle-collaborators"),
      nodePanel: document.getElementById("graph-node-panel"),
      nodePanelClose: document.getElementById("graph-node-close"),
      nodeDetails: document.getElementById("graph-node-details"),
      nodeTitle: document.getElementById("graph-node-title"),
    },

    ask: {
      form: document.getElementById("ask-form"),
      input: document.getElementById("ask-query-input"),
      loading: document.getElementById("ask-loading"),
      error: document.getElementById("ask-error"),
      empty: document.getElementById("ask-empty"),
      results: document.getElementById("ask-results"),
      answer: document.getElementById("ask-answer"),
      citations: document.getElementById("ask-citations"),
      experts: document.getElementById("ask-experts"),
    },
  };

  const debouncedQueryChanged = debounce(() => {
    onQueryChanged();
  }, DEBOUNCE_MS);

  bindEvents();
  hydrateInitialControls();
  switchTab(state.activeTab, { focus: false, triggerLoad: false });

  if (state.query) {
    runActiveTabFetch();
  } else {
    showEmptyLanding(true);
    setStatus("Try a sample query to start exploring papers, experts, graph, and ask.");
  }

  function bindEvents() {
    if (dom.queryInput) {
      dom.queryInput.addEventListener("input", () => {
        state.query = dom.queryInput.value.trim();
        if (dom.ask.input && !dom.ask.input.value.trim()) {
          dom.ask.input.value = state.query;
          state.askQuery = state.query;
        }
        debouncedQueryChanged();
      });
    }

    if (dom.clearanceSelect) {
      dom.clearanceSelect.addEventListener("change", () => {
        state.clearance = dom.clearanceSelect.value;
        abortAllRequests();
        runActiveTabFetch();
        syncUrl();
      });
    }

    if (dom.sortSelect) {
      dom.sortSelect.addEventListener("change", () => {
        state.sortOrder = dom.sortSelect.value === "recency" ? "recency" : "relevance";
        if (state.lastPayloads.papers) {
          renderPapers(state.lastPayloads.papers);
        }
        syncUrl();
      });
    }

    if (dom.runSearchButton) {
      dom.runSearchButton.addEventListener("click", () => {
        state.query = dom.queryInput ? dom.queryInput.value.trim() : state.query;
        abortAllRequests();
        runActiveTabFetch();
      });
    }

    dom.exampleButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const nextQuery = button.getAttribute("data-query") || "";
        state.query = nextQuery.trim();
        if (dom.queryInput) {
          dom.queryInput.value = state.query;
          dom.queryInput.focus();
        }
        if (dom.ask.input) {
          dom.ask.input.value = state.query;
          state.askQuery = state.query;
        }
        abortAllRequests();
        runActiveTabFetch();
      });
    });

    dom.tabButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const tab = button.getAttribute("data-tab");
        if (!tab) {
          return;
        }
        switchTab(tab, { focus: true, triggerLoad: true });
      });

      button.addEventListener("keydown", (event) => {
        handleTabKeydown(event);
      });
    });

    if (dom.ask.form) {
      dom.ask.form.addEventListener("submit", (event) => {
        event.preventDefault();
        state.askQuery = dom.ask.input ? dom.ask.input.value.trim() : "";
        if (!state.askQuery) {
          renderAskError("Enter a question to run Ask.");
          return;
        }
        if (!state.query) {
          state.query = state.askQuery;
          if (dom.queryInput) {
            dom.queryInput.value = state.query;
          }
        }
        switchTab("ask", { focus: false, triggerLoad: false });
        fetchAsk(state.askQuery);
        syncUrl();
      });
    }

    if (dom.graph.densitySelect) {
      dom.graph.densitySelect.addEventListener("change", () => {
        const next = dom.graph.densitySelect.value;
        if (!["focused", "balanced", "full"].includes(next)) {
          return;
        }
        state.graph.density = next;
        if (state.lastPayloads.graph) {
          const graph = buildGraph(state.lastPayloads.graph.results || []);
          state.graph.fullNodes = graph.nodes;
          state.graph.fullEdges = graph.edges;
          state.graph.nodeLookup = graph.nodeLookup;
          state.graph.truncatedAuthors = graph.truncatedAuthors;
          state.graph.truncatedTopics = graph.truncatedTopics;
          state.graph.collaboratorEdges = graph.collaboratorEdges;
          renderGraphMeta();
          renderGraphData();
        }
      });
    }

    if (dom.graph.expansionRange) {
      const handleExpansionChange = () => {
        const nextDepth = clampGraphExpansion(dom.graph.expansionRange.value);
        if (nextDepth === state.graph.expansionDepth) {
          updateGraphExpansionLabel();
          return;
        }
        state.graph.expansionDepth = nextDepth;
        updateGraphExpansionLabel();
        applyGraphFilters();
      };
      dom.graph.expansionRange.addEventListener("input", handleExpansionChange);
      dom.graph.expansionRange.addEventListener("change", handleExpansionChange);
    }

    [
      dom.graph.togglePathOnly,
      dom.graph.toggleClusterMode,
      dom.graph.toggleAuthors,
      dom.graph.togglePapers,
      dom.graph.toggleTopics,
      dom.graph.toggleCollaborators,
    ].forEach((toggle) => {
      if (!toggle) {
        return;
      }
      toggle.addEventListener("change", () => {
        state.graph.pathOnly = !!(dom.graph.togglePathOnly && dom.graph.togglePathOnly.checked);
        state.graph.clusterMode = !!(
          dom.graph.toggleClusterMode && dom.graph.toggleClusterMode.checked
        );
        applyGraphFilters();
      });
    });

    if (dom.graph.fitButton) {
      dom.graph.fitButton.addEventListener("click", () => {
        if (state.graph.network) {
          state.graph.network.fit({ animation: true });
        }
      });
    }

    if (dom.graph.resetButton) {
      dom.graph.resetButton.addEventListener("click", () => {
        if (!state.graph.network) {
          return;
        }
        state.graph.focusNodeId = null;
        state.graph.network.moveTo({ position: { x: 0, y: 0 }, scale: 1, animation: true });
        state.graph.network.fit({ animation: true });
        renderGraphData();
      });
    }

    if (dom.graph.nodePanelClose) {
      dom.graph.nodePanelClose.addEventListener("click", () => {
        state.graph.focusNodeId = null;
        renderGraphData();
        closeNodePanel();
      });
    }
  }

  function hydrateInitialControls() {
    if (dom.queryInput) {
      dom.queryInput.value = state.query;
    }
    if (dom.ask.input) {
      dom.ask.input.value = state.askQuery || state.query;
    }
    if (dom.clearanceSelect) {
      dom.clearanceSelect.value = state.clearance;
    }
    if (dom.sortSelect) {
      dom.sortSelect.value = state.sortOrder;
    }
    if (dom.graph.densitySelect) {
      dom.graph.densitySelect.value = state.graph.density;
    }
    if (dom.graph.expansionRange) {
      dom.graph.expansionRange.value = String(state.graph.expansionDepth);
    }
    if (dom.graph.togglePathOnly) {
      dom.graph.togglePathOnly.checked = state.graph.pathOnly;
    }
    if (dom.graph.toggleClusterMode) {
      dom.graph.toggleClusterMode.checked = state.graph.clusterMode;
    }
    updateGraphExpansionLabel();
  }

  function onQueryChanged() {
    abortAllRequests();
    clearAllErrors();
    if (!state.query) {
      showEmptyLanding(true);
      clearRedaction();
      setStatus("Type a query or pick a sample to begin.");
      syncUrl();
      return;
    }

    showEmptyLanding(false);
    runActiveTabFetch();
    syncUrl();
  }

  function runActiveTabFetch() {
    if (!state.query) {
      showEmptyLanding(true);
      return;
    }

    showEmptyLanding(false);
    clearStatus();

    if (state.activeTab === "papers") {
      fetchPapers();
      return;
    }
    if (state.activeTab === "experts") {
      fetchExperts();
      return;
    }
    if (state.activeTab === "graph") {
      fetchGraph();
      return;
    }
    if (state.activeTab === "ask") {
      const question = (dom.ask.input ? dom.ask.input.value : "").trim() || state.query;
      state.askQuery = question;
      if (!question) {
        showAskEmpty(true);
        return;
      }
      fetchAsk(question);
    }
  }

  function switchTab(tab, options) {
    if (!TAB_ORDER.includes(tab)) {
      return;
    }

    state.activeTab = tab;

    dom.tabButtons.forEach((button) => {
      const buttonTab = button.getAttribute("data-tab");
      const selected = buttonTab === tab;
      button.setAttribute("aria-selected", selected ? "true" : "false");
      button.setAttribute("tabindex", selected ? "0" : "-1");
    });

    TAB_ORDER.forEach((tabId) => {
      const panel = dom.tabPanels[tabId];
      if (!panel) {
        return;
      }
      panel.hidden = tabId !== tab || !state.query;
    });

    if (options.focus) {
      const activeButton = dom.tabButtons.find((button) => button.getAttribute("data-tab") === tab);
      if (activeButton) {
        activeButton.focus();
      }
    }

    showEmptyLanding(!state.query);
    syncUrl();

    if (options.triggerLoad && state.query) {
      runActiveTabFetch();
    }

    if (tab === "graph" && state.graph.network) {
      window.requestAnimationFrame(() => {
        if (!state.graph.network) {
          return;
        }
        state.graph.network.redraw();
        state.graph.network.fit({ animation: true });
      });
    }
  }

  function handleTabKeydown(event) {
    const currentIndex = dom.tabButtons.findIndex((button) => button === event.currentTarget);
    if (currentIndex === -1) {
      return;
    }

    let nextIndex = currentIndex;

    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % dom.tabButtons.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + dom.tabButtons.length) % dom.tabButtons.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = dom.tabButtons.length - 1;
    } else if (event.key === "Enter" || event.key === " ") {
      const tab = event.currentTarget.getAttribute("data-tab");
      if (tab) {
        switchTab(tab, { focus: false, triggerLoad: true });
      }
      event.preventDefault();
      return;
    } else {
      return;
    }

    event.preventDefault();
    const nextButton = dom.tabButtons[nextIndex];
    const tab = nextButton.getAttribute("data-tab");
    if (tab) {
      switchTab(tab, { focus: true, triggerLoad: true });
    }
  }

  async function fetchPapers() {
    showLoading("papers", true);
    hideElement(dom.papers.error);
    hideElement(dom.papers.empty);
    dom.papers.results.innerHTML = "";

    const requestState = snapshotRequestState();

    try {
      const payload = await fetchApiJson(
        bootstrap.apiSearchUrl,
        {
          query: state.query,
          clearance: state.clearance,
          page: 1,
        },
        "papers"
      );

      if (!isCurrentRequest(requestState)) {
        return;
      }

      state.lastPayloads.papers = payload;
      renderPapers(payload);
      applyRedaction(payload.hidden_count ?? payload.redacted_count ?? 0);
      renderLiveFetchStatus(payload);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      renderPapersError(
        "Could not load papers. Try a shorter query, change clearance, or retry in a moment."
      );
    } finally {
      showLoading("papers", false);
    }
  }

  function renderPapers(payload) {
    const incoming = Array.isArray(payload.results) ? payload.results.slice() : [];
    const sorted = sortPapers(incoming, state.sortOrder);
    const liveFetch = payload && typeof payload === "object" ? payload.live_fetch : null;
    const tookMs =
      payload && typeof payload === "object" && Number.isFinite(Number(payload.took_ms))
        ? Number(payload.took_ms)
        : null;

    if (!sorted.length) {
      if (dom.papers.meta) {
        dom.papers.meta.textContent = "No matching papers";
      }
      dom.papers.empty.textContent = liveFetchEmptyMessage(liveFetch);
      showElement(dom.papers.empty);
      dom.papers.results.innerHTML = "";
      return;
    }

    dom.papers.empty.textContent = "No papers found for this query and clearance.";
    hideElement(dom.papers.empty);
    const timingText = tookMs !== null ? ` · ${tookMs} ms` : "";
    if (dom.papers.meta) {
      dom.papers.meta.textContent = `Sorted by ${state.sortOrder}${timingText}`;
    }

    dom.papers.results.innerHTML = sorted
      .map((paper) => {
        const published = paper.published_date || "n/a";
        const relevance = normalizeScore(paper.relevance_score);
        const semantic = normalizeScore(
          paper.score_breakdown?.semantic_relevance ?? paper.semantic_relevance_score
        );
        const queryAlignment = normalizeScore(paper.score_breakdown?.query_alignment);
        const authority = normalizeScore(paper.score_breakdown?.graph_authority);
        const centrality = normalizeScore(paper.score_breakdown?.graph_centrality);

        const topicChips = asChipList(paper.topics || [], "subtle");
        const authorChips = asChipList(paper.authors || [], "subtle");
        const whyMatched = escapeHtml(paper.why_matched || "No explanation available.");
        const graphPath = escapeHtml(paper.graph_path || "query -> paper");

        return `
          <article class="card">
            <h3>${escapeHtml(paper.title || "Untitled paper")}</h3>
            <p class="paper-snippet">${escapeHtml(paper.snippet || "")}</p>
            <div class="paper-meta">
              <span class="pill">Published: ${escapeHtml(published)}</span>
              <span class="pill">Relevance: ${relevance.toFixed(3)}</span>
              <span class="pill">Semantic: ${semantic.toFixed(3)}</span>
            </div>
            <div class="score-grid" aria-label="Search score breakdown for ${escapeHtml(paper.title || "paper")}">
              ${scoreRow("Query", queryAlignment)}
              ${scoreRow("Graph authority", authority)}
              ${scoreRow("Centrality", centrality)}
            </div>
            <details class="why-details">
              <summary>Why this paper?</summary>
              <p>${whyMatched}</p>
              <p><strong>Path:</strong> ${graphPath}</p>
            </details>
            <p><strong>Topics</strong></p>
            <div class="meta-list">${topicChips || '<span class="pill subtle">None</span>'}</div>
            <p><strong>Authors</strong></p>
            <div class="meta-list">${authorChips || '<span class="pill subtle">None</span>'}</div>
          </article>
        `;
      })
      .join("");
  }

  function renderLiveFetchStatus(payload) {
    const liveFetch = payload && typeof payload === "object" ? payload.live_fetch : null;
    if (!liveFetch || typeof liveFetch !== "object") {
      return;
    }
    if (!liveFetch.enabled) {
      return;
    }
    if (liveFetch.attempted && liveFetch.reason === "fetched") {
      const worksProcessed = Number(liveFetch.works_processed || 0);
      const papersTouched = Number(liveFetch.papers_touched || 0);
      const durationMs = Number(liveFetch.duration_ms || 0);
      setStatus(
        `Live OpenAlex fetch added ${worksProcessed} work(s), touching ${papersTouched} local paper record(s) in ${durationMs} ms.`
      );
      return;
    }
    if (!liveFetch.attempted && liveFetch.reason === "missing_api_key") {
      setStatus("Local results only. Add OPENALEX_API_KEY to enable live OpenAlex fetch.");
      return;
    }
    if (!liveFetch.attempted && liveFetch.reason === "cooldown") {
      setStatus("Live OpenAlex fetch is cooling down to avoid API spam. Reusing cached local data.");
      return;
    }
    if (liveFetch.attempted && liveFetch.reason === "failed") {
      setStatus("Live OpenAlex fetch failed. Showing best available local results.");
    }
  }

  function liveFetchEmptyMessage(liveFetch) {
    if (!liveFetch || typeof liveFetch !== "object") {
      return "No papers found for this query and clearance.";
    }
    if (liveFetch.attempted && liveFetch.reason === "fetched") {
      return "No papers yet after live OpenAlex fetch. Try a broader query or another example.";
    }
    if (!liveFetch.attempted && liveFetch.reason === "missing_api_key") {
      return "No papers found locally. Add OPENALEX_API_KEY to enable live OpenAlex fetch.";
    }
    return "No papers found for this query and clearance.";
  }

  function renderPapersError(message) {
    if (dom.papers.meta) {
      dom.papers.meta.textContent = "Error";
    }
    dom.papers.results.innerHTML = "";
    dom.papers.error.textContent = message;
    showElement(dom.papers.error);
    setStatus("Paper search failed.");
  }

  async function fetchExperts() {
    showLoading("experts", true);
    hideElement(dom.experts.error);
    hideElement(dom.experts.empty);
    dom.experts.results.innerHTML = "";

    const requestState = snapshotRequestState();

    try {
      const payload = await fetchApiJson(
        bootstrap.apiExpertsUrl,
        {
          query: state.query,
          clearance: state.clearance,
        },
        "experts"
      );

      if (!isCurrentRequest(requestState)) {
        return;
      }

      state.lastPayloads.experts = payload;
      renderExperts(payload);
      applyRedaction(payload.redacted_count || 0);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      renderExpertsError(
        "Could not load experts. Please retry or broaden your query terms."
      );
    } finally {
      showLoading("experts", false);
    }
  }

  function renderExperts(payload) {
    const experts = Array.isArray(payload.experts) ? payload.experts : [];

    if (!experts.length) {
      if (dom.experts.meta) {
        dom.experts.meta.textContent = "No matching experts";
      }
      showElement(dom.experts.empty);
      return;
    }

    hideElement(dom.experts.empty);
    if (dom.experts.meta) {
      dom.experts.meta.textContent = "Ranked by semantic and graph signals";
    }

    dom.experts.results.innerHTML = experts
      .map((expert, index) => {
        const profileHref = `${bootstrap.expertProfileBasePath}${expert.author_id}/?clearance=${encodeURIComponent(
          state.clearance
        )}&query=${encodeURIComponent(state.query)}`;

        const semantic = normalizeScore(expert.score_breakdown?.semantic_relevance);
        const recency = normalizeScore(expert.score_breakdown?.recency_boost);
        const coverage = normalizeScore(expert.score_breakdown?.topic_coverage);
        const queryAlignment = normalizeScore(expert.score_breakdown?.query_alignment);
        const graphProximity = normalizeScore(expert.score_breakdown?.graph_proximity);
        const citationAuthority = normalizeScore(expert.score_breakdown?.citation_authority);
        const centrality = normalizeScore(expert.score_breakdown?.graph_centrality);
        const topPaperList = Array.isArray(expert.top_papers) ? expert.top_papers : [];
        const topPaperHtml = topPaperList
          .slice(0, 3)
          .map((paper) => {
            const title = escapeHtml(paper.title || "Untitled");
            const dateLabel = escapeHtml(paper.published_date || "n/a");
            return `<li>${title} <span class="muted">(${dateLabel})</span></li>`;
          })
          .join("");

        return `
          <article class="card">
            <h3>
              ${index + 1}. <a href="${profileHref}">${escapeHtml(expert.name || "Unknown")}</a>
            </h3>
            <p>${escapeHtml(expert.institution || "")}</p>
            <div class="meta-list">${asChipList(expert.top_topics || [], "")}</div>

            <div class="score-grid" aria-label="Score breakdown for ${escapeHtml(expert.name || "expert")}">
              ${scoreRow("Semantic", semantic)}
              ${scoreRow("Graph proximity", graphProximity)}
              ${scoreRow("Citation auth.", citationAuthority)}
              ${scoreRow("Recency", recency)}
              ${scoreRow("Query", queryAlignment)}
              ${scoreRow("Coverage", coverage)}
              ${scoreRow("Centrality", centrality)}
            </div>

            <details class="why-details">
              <summary>Top papers used for ranking</summary>
              <ul class="list">${topPaperHtml || "<li>No top papers available.</li>"}</ul>
            </details>

            <details class="why-details">
              <summary>Why this expert?</summary>
              <p>${escapeHtml(expert.why_ranked || "No explanation available.")}</p>
            </details>
          </article>
        `;
      })
      .join("");
  }

  function renderExpertsError(message) {
    if (dom.experts.meta) {
      dom.experts.meta.textContent = "Error";
    }
    dom.experts.error.textContent = message;
    showElement(dom.experts.error);
    setStatus("Expert ranking failed.");
  }

  async function fetchGraph() {
    showLoading("graph", true);
    hideElement(dom.graph.error);
    hideElement(dom.graph.empty);
    hideElement(dom.graph.shell);
    closeNodePanel();

    const requestState = snapshotRequestState();

    try {
      const payload = await fetchApiJson(
        bootstrap.apiSearchUrl,
        {
          query: state.query,
          clearance: state.clearance,
          page: 1,
        },
        "graph"
      );

      if (!isCurrentRequest(requestState)) {
        return;
      }

      state.lastPayloads.graph = payload;
      const graph = buildGraph(payload.results || []);
      state.graph.fullNodes = graph.nodes;
      state.graph.fullEdges = graph.edges;
      state.graph.nodeLookup = graph.nodeLookup;
      state.graph.focusNodeId = null;
      state.graph.truncatedAuthors = graph.truncatedAuthors;
      state.graph.truncatedTopics = graph.truncatedTopics;
      state.graph.collaboratorEdges = graph.collaboratorEdges;

      if (!graph.nodes.length) {
        dom.graph.meta.textContent = "0 nodes";
        showElement(dom.graph.empty);
        return;
      }

      hideElement(dom.graph.empty);
      showElement(dom.graph.shell);
      renderGraphMeta();
      renderGraphData();
      applyRedaction(payload.redacted_count || 0);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      dom.graph.error.textContent =
        "Could not load graph data. Try narrowing the query or refresh the page.";
      showElement(dom.graph.error);
      setStatus("Graph load failed.");
    } finally {
      showLoading("graph", false);
    }
  }

  function buildGraph(results) {
    const nodes = [];
    const edges = [];
    const nodeLookup = new Map();
    const density = state.graph.density || "balanced";

    const seenNodeIds = new Set();
    const seenEdges = new Set();
    const queryId = "query:current";
    const queryLabel = truncateLabel(state.query || "Current query", 28);
    const authorByPaper = new Map();
    const caps = graphCaps(density);
    let truncatedAuthors = 0;
    let truncatedTopics = 0;
    let collaboratorEdges = 0;

    function addNode(node) {
      if (seenNodeIds.has(node.id)) {
        return;
      }
      seenNodeIds.add(node.id);
      nodes.push(node);
      nodeLookup.set(node.id, node);
    }

    function addEdge(edge) {
      const key = edge.id || `${edge.from}->${edge.to}:${edge.label || ""}`;
      if (seenEdges.has(key)) {
        return;
      }
      seenEdges.add(key);
      edges.push({
        ...edge,
        id: key,
      });
    }

    addNode({
      id: queryId,
      label: queryLabel,
      group: "query",
      value: 42,
      title: `Query: ${escapeHtml(state.query || "n/a")}`,
      details: {
        Type: "Query",
        Query: state.query || "n/a",
      },
    });

    results.forEach((paper, index) => {
      const paperId = `paper:${paper.paper_id || index + 1}`;
      const hop = Number.isFinite(Number(paper.graph_hop_distance))
        ? Number(paper.graph_hop_distance)
        : 0;
      const pathLabel = paper.graph_path || `query -> paper:${paper.paper_id || "n/a"}`;
      const whyMatched = paper.why_matched || "No explanation available.";
      const source = paper.source || "semantic";

      addNode({
        id: paperId,
        label: paper.title || "Untitled paper",
        group: "paper",
        value: 18 + Math.round(normalizeScore(paper.relevance_score) * 12),
        title: `Paper: ${escapeHtml(paper.title || "Untitled")}`,
        details: {
          Type: "Paper",
          Title: paper.title || "Untitled",
          Published: paper.published_date || "n/a",
          Score: Number(normalizeScore(paper.relevance_score)).toFixed(3),
          Source: source,
          Hop: String(hop),
          Why: whyMatched,
          Path: pathLabel,
        },
      });

      addEdge({
        id: `${queryId}->${paperId}:QUERY_MATCH`,
        from: queryId,
        to: paperId,
        label: hop > 0 ? `HOP ${hop}` : "MATCH",
        title: pathLabel,
        dashes: hop > 0,
      });

      const shownAuthors = (paper.authors || []).slice(0, caps.maxAuthorsPerPaper);
      const hiddenAuthors = Math.max(0, (paper.authors || []).length - shownAuthors.length);
      truncatedAuthors += hiddenAuthors;

      shownAuthors.forEach((author) => {
        const authorId = `author:${slug(author)}`;
        addNode({
          id: authorId,
          label: author,
          group: "author",
          value: 12,
          title: `Author: ${escapeHtml(author)}`,
          details: {
            Type: "Author",
            Name: author,
          },
        });
        addEdge({
          from: authorId,
          to: paperId,
          label: "WROTE",
          relation: "WROTE",
        });

        if (!authorByPaper.has(paperId)) {
          authorByPaper.set(paperId, []);
        }
        authorByPaper.get(paperId).push(authorId);
      });

      const shownTopics = (paper.topics || []).slice(0, caps.maxTopicsPerPaper);
      const hiddenTopics = Math.max(0, (paper.topics || []).length - shownTopics.length);
      truncatedTopics += hiddenTopics;

      shownTopics.forEach((topic) => {
        const topicId = `topic:${slug(topic)}`;
        addNode({
          id: topicId,
          label: topic,
          group: "topic",
          value: 10,
          title: `Topic: ${escapeHtml(topic)}`,
          details: {
            Type: "Topic",
            Name: topic,
          },
        });
        addEdge({
          from: paperId,
          to: topicId,
          label: "HAS_TOPIC",
          relation: "HAS_TOPIC",
        });
      });

      if (hiddenAuthors > 0 || hiddenTopics > 0) {
        const paperNode = nodeLookup.get(paperId);
        if (paperNode && paperNode.details) {
          paperNode.details["Graph scope"] = `showing ${shownAuthors.length}/${(paper.authors || []).length} authors and ${shownTopics.length}/${(paper.topics || []).length} topics`;
        }
      }
    });

    if (dom.graph.toggleCollaborators && dom.graph.toggleCollaborators.checked) {
      authorByPaper.forEach((authorIds, paperId) => {
        for (let left = 0; left < authorIds.length; left += 1) {
          for (let right = left + 1; right < authorIds.length; right += 1) {
            addEdge({
              id: `${authorIds[left]}<->${authorIds[right]}:${paperId}:COLLABORATED_WITH`,
              from: authorIds[left],
              to: authorIds[right],
              label: "COLLABORATED_WITH",
              relation: "COLLABORATED_WITH",
              dashes: true,
            });
            collaboratorEdges += 1;
          }
        }
      });
    }

    return {
      nodes,
      edges,
      nodeLookup,
      truncatedAuthors,
      truncatedTopics,
      collaboratorEdges,
    };
  }

  function renderGraphData() {
    if (!window.vis || !window.vis.Network || !window.vis.DataSet || !dom.graph.canvas) {
      dom.graph.error.textContent = "vis-network failed to load. Check network access and reload.";
      showElement(dom.graph.error);
      return;
    }

    const filtered = filteredGraphData();
    const expanded = applyQueryExpansion(filtered);
    const pathScoped = applyPathOnlyView(expanded);
    state.graph.focusNodeId = pathScoped.focusNodeId;
    const styled = applyGraphPathHighlight(pathScoped, pathScoped.focusNodeId);
    const minCanvasHeight = Math.max(dom.graph.canvas.clientHeight || 0, 520);
    dom.graph.canvas.style.height = `${minCanvasHeight}px`;

    try {
      if (!state.graph.network) {
        state.graph.network = new window.vis.Network(
          dom.graph.canvas,
          {
            nodes: new window.vis.DataSet(styled.nodes),
            edges: new window.vis.DataSet(styled.edges),
          },
          {
            autoResize: true,
            interaction: {
              hover: true,
              multiselect: false,
              navigationButtons: true,
              keyboard: true,
            },
            layout: {
              improvedLayout: true,
            },
            physics: {
              barnesHut: {
                gravitationalConstant: -18000,
                springLength: 120,
                springConstant: 0.03,
              },
              stabilization: {
                iterations: 200,
              },
            },
            nodes: {
              shape: "dot",
              scaling: {
                min: 8,
                max: 34,
              },
              font: {
                size: 12,
                color: "#102236",
              },
            },
            edges: {
              arrows: { to: { enabled: true, scaleFactor: 0.35 } },
              smooth: { type: "dynamic" },
              color: "#4a5568",
              width: 0.9,
              font: {
                size: 0,
              },
            },
            groups: {
              query: { color: { background: "#6d28d9" } },
              author: { color: { background: "#ef9d27" } },
              paper: { color: { background: "#2f7dd3" } },
              topic: { color: { background: "#16a073" } },
            },
          }
        );

        state.graph.network.on("click", (params) => {
          if (!params.nodes || !params.nodes.length) {
            state.graph.focusNodeId = null;
            renderGraphData();
            closeNodePanel();
            return;
          }
          const clickedNodeId = params.nodes[0];
          if (
            typeof state.graph.network.isCluster === "function" &&
            state.graph.network.isCluster(clickedNodeId)
          ) {
            const clusterNode = state.graph.network.body?.data?.nodes?.get(clickedNodeId);
            openNodePanel({
              id: clickedNodeId,
              group: "cluster",
              details: {
                Type: "Cluster",
                Label: clusterNode?.label || "Grouped node",
                Summary: "Disable cluster mode to inspect individual nodes.",
              },
            });
            return;
          }

          const node = state.graph.nodeLookup.get(clickedNodeId);
          if (!node) {
            state.graph.focusNodeId = null;
            renderGraphData();
            closeNodePanel();
            return;
          }
          state.graph.focusNodeId = node.id;
          renderGraphData();
          openNodePanel(node);
        });

        applyClusterMode(styled.nodes);
        state.graph.network.fit({ animation: true });
        return;
      }

      state.graph.network.setData({
        nodes: new window.vis.DataSet(styled.nodes),
        edges: new window.vis.DataSet(styled.edges),
      });
      applyClusterMode(styled.nodes);
      state.graph.network.redraw();
      state.graph.network.fit({ animation: true });
    } catch (_error) {
      dom.graph.error.textContent = "Graph rendering failed. Reload the page and retry.";
      showElement(dom.graph.error);
    }
  }

  function filteredGraphData() {
    const showAuthors = !!(dom.graph.toggleAuthors && dom.graph.toggleAuthors.checked);
    const showPapers = !!(dom.graph.togglePapers && dom.graph.togglePapers.checked);
    const showTopics = !!(dom.graph.toggleTopics && dom.graph.toggleTopics.checked);
    const showCollaborators = !!(
      dom.graph.toggleCollaborators && dom.graph.toggleCollaborators.checked
    );

    const allowed = new Set();
    if (showAuthors) {
      allowed.add("author");
    }
    if (showPapers) {
      allowed.add("paper");
    }
    if (showTopics) {
      allowed.add("topic");
    }
    allowed.add("query");

    const nodes = state.graph.fullNodes.filter((node) => allowed.has(node.group));
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = state.graph.fullEdges.filter((edge) => {
      if (!showCollaborators && edge.relation === "COLLABORATED_WITH") {
        return false;
      }
      return nodeIds.has(edge.from) && nodeIds.has(edge.to);
    });

    return { nodes, edges };
  }

  function applyQueryExpansion(filtered) {
    const expansionDepth = clampGraphExpansion(state.graph.expansionDepth);
    const queryNodeId = "query:current";
    const nodeDistance = computeNodeDistances({
      startNodeId: queryNodeId,
      edges: filtered.edges,
      maxDepth: expansionDepth,
    });

    const allowedNodeIds = new Set([queryNodeId]);
    nodeDistance.forEach((distance, nodeId) => {
      if (distance <= expansionDepth) {
        allowedNodeIds.add(nodeId);
      }
    });

    const nodes = filtered.nodes.filter((node) => allowedNodeIds.has(node.id));
    const visibleNodeIds = new Set(nodes.map((node) => node.id));
    const edges = filtered.edges.filter(
      (edge) => visibleNodeIds.has(edge.from) && visibleNodeIds.has(edge.to)
    );
    return { nodes, edges };
  }

  function applyPathOnlyView(filtered) {
    const pathOnlyEnabled = state.graph.pathOnly;
    const visibleNodeIds = new Set(filtered.nodes.map((node) => node.id));

    let focusNodeId = state.graph.focusNodeId;
    if (!focusNodeId || !visibleNodeIds.has(focusNodeId)) {
      focusNodeId = null;
    }

    if (!pathOnlyEnabled) {
      return { ...filtered, focusNodeId };
    }

    if (!focusNodeId) {
      focusNodeId = pickDefaultPathTarget(filtered);
    }
    if (!focusNodeId) {
      return { ...filtered, focusNodeId: null };
    }

    const path = computePathToQuery({
      targetNodeId: focusNodeId,
      edges: filtered.edges,
    });
    if (!path) {
      return { ...filtered, focusNodeId: null };
    }

    const nodes = filtered.nodes.filter((node) => path.nodeIds.has(node.id));
    const edges = filtered.edges.filter((edge) => path.edgeIds.has(edge.id));
    return { nodes, edges, focusNodeId };
  }

  function pickDefaultPathTarget(filtered) {
    const directMatch = filtered.edges.find(
      (edge) => edge.from === "query:current" && String(edge.to).startsWith("paper:")
    );
    if (directMatch) {
      return directMatch.to;
    }
    const firstPaper = filtered.nodes.find((node) => node.group === "paper");
    if (firstPaper) {
      return firstPaper.id;
    }
    return null;
  }

  function applyGraphPathHighlight(filtered, focusOverride) {
    const visibleNodeIds = new Set(filtered.nodes.map((node) => node.id));
    const focusNodeId = visibleNodeIds.has(focusOverride) ? focusOverride : null;
    if (!focusNodeId) {
      return {
        nodes: filtered.nodes.map((node) => ({
          ...node,
          color: graphNodeColor(node.group, false, false),
        })),
        edges: filtered.edges.map((edge) => ({
          ...edge,
          color: { color: "#4a5568", opacity: 0.9 },
          font: { size: 0 },
        })),
      };
    }

    const path = computePathToQuery({
      targetNodeId: focusNodeId,
      edges: filtered.edges,
    });

    const highlightedNodeIds = path ? path.nodeIds : new Set([focusNodeId]);
    const highlightedEdgeIds = path ? path.edgeIds : new Set();

    const nodes = filtered.nodes.map((node) => {
      const highlighted = highlightedNodeIds.has(node.id);
      const muted = !highlighted;
      return {
        ...node,
        color: graphNodeColor(node.group, highlighted, muted),
      };
    });

    const edges = filtered.edges.map((edge) => {
      const highlighted = highlightedEdgeIds.has(edge.id);
      return {
        ...edge,
        width: highlighted ? 2.8 : 0.9,
        color: highlighted
          ? { color: "#c2410c", opacity: 1.0 }
          : { color: "#9aa5b1", opacity: 0.45 },
        font: highlighted ? { size: 10, color: "#7c2d12" } : { size: 0 },
      };
    });

    return { nodes, edges };
  }

  function applyClusterMode(nodes) {
    if (!state.graph.clusterMode || !state.graph.network) {
      return;
    }

    const groupCounts = nodes.reduce((counts, node) => {
      const current = counts.get(node.group) || 0;
      counts.set(node.group, current + 1);
      return counts;
    }, new Map());

    clusterGraphGroup("author", Number(groupCounts.get("author") || 0), 14);
    clusterGraphGroup("topic", Number(groupCounts.get("topic") || 0), 16);
  }

  function clusterGraphGroup(group, count, minCount) {
    if (!state.graph.network || count < minCount) {
      return;
    }

    const clusterId = `cluster:${group}`;
    const color = graphNodeColor(group, false, false);
    const labelSuffix = count === 1 ? "" : "s";

    try {
      state.graph.network.cluster({
        joinCondition(nodeOptions) {
          return nodeOptions.group === group;
        },
        clusterNodeProperties: {
          id: clusterId,
          label: `${count} ${group}${labelSuffix}`,
          group,
          shape: "database",
          value: Math.max(18, Math.min(42, Math.round(count / 2))),
          color,
          title: `Clustered ${count} ${group}${labelSuffix}`,
        },
      });
    } catch (_error) {
      // Keep graph interactive even if clustering fails on a specific dataset.
    }
  }

  function computeNodeDistances({ startNodeId, edges, maxDepth }) {
    const adjacency = new Map();
    edges.forEach((edge) => {
      const left = adjacency.get(edge.from) || [];
      left.push(edge.to);
      adjacency.set(edge.from, left);

      const right = adjacency.get(edge.to) || [];
      right.push(edge.from);
      adjacency.set(edge.to, right);
    });

    const distanceByNode = new Map([[startNodeId, 0]]);
    const queue = [startNodeId];

    while (queue.length > 0) {
      const current = queue.shift();
      if (!current) {
        continue;
      }

      const currentDistance = distanceByNode.get(current);
      if (!Number.isFinite(currentDistance)) {
        continue;
      }
      if (currentDistance >= maxDepth) {
        continue;
      }

      const neighbors = adjacency.get(current) || [];
      neighbors.forEach((neighbor) => {
        if (distanceByNode.has(neighbor)) {
          return;
        }
        distanceByNode.set(neighbor, currentDistance + 1);
        queue.push(neighbor);
      });
    }

    return distanceByNode;
  }

  function computePathToQuery({ targetNodeId, edges }) {
    const queryNodeId = "query:current";
    if (!targetNodeId) {
      return null;
    }
    if (targetNodeId === queryNodeId) {
      return {
        nodeIds: new Set([queryNodeId]),
        edgeIds: new Set(),
      };
    }

    const adjacency = new Map();
    edges.forEach((edge) => {
      const left = adjacency.get(edge.from) || [];
      left.push({ next: edge.to, edgeId: edge.id });
      adjacency.set(edge.from, left);

      const right = adjacency.get(edge.to) || [];
      right.push({ next: edge.from, edgeId: edge.id });
      adjacency.set(edge.to, right);
    });

    const queue = [queryNodeId];
    const visited = new Set([queryNodeId]);
    const parentByNode = new Map();

    while (queue.length > 0) {
      const current = queue.shift();
      if (!current) {
        continue;
      }
      if (current === targetNodeId) {
        break;
      }

      const neighbors = adjacency.get(current) || [];
      neighbors.forEach((neighbor) => {
        if (visited.has(neighbor.next)) {
          return;
        }
        visited.add(neighbor.next);
        parentByNode.set(neighbor.next, {
          prev: current,
          edgeId: neighbor.edgeId,
        });
        queue.push(neighbor.next);
      });
    }

    if (!visited.has(targetNodeId)) {
      return null;
    }

    const nodeIds = new Set([targetNodeId]);
    const edgeIds = new Set();
    let cursor = targetNodeId;
    while (cursor !== queryNodeId) {
      const step = parentByNode.get(cursor);
      if (!step) {
        break;
      }
      edgeIds.add(step.edgeId);
      nodeIds.add(step.prev);
      cursor = step.prev;
    }
    return { nodeIds, edgeIds };
  }

  function graphNodeColor(group, highlighted, muted) {
    if (muted) {
      return { background: "#e2e8f0", border: "#cbd5e1" };
    }
    if (group === "query") {
      return highlighted
        ? { background: "#6d28d9", border: "#4c1d95" }
        : { background: "#8b5cf6", border: "#6d28d9" };
    }
    if (group === "author") {
      return highlighted
        ? { background: "#ef9d27", border: "#b45309" }
        : { background: "#f4b860", border: "#c47d16" };
    }
    if (group === "paper") {
      return highlighted
        ? { background: "#2f7dd3", border: "#1e40af" }
        : { background: "#64a3e8", border: "#2f7dd3" };
    }
    return highlighted
      ? { background: "#16a073", border: "#047857" }
      : { background: "#52c39d", border: "#16a073" };
  }

  function applyGraphFilters() {
    if (!state.graph.fullNodes.length) {
      return;
    }
    if (state.graph.focusNodeId && !state.graph.nodeLookup.has(state.graph.focusNodeId)) {
      state.graph.focusNodeId = null;
    }
    renderGraphMeta();
    renderGraphData();
  }

  function graphCaps(density) {
    if (density === "focused") {
      return { maxAuthorsPerPaper: 3, maxTopicsPerPaper: 4 };
    }
    if (density === "full") {
      return { maxAuthorsPerPaper: 8, maxTopicsPerPaper: 10 };
    }
    return { maxAuthorsPerPaper: 5, maxTopicsPerPaper: 6 };
  }

  function renderGraphMeta() {
    if (!dom.graph.meta) {
      return;
    }
    const nodesCount = state.graph.fullNodes.length;
    const edgesCount = state.graph.fullEdges.length;
    const extraAuthors = Number(state.graph.truncatedAuthors || 0);
    const extraTopics = Number(state.graph.truncatedTopics || 0);
    const collabEdges = Number(state.graph.collaboratorEdges || 0);
    const truncationText =
      extraAuthors > 0 || extraTopics > 0
        ? ` · truncated ${extraAuthors} authors, ${extraTopics} topics`
        : "";
    const collabText = collabEdges > 0 ? ` · ${collabEdges} collaborator links` : "";
    const modeText = [
      `depth ${state.graph.expansionDepth}`,
      state.graph.pathOnly ? "path-only" : null,
      state.graph.clusterMode ? "clustered" : null,
    ]
      .filter(Boolean)
      .join(" · ");
    const modeSuffix = modeText ? ` · ${modeText}` : "";
    dom.graph.meta.textContent = `${nodesCount} nodes · ${edgesCount} edges${collabText}${truncationText}${modeSuffix}`;
  }

  function openNodePanel(node) {
    if (!dom.graph.nodePanel || !dom.graph.nodeDetails || !dom.graph.nodeTitle) {
      return;
    }

    const groupName = String(node.group || "node");
    dom.graph.nodeTitle.textContent = `${groupName[0].toUpperCase()}${groupName.slice(1)} Details`;
    const details = node.details || {};

    dom.graph.nodeDetails.innerHTML = Object.entries(details)
      .map(([key, value]) => {
        return `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value))}</dd>`;
      })
      .join("");

    showElement(dom.graph.nodePanel);
  }

  function closeNodePanel() {
    if (!dom.graph.nodePanel) {
      return;
    }
    hideElement(dom.graph.nodePanel);
  }

  async function fetchAsk(question) {
    showLoading("ask", true);
    hideElement(dom.ask.error);
    hideElement(dom.ask.empty);
    hideElement(dom.ask.results);

    const requestState = snapshotRequestState();

    try {
      const payload = await fetchApiJson(
        bootstrap.apiAskUrl,
        {
          query: question,
          clearance: state.clearance,
        },
        "ask"
      );

      if (!isCurrentRequest(requestState)) {
        return;
      }

      state.lastPayloads.ask = payload;
      renderAsk(payload);
      applyRedaction(payload.redacted_count || 0);
    } catch (error) {
      if (isAbortError(error)) {
        return;
      }
      renderAskError(
        "Could not generate an answer. Try refining the question or switching clearance."
      );
    } finally {
      showLoading("ask", false);
    }
  }

  function renderAsk(payload) {
    showElement(dom.ask.results);
    dom.ask.answer.innerHTML = renderStructuredAnswer(
      payload.answer_payload || payload.answer || "No answer available."
    );

    const citations = Array.isArray(payload.citations) ? payload.citations : [];
    dom.ask.citations.innerHTML = citations
      .map((citation) => {
        if (citation.redacted) {
          return `<li>[${citation.id}] <span class="redacted">redacted reference</span></li>`;
        }
        const label = `${citation.paper_title || "Unknown"} (${citation.reference || "n/a"})`;
        return `<li>[${citation.id}] ${escapeHtml(label)}</li>`;
      })
      .join("");

    const experts = Array.isArray(payload.recommended_experts)
      ? payload.recommended_experts
      : [];

    if (!experts.length) {
      dom.ask.experts.innerHTML = "<li>No expert recommendations available.</li>";
      return;
    }

    dom.ask.experts.innerHTML = experts
      .map((expert) => {
        const href = `${bootstrap.expertProfileBasePath}${expert.author_id}/?clearance=${encodeURIComponent(
          state.clearance
        )}&query=${encodeURIComponent(state.query)}`;
        return `<li><a href="${href}">${escapeHtml(expert.name || "Unknown")}</a> <span class="muted">${escapeHtml(
          expert.institution || ""
        )}</span></li>`;
      })
      .join("");
  }

  function renderAskError(message) {
    dom.ask.error.textContent = message;
    showElement(dom.ask.error);
    hideElement(dom.ask.results);
    setStatus("Ask query failed.");
  }

  function showAskEmpty(show) {
    if (show) {
      showElement(dom.ask.empty);
      hideElement(dom.ask.results);
      return;
    }
    hideElement(dom.ask.empty);
  }

  function showEmptyLanding(show) {
    if (show) {
      showElement(dom.emptyPanel);
      TAB_ORDER.forEach((tabId) => {
        const panel = dom.tabPanels[tabId];
        if (panel) {
          panel.hidden = true;
        }
      });
      return;
    }

    hideElement(dom.emptyPanel);
    TAB_ORDER.forEach((tabId) => {
      const panel = dom.tabPanels[tabId];
      if (panel) {
        panel.hidden = tabId !== state.activeTab;
      }
    });
  }

  function showLoading(tab, isLoading) {
    if (tab === "papers") {
      toggleLoadingElement(dom.papers.loading, isLoading);
      return;
    }
    if (tab === "experts") {
      toggleLoadingElement(dom.experts.loading, isLoading);
      return;
    }
    if (tab === "graph") {
      toggleLoadingElement(dom.graph.loading, isLoading);
      return;
    }
    if (tab === "ask") {
      toggleLoadingElement(dom.ask.loading, isLoading);
    }
  }

  function toggleLoadingElement(element, isLoading) {
    if (!element) {
      return;
    }
    if (isLoading) {
      showElement(element);
      return;
    }
    hideElement(element);
  }

  function clearAllErrors() {
    [dom.papers.error, dom.experts.error, dom.graph.error, dom.ask.error].forEach((element) => {
      if (element) {
        hideElement(element);
      }
    });
  }

  function applyRedaction(count) {
    const normalized = Number.isFinite(Number(count)) ? Number(count) : 0;
    if (!dom.redactionBanner || !dom.redactionCount) {
      return;
    }
    if (normalized > 0) {
      dom.redactionCount.textContent = String(normalized);
      showElement(dom.redactionBanner);
      return;
    }
    clearRedaction();
  }

  function clearRedaction() {
    if (dom.redactionCount) {
      dom.redactionCount.textContent = "0";
    }
    if (dom.redactionBanner) {
      hideElement(dom.redactionBanner);
    }
  }

  function setStatus(message) {
    if (!dom.statusBanner) {
      return;
    }
    if (!message) {
      clearStatus();
      return;
    }
    dom.statusBanner.textContent = message;
    showElement(dom.statusBanner);
  }

  function clearStatus() {
    if (!dom.statusBanner) {
      return;
    }
    dom.statusBanner.textContent = "";
    hideElement(dom.statusBanner);
  }

  async function fetchApiJson(endpoint, params, controllerKey) {
    const queryString = new URLSearchParams(params).toString();
    const url = `${endpoint}?${queryString}`;

    const cached = getCachedResponse(url);
    if (cached) {
      return cached;
    }

    const controller = resetController(controllerKey);
    const timeoutId = window.setTimeout(() => {
      controller.__timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    let response;
    try {
      response = await fetch(url, {
        method: "GET",
        headers: {
          Accept: "application/json",
        },
        signal: controller.signal,
      });
    } catch (error) {
      if (isAbortError(error) && controller.__timedOut) {
        throw new Error("Request timed out. Please retry.");
      }
      throw error;
    } finally {
      window.clearTimeout(timeoutId);
    }

    if (!response.ok) {
      const detail = await extractError(response);
      throw new Error(detail || `Request failed with status ${response.status}`);
    }

    const payload = await response.json();
    setCachedResponse(url, payload);
    return payload;
  }

  async function extractError(response) {
    try {
      const body = await response.json();
      if (typeof body.detail === "string") {
        return body.detail;
      }
      if (typeof body.query === "string") {
        return body.query;
      }
      if (body && typeof body === "object") {
        const firstEntry = Object.entries(body)[0];
        if (!firstEntry) {
          return "";
        }
        const firstValue = firstEntry[1];
        if (Array.isArray(firstValue) && firstValue.length > 0) {
          return String(firstValue[0]);
        }
        if (typeof firstValue === "string") {
          return firstValue;
        }
      }
      return "";
    } catch (_error) {
      return "";
    }
  }

  function resetController(key) {
    const existing = state.controllers[key];
    if (existing) {
      existing.abort();
    }
    const controller = new AbortController();
    controller.__timedOut = false;
    state.controllers[key] = controller;
    return controller;
  }

  function abortAllRequests() {
    Object.values(state.controllers).forEach((controller) => {
      if (controller && typeof controller.abort === "function") {
        controller.abort();
      }
    });
    state.controllers = {};
  }

  function snapshotRequestState() {
    return {
      query: state.query,
      clearance: state.clearance,
      tab: state.activeTab,
    };
  }

  function isCurrentRequest(snapshot) {
    return (
      snapshot.query === state.query &&
      snapshot.clearance === state.clearance &&
      snapshot.tab === state.activeTab
    );
  }

  function getCachedResponse(key) {
    const value = state.cache.get(key);
    if (!value) {
      return null;
    }
    // Clone to avoid accidental mutation of cache entries.
    return JSON.parse(JSON.stringify(value));
  }

  function setCachedResponse(key, value) {
    if (state.cache.has(key)) {
      state.cacheOrder = state.cacheOrder.filter((item) => item !== key);
    }

    state.cache.set(key, value);
    state.cacheOrder.push(key);

    while (state.cacheOrder.length > CACHE_LIMIT) {
      const oldest = state.cacheOrder.shift();
      if (!oldest) {
        break;
      }
      state.cache.delete(oldest);
    }
  }

  function scoreRow(label, value) {
    const width = `${Math.round(value * 100)}%`;
    return `
      <div class="score-row">
        <span>${escapeHtml(label)}</span>
        <span class="score-bar" aria-hidden="true"><span style="width:${width}"></span></span>
        <span>${value.toFixed(2)}</span>
      </div>
    `;
  }

  function renderStructuredAnswer(value) {
    const normalized = normalizeStructuredAnswer(value);
    const keyPoints = normalized.key_points.length
      ? normalized.key_points
      : ["No key points were returned."];
    const evidence = normalized.evidence_used.length
      ? normalized.evidence_used
      : [{ source: "n/a", reason: "No explicit evidence mapping returned." }];

    const evidenceHtml = evidence
      .map((item) => {
        return `<li><strong>${escapeHtml(item.source)}</strong>: ${escapeHtml(item.reason)}</li>`;
      })
      .join("");

    return `
      <section class="answer-block">
        <h4>Answer</h4>
        <p>${escapeHtml(normalized.answer)}</p>
      </section>
      <section class="answer-block">
        <h4>Key Points</h4>
        <ul class="answer-list">
          ${keyPoints.map((line) => `<li>${escapeHtml(line)}</li>`).join("")}
        </ul>
      </section>
      <section class="answer-block">
        <h4>Evidence Used</h4>
        <ul class="answer-list">${evidenceHtml}</ul>
      </section>
      <section class="answer-block">
        <h4>Confidence</h4>
        <p>${escapeHtml(normalized.confidence)}</p>
      </section>
      <section class="answer-block">
        <h4>Limitations</h4>
        <p>${escapeHtml(normalized.limitations)}</p>
      </section>
    `;
  }

  function normalizeStructuredAnswer(value) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      const payload = value;
      const answer = typeof payload.answer === "string" ? payload.answer.trim() : "";
      const keyPoints = Array.isArray(payload.key_points)
        ? payload.key_points.map((item) => String(item)).filter((item) => item)
        : [];
      const evidenceUsed = Array.isArray(payload.evidence_used)
        ? payload.evidence_used
            .map((item) => {
              if (!item || typeof item !== "object") {
                return null;
              }
              const source = typeof item.source === "string" ? item.source.trim() : "n/a";
              const reason = typeof item.reason === "string" ? item.reason.trim() : "";
              if (!reason) {
                return null;
              }
              return { source, reason };
            })
            .filter(Boolean)
        : [];
      const confidenceRaw = String(payload.confidence || "").trim().toLowerCase();
      const confidence = ["high", "medium", "low"].includes(confidenceRaw)
        ? confidenceRaw
        : "medium";
      const limitations =
        typeof payload.limitations === "string" && payload.limitations.trim()
          ? payload.limitations.trim()
          : "No explicit limitations were returned.";

      return {
        answer: answer || "No answer available.",
        key_points: keyPoints,
        evidence_used: evidenceUsed,
        confidence,
        limitations,
      };
    }

    const sections = parseStructuredAnswer(typeof value === "string" ? value : "");
    return {
      answer: sections.concise.join(" ").trim() || "No answer available.",
      key_points: sections.evidence.length
        ? sections.evidence
        : ["No explicit evidence bullets were returned."],
      evidence_used: sections.citations.length
        ? sections.citations.map((citation) => ({ source: citation, reason: "Cited by response." }))
        : [],
      confidence: "medium",
      limitations: "Legacy answer format returned; structured evidence mapping is limited.",
    };
  }

  function parseStructuredAnswer(text) {
    const sections = {
      concise: [],
      evidence: [],
      citations: [],
      followUps: [],
    };

    const lines = String(text || "")
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);

    let current = "concise";
    lines.forEach((line) => {
      const lowered = line.toLowerCase();
      if (lowered.startsWith("1. concise answer")) {
        current = "concise";
        return;
      }
      if (lowered.startsWith("2. evidence bullets")) {
        current = "evidence";
        return;
      }
      if (lowered.startsWith("3. citations")) {
        current = "citations";
        return;
      }
      if (lowered.startsWith("4. suggested follow-up questions")) {
        current = "followUps";
        return;
      }

      const cleaned = line.replace(/^-+\s*/, "").trim();
      if (!cleaned) {
        return;
      }
      sections[current].push(cleaned);
    });

    if (
      sections.concise.length === 0 &&
      sections.evidence.length === 0 &&
      sections.citations.length === 0 &&
      sections.followUps.length === 0
    ) {
      sections.concise.push(String(text || "").trim());
    }

    return sections;
  }

  function sortPapers(papers, sortOrder) {
    const rows = papers.slice();

    rows.sort((left, right) => {
      if (sortOrder === "recency") {
        const leftDate = parseDateScore(left.published_date);
        const rightDate = parseDateScore(right.published_date);
        if (rightDate !== leftDate) {
          return rightDate - leftDate;
        }
      }

      const leftScore = normalizeScore(left.relevance_score);
      const rightScore = normalizeScore(right.relevance_score);
      if (rightScore !== leftScore) {
        return rightScore - leftScore;
      }

      return String(left.title || "").localeCompare(String(right.title || ""));
    });

    return rows;
  }

  function parseDateScore(isoDate) {
    if (!isoDate) {
      return 0;
    }
    const timestamp = Date.parse(isoDate);
    if (Number.isNaN(timestamp)) {
      return 0;
    }
    return timestamp;
  }

  function asChipList(values, extraClass) {
    if (!Array.isArray(values) || values.length === 0) {
      return "";
    }

    return values
      .map((value) => {
        return `<span class="pill ${extraClass || ""}">${escapeHtml(String(value))}</span>`;
      })
      .join("");
  }

  function normalizeScore(raw) {
    const value = Number(raw);
    if (!Number.isFinite(value)) {
      return 0;
    }
    return Math.max(0, Math.min(1, value));
  }

  function updateGraphExpansionLabel() {
    if (!dom.graph.expansionValue) {
      return;
    }
    dom.graph.expansionValue.textContent = String(clampGraphExpansion(state.graph.expansionDepth));
  }

  function clampGraphExpansion(rawDepth) {
    const parsed = Number.parseInt(String(rawDepth), 10);
    if (!Number.isFinite(parsed)) {
      return 2;
    }
    return Math.max(1, Math.min(GRAPH_MAX_EXPANSION, parsed));
  }

  function parseBootstrap(text) {
    if (!text) {
      return null;
    }
    try {
      return JSON.parse(text);
    } catch (_error) {
      return null;
    }
  }

  function syncUrl() {
    const nextParams = new URLSearchParams();
    if (state.query) {
      nextParams.set("query", state.query);
    }
    nextParams.set("clearance", state.clearance);
    nextParams.set("tab", state.activeTab);
    nextParams.set("sort", state.sortOrder);

    const askValue = (dom.ask.input ? dom.ask.input.value : "").trim();
    if (askValue) {
      nextParams.set("ask_query", askValue);
    }

    const nextUrl = `${window.location.pathname}?${nextParams.toString()}`;
    window.history.replaceState({}, "", nextUrl);
  }

  function showElement(element) {
    if (!element) {
      return;
    }
    element.hidden = false;
  }

  function hideElement(element) {
    if (!element) {
      return;
    }
    element.hidden = true;
  }

  function isAbortError(error) {
    if (!error) {
      return false;
    }
    return error.name === "AbortError";
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function slug(value) {
    return String(value || "")
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "unknown";
  }

  function truncateLabel(value, maxChars) {
    const text = String(value || "").trim();
    if (!text) {
      return "";
    }
    if (text.length <= maxChars) {
      return text;
    }
    if (maxChars <= 3) {
      return text.slice(0, maxChars);
    }
    return `${text.slice(0, maxChars - 3)}...`;
  }

  function debounce(fn, waitMs) {
    let timeoutId = null;
    return function debounced(...args) {
      if (timeoutId) {
        clearTimeout(timeoutId);
      }
      timeoutId = window.setTimeout(() => {
        fn.apply(this, args);
      }, waitMs);
    };
  }
})();
