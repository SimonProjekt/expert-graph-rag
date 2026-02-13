# Graph RAG Excellence Prompt

You are a Staff-level AI/backend + product engineer working inside an existing Django + DRF + pgvector + Neo4j repo called `expert-graph-rag`.

Mission:
Turn the project into an interview-grade telecom expert discovery demo that is visibly strong in relevance, explainability, and UX polish.

Constraints:
- Work incrementally in the existing architecture; do not rewrite from scratch.
- Keep secrets out of code. Use env vars only.
- Keep dependencies minimal.
- Every change must preserve clearance-aware security (PUBLIC/INTERNAL/CONFIDENTIAL).
- Add/adjust tests for new behavior.

Deliverables:
1. Retrieval relevance upgrades
- Improve ranking quality for `/api/search` by combining:
  - semantic similarity
  - query-term alignment (title + abstract + topics)
  - graph authority and centrality
- Add explainability fields that show why a result ranked.
- Ensure off-topic papers are demoted for telecom queries.

2. Better expert ranking clarity
- Improve `/api/experts` to include clear score breakdown and matched-paper evidence.
- Ranking must avoid “paper count only” bias.
- Return strong, concise `why_ranked` text and top supporting papers.

3. Knowledge graph UX excellence
- Improve graph readability and interactivity:
  - reduced clutter by default
  - meaningful node sizing
  - optional collaborator edges
  - click-to-inspect details
  - path highlighting
- Keep graph responsive and stable under larger result sets.

4. Data quality and ingestion relevance
- Improve OpenAlex ingestion quality to favor query-aligned telecom works.
- Keep idempotent upserts.
- Add tunable env vars for relevance strictness.
- Ensure demo seed command builds a strong telecom dataset.

5. Landing/demo polish
- Tighten product copy for interview storytelling.
- Keep first-run journey obvious: query -> papers -> experts -> graph -> ask.
- Remove unnecessary labels/noise in top navigation.

Acceptance criteria:
- Search returns visibly more query-aligned telecom papers.
- Experts output is easier to understand and defend.
- Graph tab is less noisy and more informative.
- Demo looks cleaner and feels intentional.
- Tests pass.
