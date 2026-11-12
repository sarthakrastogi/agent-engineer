---
name: agent-rag
description: >-
  Decides whether an agent needs retrieval at all, then designs, measures and debugs the
  pipeline: parsing, chunking, contextual retrieval, embeddings, hybrid BM25 + dense,
  reranking, filters, context assembly, citations, agentic search, retrieval metrics. Use
  when the user wants answers "from our docs/data", mentions embeddings, a vector DB, chunk
  size or semantic search, or a RAG system gives wrong, outdated or uncited answers or
  misses content that exists. Not for context accumulated over a run, memory or compaction
  (agent-context), or judge design beyond retrieval metrics (agent-evals).
license: MIT
metadata:
  version: 0.4.0
---

# Agent RAG

Retrieval is a search problem: measure it with retrieval metrics on a labelled set,
separately from the answer.

## Core rules

1. **Decide if retrieval is needed first.** RAG fixes missing knowledge, not behaviour.
2. **Under ~200k tokens, try the whole corpus in the prompt with caching** before an index,
   and measure it.
3. **Build a labelled retrieval set before tuning:** 20–50 query → relevant-span pairs,
   real queries first.
4. **Score retrieval apart from generation.** Recall@k at candidate k and final k first;
   faithfulness and answer relevance separately.
5. **Default to hybrid BM25 + dense, then rerank** to a few chunks. Dense alone misses IDs,
   codes and jargon.
6. **Chunking and embeddings are hyperparameters.** Grid-search on your data; leaderboards
   only make the shortlist.
7. **Diagnose at the first failing stage** before changing anything.
8. **Cheap fixes before infrastructure:** chunk headers, BM25, filters, rerank, k — before
   a new embedder, vector DB or framework.
9. **Permissions are filters applied in code** from the authenticated user, never left to
   the model. Retrieved text is untrusted input once the agent can act.
10. **Ask what "relevant" and "correct" mean;** a domain expert labels.

## Workflow

1. **Classify the need.** Read `design.md` and 5–10 failing examples. Ask corpus size,
   change rate, query types, latency/cost budget, per-user access. Pick prompt + cache,
   hybrid RAG or agentic search (`references/retrieval-or-not.md`); record it in `design.md`.
2. **Inspect ingestion.** Read the parsed output of ~20 documents; fix tables, PDFs,
   headings, metadata (`references/chunking-and-ingestion.md`).
3. **Label** the retrieval set in `datasets/` (`references/rag-evaluation.md`).
4. **Baseline:** hybrid + structure-aware chunks with headers. Record recall@k at candidate
   and final k, latency, cost.
5. **Tune one variable at a time:** chunking grid, contextual retrieval, reranker and k,
   filters, query transformation (`references/retrieval-and-ranking.md`). One
   `experiment-log.md` entry each.
6. **Assemble and ground:** fewer, better chunks, best at the edges, source IDs on each,
   citations checked in code.
7. **Evaluate generation:** faithfulness, answer relevance, abstention, with validated judges.
8. **Diagnose failures** stage by stage (`references/diagnosing-rag.md`); delegate to
   `rag-diagnostician` if available.
9. **Next:** `agent-observability` for retrieval spans, `agent-evals` for gates and online
   scoring, `agent-accuracy` if failures persist once retrieval is sound.

## Symptom → fix

| If | Then |
|---|---|
| Failures are format, tone or consistency | Not RAG → `agent-prompting` / `agent-accuracy` |
| Right chunk in BM25 results, not dense | Hybrid with RRF |
| Right chunk in neither | Chunk headers / contextual retrieval, then query rewriting |
| Retrieved but cut off or filtered out | Reranker, tune k, fix the filter |
| "Latest", dated, typed or hard-constraint queries ("under $200", "not X") | Extract filters; search only within them |
| Exact search finds it, the ANN index doesn't | Tune the index (`references/retrieval-and-ranking.md` → Vector index) |
| Multi-hop across entities after hybrid + rerank | Agentic search, then graph retrieval, measured |
| Right chunk in context, answer wrong | Generation: quote-first, allow "I don't know", ordering |
| Chat follow-ups retrieve badly | Condense history into a standalone query |
| Query transform unproven on your set, or ID-like queries | Skip it |

## Anti-patterns

- Choosing a vector DB or framework before knowing corpus size and query types.
- Copying 800-token / 400-overlap chunk defaults (they score worst).
- ROUGE, BERTScore or cosine similarity to grade answers.
- Tuning the prompt before retrieval recall is known.
- Stuffing top-50 chunks into context "to be safe".
- Synthetic-only eval sets; labels keyed to chunk IDs that break on re-chunk.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- Retrieval decision and rejected options in `design.md`.
- `datasets/retrieval-<name>.jsonl` with provenance; retrieval and generation rows in
  `eval-plan.md`.
- One `experiment-log.md` entry per pipeline change: recall delta, p95 latency, cost/query.
- Diagnosis reports as stage tallies (`rag-diagnostician` format).

## References

- `references/retrieval-or-not.md` — knowledge vs behaviour, prompt + cache vs RAG vs
  agentic search. Read before building anything.
- `references/chunking-and-ingestion.md` — parsing, tables, chunk-size grid, per-type
  routing, contextual retrieval, parent–child. Read when building or re-indexing.
- `references/retrieval-and-ranking.md` — embeddings, hybrid, rerank, query transforms,
  filters, assembly, citations, agentic and graph retrieval. Read when tuning query time.
- `references/rag-evaluation.md` — metrics, constraint satisfaction, building the labelled
  set, recall@k snippet. Read before tuning and when writing `eval-plan.md`.
- `references/diagnosing-rag.md` — logging, first-failing-stage walk, fix order, query
  clusters, flywheel. Read when answers are wrong or quality is unknown.
