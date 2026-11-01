# Step 6: Node Construction and Entity Alignment

> Prerequisite: Step 5B (verification supplement) complete (`04_verified_image_data.json` + `04c_errata.json` ready, `severity` backfilled in `07_contradiction_ledger.json`).
> Executed by: one `fenton-node` per paper (node inventory) → orchestrator script (entity alignment).

## Dispatch

```
delegate_task(tasks=[
  {"profile": "fenton-node",
   "goal": "Node inventory: read the full text and enumerate the four node layers — catalyst / pollutant / experiment / variable; register each variable's parameter name + declared_levels (every level the authors swept, listed value by value), **nested inside the corresponding experiment node's variables[]** (never flattened at top level); do not fill indicator values for the levels",
   "context": "<paper folder path>"},
  # one entry per paper
])
```

Optional: **one shared instance for the whole corpus** to merge material families (reads every `05_node_inventory.json` and emits a material-family list for manual review).

## Subagent return format

The **field definitions and examples are defined in** `backend/agents/fenton-node/system.md` (single source of truth; not repeated here).
The orchestrator checks only: the four layers are fully referenced, variables register a parameter name + `declared_levels` (value by value, not a prose sentence) and are nested inside the corresponding experiment node's `variables[]` (no top-level flattened `variables`), one pollutant per experiment, and every node carries `evidence`.

## Orchestrator close-out

1. Persist `05_node_inventory.json` per paper (the JSON itself + `_meta`: agent/status/confidence/rounds/tools_used);
2. Run `step5b1_pollutant_align.py <paper folder or corpus root>` for entity alignment (auto-adapts to single or multiple papers):

| Product (in the ledger directory) | Content |
|---|---|
| `<corpus>_pollutant_alignment.json` | Pollutant → CID / molecular formula / molecular weight / InChIKey / CAS (used by the Step 10 export join) |
| `<corpus>_catalyst_alignment.json` | Catalyst → family key `material_family` + same-family nodes already in the graph |
| `<corpus>_entity_alignment.json` | The above combined + `summary` (hit count, pending-merge count) |

Ledger directory: single paper `<paper>/datamining/_ledgers/`; multi-paper `<corpus root>/_ledgers/`.
Optional flags: `--no-net` (graph only, no network), `--dry-run` (match without writing).

## Alignment policy (the two entity classes differ; do not mix them)

- **Pollutants** (finite set, same substance across papers) — **converge to a single node**:
  (1) query existing graph nodes first (normalized name / English name / abbreviation expansion match) → on a hit, reuse its CID / CAS / InChIKey, which is consistent across papers and saves network calls; (2) on a miss → PubChem → NCI CACTUS as fallback.
  `graph_hits > 1` in the alignment table means the same substance has already split into several nodes in the graph (legacy debt, pending merge, **does not block the pipeline**).
- **Catalysts** (extremely varied: loading / doping ratio / support) — **do not merge nodes**:
  compute only the family skeleton `material_family` as the family key (algorithm strictly identical to the Step 10 export), and record same-family nodes already in the graph.
  Cross-paper usage = aggregate by family, distinguish by variant:
  `MATCH (f {material_family:'Fe3O4'})…` compared variant by variant, instead of merging variants into one.

## Pitfalls

- The graph is a **remote Neo4j**; idle connections are reclaimed → the first call may fail. The script retries three times automatically;
  if it still fails, rerun later and **never conclude from that failure that the graph holds no data**.
- The pollutant node label is `:Entity {label:'Pollutant'}`, **not** `:Pollutant`.

## Verification

1. The catalyst / pollutant / experiment references are complete;
2. Variables register a parameter name and `declared_levels` (level values one by one), and **no** indicator values for the levels (that belongs to Step 7);
3. One pollutant per experiment (no co-listed strings);
4. Every node carries evidence (section location);
5. All three alignment tables exist; no pollutant is `unresolved` (if any, retry manually with synonyms); every catalyst `family` is non-empty.
