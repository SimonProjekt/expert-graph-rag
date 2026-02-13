

# Expert Graph RAG — Frontend

## Overview
A production-grade React + TypeScript frontend for the Expert Graph RAG backend. Features a tabbed interface for searching papers, exploring experts, visualizing knowledge graphs, and asking questions — all with clearance-level filtering.

## App Shell & Layout
- **Top navigation bar** with app title, status chips (Demo Mode, LLM status, OpenAlex status), and a clearance level selector (Public / Internal / Confidential)
- **Left control panel** with search input (300ms debounce, abort-on-change) and result metadata (timing, count, redaction banner)
- **Main content area** with four accessible ARIA tabs: Papers, Experts, Graph, Ask

## Papers Tab
- Card list showing title, snippet, authors (as badges), topics, and published date
- Relevance score with visual breakdown bars (semantic relevance, graph authority, graph centrality)
- Collapsible "Why this paper?" section showing `why_matched` text and `graph_path`
- Sort toggle: relevance vs. recency

## Experts Tab
- Ranked expert cards with score breakdown bars and explanation text

## Graph Tab (vis-network)
- Interactive network visualization with color-coded node types: Query, Paper, Author, Topic
- Legend and filter controls to show/hide node types
- Zoom-to-fit and reset view buttons
- Click a node to open a right-side detail panel
- Highlight shortest path from query to clicked node

## Ask Tab
- Question input with answer display panel
- Citations list linking back to source papers
- Recommended experts list

## States & UX
- Empty state with friendly prompt copy
- Loading skeletons for all data-fetching views
- Error state with retry button
- Enterprise-clean visual style

## Architecture & Quality
- **API layer**: Reusable fetch client with AbortController support and client-side LRU cache (last 10 requests)
- **Typed models**: Full TypeScript interfaces for all API responses
- **Feature components**: Modular per-tab components
- **Shared UI primitives**: Using shadcn/ui components already installed
- **Accessibility**: WCAG-friendly focus states, keyboard navigation, ARIA attributes on tabs
- **Smoke test**: Route render test + one mocked API search flow
- **README**: Run instructions, `VITE_API_BASE_URL` env var documentation, and demo walkthrough

