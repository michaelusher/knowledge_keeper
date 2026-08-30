# knowledge-keeper

Knowledge preservation for organizations: ingest training documents, PowerPoints, PDFs, and runbooks into a cloud RAG database that supplements an LLM — then go further and **generate system documentation from the corpus and surface gaps, risks, and contradictions** in what your organization has written down.

Two systems share one ingestion pipeline:

1. **RAG assistant** — ask questions, get answers grounded in your documents with citations back to the exact slide/page/section.
2. **Analysis layer** — a corpus-wide knowledge map that powers gap analysis (undocumented topics, bus-factor risk, stale docs, conflicting procedures) and a generated Markdown system report.

Runs against **Azure** (AI Search + Azure OpenAI), **AWS** (OpenSearch Serverless + Bedrock), or fully **offline in local mode** for development and demos — same code, one config switch.

```
                        ┌─────────────────────────────────────────────┐
  pptx  docx  pdf  md   │              INGESTION PIPELINE             │
   │     │    │    │    │  extract → chunk (section-aware) → embed    │
   └─────┴────┴────┴──▶ │  preserves: author, dates, slide/page locs  │
                        └────────────────┬────────────────────────────┘
                                         ▼
                        ┌─────────────────────────────────────────────┐
                        │   VECTOR STORE (hybrid search)              │
                        │   Azure AI Search │ OpenSearch │ local      │
                        └───────┬─────────────────────────┬───────────┘
                                ▼                         ▼
                  ┌──────────────────────┐   ┌──────────────────────────┐
                  │  RAG (kk ask, /ask)  │   │  ANALYSIS (kk analyze)   │
                  │  cited answers       │   │  knowledge map           │
                  └──────────────────────┘   │  → gaps / bus factor     │
                                             │  → staleness / conflicts │
                                             │  → system report (md)    │
                                             └──────────────────────────┘
```

## Quick start

### Linux — Fedora / RHEL (pipx — recommended, no venv juggling)

```bash
sudo dnf install pipx python3-pip
cd knowledge-keeper
pipx install -e .          # installs the `kk` command onto your PATH permanently
```

### Linux — Ubuntu / Debian

```bash
sudo apt update && sudo apt install -y pipx python3-pip python3-venv
pipx ensurepath            # then open a NEW terminal so PATH updates apply
cd knowledge-keeper
pipx install -e .
```

Ubuntu notes:
- `python3-venv` must be installed explicitly (unlike Fedora) — without it, pipx and venv creation
  both fail with a cryptic ensurepip error.
- On older releases (22.04 and earlier) the packaged pipx can be outdated; if `pipx install -e .`
  misbehaves, use `python3 -m pip install --user pipx` instead.
- Everything else — demo commands, pipx venv path (`~/.local/share/pipx/venvs/...`), Gemini key in
  `~/.bashrc` — is identical to the Fedora instructions.

The `-e` (editable) install means the extracted folder is the live code — keep it where it is.
Then run the offline demo (no cloud, no keys):

```bash
# the demo-corpus script needs the project's own Python (pipx keeps deps isolated):
~/.local/share/pipx/venvs/knowledge-keeper/bin/python scripts/make_sample_corpus.py
kk ingest ./sample_corpus
kk ask "What happens if the nightly reconciliation fails?"
kk analyze --no-llm
kk report --no-llm -o system_report.md
kk serve                   # HTTP API at :8000/docs
```

Run `kk` commands from inside the project folder — the index (`kk_data/`) is created wherever you run them.

### macOS (pipx via Homebrew)

```bash
brew install pipx && pipx ensurepath      # install Homebrew first from https://brew.sh if needed
# open a NEW terminal so PATH updates take effect, then:
cd knowledge-keeper
pipx install -e .
```

Demo commands are identical to Fedora's above, with one path difference — pipx keeps its
environments elsewhere on macOS, so the corpus script runs as:

```bash
"$(pipx environment --value PIPX_LOCAL_VENVS)/knowledge-keeper/bin/python" scripts/make_sample_corpus.py
```

If Homebrew's pipx doesn't find a Python, `brew install python` first. Apple Silicon and Intel both work.

### Windows (PowerShell + pipx)

Install Python 3.10+ from https://python.org (check **"Add python.exe to PATH"** during install —
the most common Windows setup failure is skipping this), then in PowerShell:

```powershell
py -m pip install --user pipx
py -m pipx ensurepath
# open a NEW PowerShell window, then:
cd knowledge-keeper
pipx install -e .
```

Run the demo with Windows-style paths:

```powershell
& "$env:USERPROFILE\pipx\venvs\knowledge-keeper\Scripts\python.exe" scripts\make_sample_corpus.py
kk ingest .\sample_corpus
kk ask "What happens if the nightly reconciliation fails?"
kk analyze --no-llm
kk report --no-llm -o system_report.md
```

Windows notes:
- If `kk` isn't recognized after install, you skipped the "new window" step — `ensurepath` edits PATH
  for *future* shells only.
- If the pipx venvs path above doesn't exist, find it with `pipx environment --value PIPX_LOCAL_VENVS`.
- Setting the Gemini key permanently on Windows: `setx GEMINI_API_KEY "AIza...your-key"` (then open a
  new window). For the current session only: `$env:GEMINI_API_KEY = "AIza...your-key"`.
- WSL (Windows Subsystem for Linux) also works and behaves like the Fedora instructions — a good
  option if you're comfortable with Linux tooling.

### Any platform (classic venv — no pipx)

Linux/macOS:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e . && pip install pytest
python -m pytest tests/ -v          # 7 tests should pass
```

Windows PowerShell:
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -e . ; pip install pytest
python -m pytest tests\ -v
```

With a venv active, `python scripts/make_sample_corpus.py` works directly (no pipx path tricks),
but remember to re-activate the venv in every new terminal before using `kk`.

## Free LLM setup (Gemini)

Without an LLM, `kk ask` returns raw passages and `kk analyze` runs heuristics only. The fastest free
upgrade is Google's Gemini free tier (no credit card):

1. Get a key at https://aistudio.google.com (it starts with `AIza...`).
2. Put it in your environment — **never in a file, chat, or commit**:
   ```bash
   # Linux / macOS:
   echo 'export GEMINI_API_KEY=AIza...your-key' >> ~/.bashrc && source ~/.bashrc
   # (macOS default shell is zsh: use ~/.zshrc instead of ~/.bashrc)
   ```
   ```powershell
   # Windows PowerShell (permanent; open a new window afterwards):
   setx GEMINI_API_KEY "AIza...your-key"
   ```
3. `cp config.example.yaml config.yaml` and set `llm: { provider: gemini }`.

Now `kk ask` gives synthesized, cited answers — including explicitly saying when the corpus doesn't
contain the answer — and `kk analyze` / `kk report` unlock semantic topic extraction, cross-document
conflict detection, and written documentation sections.

**Free-tier realities** (learned the hard way):
- **Models get retired.** If you hit `404 ... no longer available`, the error names the successor —
  update `gemini.model` in config.yaml. Default here is `gemini-3.6-flash`.
- **Capacity spikes happen.** On `503 UNAVAILABLE` the client retries automatically (5 attempts,
  exponential backoff, visible progress). If all retries fail, wait a few minutes; off-peak hours are smoother.
- If a key is ever pasted anywhere public, rotate it at aistudio.google.com immediately.

## Working with your own documents

```bash
mkdir -p my_docs && cp ~/Downloads/some-file.pdf my_docs/
kk ingest ./my_docs                 # adds to the index; re-runs skip unchanged files
kk remove some-file                 # drop one document (partial filename match)
rm -rf kk_data/local                # nuclear option: wipe the local index entirely
```

Notes that save head-scratching:
- **Ingest adds, never replaces.** Both old and new documents stay searchable until you `kk remove`
  or wipe. Citations always show which document answered.
- **PDFs must contain real text.** Digitally-created PDFs work; scanned ones extract nothing — run
  `ocrmypdf in.pdf out.pdf` first (Fedora: `sudo dnf install ocrmypdf`; Ubuntu/Debian: `sudo apt install ocrmypdf`; macOS: `brew install ocrmypdf`;
  Windows: easiest via WSL, or see ocrmypdf.readthedocs.io for native install). Quick test: if you can select text
  in a PDF viewer, it will ingest.
- **Single-document corpora analyze flat.** Findings (gaps, bus factor, conflicts) are mostly
  cross-document signals; expect them once several related documents are indexed, not from one book.
- **Answers report what the corpus says, not what is true.** Ask "according to this document, ..."
  and read the citations as provenance — fringe sources get faithfully summarized, and "the text
  doesn't address this" is a correct answer, not a failure.
- Local mode's hashing embedder matches vocabulary, not meaning — phrase questions using the
  document's own words, or raise `--top-k`.

## Switching between local, Azure, and AWS

Three ways, in order of precedence:

```bash
kk -p azure ask "How does failover work?"   # one-off: -p/--provider flag on any command
kk use aws                                  # persistent: sets the default for all future commands
# lowest precedence: provider: in config.yaml / KK_PROVIDER env var
```

`kk status` shows which backend is active, whether its dependencies are installed and its
config filled in, and how many documents each backend has ingested:

```
Active provider: aws  (set via `kk use aws`)

│ Provider │ Dependencies              │ Configured │ Ingested docs │
│   local  │ installed                 │ yes        │ 4             │
│   azure  │ pip install -e '.[azure]' │ no         │ —             │
│ ▶ aws    │ installed                 │ yes        │ —             │
```

Each backend's state (ingestion manifest, document registry, knowledge map, and the local
vector store) is isolated under `kk_data/<provider>/`, so switching never cross-contaminates:
a freshly configured cloud backend starts empty and `kk ingest` correctly re-indexes everything
into it, while your local experiments stay intact. A typical flow: develop everything against
`local` for free, then `kk use azure && kk ingest ./docs` to populate the real deployment —
and `kk -p local ask ...` any time to compare.

## CLI

| Command | What it does |
|---|---|
| `kk use <provider>` | Set the default backend (local / azure / aws). Persists across commands. |
| `kk status` | Show active backend, dependency/config readiness, per-backend document counts. |
| `kk ingest <dir>` | Walk a directory, extract/chunk/embed/index all supported files. Incremental: unchanged files (by SHA-256) are skipped; changed files are re-indexed. |
| `kk ask "<question>"` | Hybrid retrieval + LLM answer with inline `[S1]` citations resolving to document + slide/page/section. |
| `kk analyze` | Build the knowledge map (topics × documents × authors × freshness) and run all gap-analysis passes. Writes `kk_data/knowledge_map.json`. |
| `kk report -o out.md` | Generate the system report: inventory, coverage matrix, LLM-synthesized per-topic documentation (with attribution), and all findings. |
| `kk remove <filename>` | Drop one document from the index (partial filename match). |
| `kk serve` | FastAPI server: `/ask`, `/ingest`, `/analyze`, `/findings`, `/topics`, `/documents`, `/report`, `/healthz`. |

## What the gap analysis finds

| Finding | Signal | Why it matters |
|---|---|---|
| **gap** | Topic referenced ≥ N times across docs but explained nowhere | The knowledge exists only in people's heads |
| **bus_factor** | All docs covering a topic trace to ≤ 1 author | One departure loses the documented custodian |
| **stale** | Newest covering doc older than a cutoff (default ~18 months) | Docs may no longer match the system |
| **single source** | Topic documented in exactly one file | No redundancy; one file rots, topic is gone |
| **conflict** (LLM) | Multiple docs give contradictory procedures/owners/values | Someone will follow the wrong one during an incident |
| **orphan** | Document has no author metadata | Nobody to ask when it stops making sense |

Heuristic passes always run. With an LLM configured, topic extraction becomes semantic (systems vs. procedures vs. roles, *mentioned* vs. *explained*) and conflict detection activates. Thresholds live in `config.yaml → analysis`.

## Deploying on Azure

1. **Provision** — `infra/azure/main.bicep` deploys Azure AI Search (semantic ranker enabled) + Azure OpenAI with `text-embedding-3-large` and `gpt-4o` deployments:
   ```bash
   az group create -n kk-rg -l eastus2
   az deployment group create -g kk-rg -f infra/azure/main.bicep -p namePrefix=kkprod
   ```
2. **Grant access** — assign your identity (or the app's managed identity) `Search Index Data Contributor` on the search service and `Cognitive Services OpenAI User` on the OpenAI account. Leave API keys empty in config to use `DefaultAzureCredential`.
3. **Configure**:
   ```yaml
   provider: azure
   llm: { provider: azure_openai }
   azure:
     search_endpoint: https://kkprod-search.search.windows.net
     openai_endpoint: https://kkprod-aoai.openai.azure.com
   ```
4. `pip install -e ".[azure]"` then `kk ingest <dir>` — the index is created automatically with vector + keyword + semantic-ranker hybrid search.

## Deploying on AWS

1. **Provision** — `infra/aws/main.tf` creates an OpenSearch Serverless VECTORSEARCH collection with encryption/network/data-access policies, plus an IAM policy for Bedrock invocation:
   ```bash
   cd infra/aws && terraform init
   terraform apply -var name_prefix=kkprod -var principal_arn=arn:aws:iam::<acct>:role/<runner-role>
   ```
2. **Enable models** — in the Bedrock console (once per account/region), enable *Titan Text Embeddings V2* and your Claude model under Model access.
3. **Configure**:
   ```yaml
   provider: aws
   llm: { provider: bedrock }
   aws:
     region: us-east-1
     opensearch_endpoint: <terraform output opensearch_endpoint>
   ```
4. `pip install -e ".[aws]"` then `kk ingest <dir>`. Auth follows the standard boto3 chain (env vars / profile / instance role).

You can also mix providers — e.g. AWS vector store with the direct Anthropic API as the LLM (`llm: {provider: anthropic}` + `ANTHROPIC_API_KEY`).

## Configuration & secrets

Copy `config.example.yaml` → `config.yaml`. Every value is overridable by environment variables with `KK_` prefix and `__` nesting:

```bash
export KK_PROVIDER=azure
export KK_LLM__PROVIDER=azure_openai
export KK_AZURE__SEARCH_ENDPOINT=https://...
```

Prefer managed identity (Azure) / IAM roles (AWS) over keys. If you must use keys, inject them as env vars from Key Vault / Secrets Manager — never commit them.

## Docker

```bash
docker build -t knowledge-keeper .
docker run -p 8000:8000 -v $(pwd)/kk_data:/data \
  -e KK_PROVIDER=azure -e KK_LLM__PROVIDER=azure_openai \
  -e KK_AZURE__SEARCH_ENDPOINT=... -e KK_AZURE__OPENAI_ENDPOINT=... \
  knowledge-keeper
```

Deploy the image to Azure Container Apps or ECS/Fargate; mount `/data` on durable storage (the knowledge map and ingestion manifest live there). For scheduled re-ingestion + re-analysis, run `kk ingest && kk analyze && kk report` as a cron job / scheduled task against a mounted document share or a synced S3/Blob copy of your document library.

## Design notes

- **Metadata is the product.** Author, modified date, and slide/page location are captured at extraction and carried through chunks into the index. Staleness, bus-factor, and citations are all downstream of that decision — if you extend the extractors, keep provenance intact.
- **Section-aware chunking.** Slides, pages, and headed sections are semantic units; the chunker never merges across them, splits oversized ones on sentence boundaries with overlap, and coalesces tiny title-only slides.
- **Speaker notes are extracted.** On training decks the notes often carry the actual knowledge; they're indexed with a `[Speaker notes]` marker.
- **Office metadata over filesystem mtime.** Embedded core properties survive file copies; fs mtime doesn't. (Beware: files generated by python-docx/pptx default templates carry a bogus 2013 date.)
- **Incremental ingestion.** SHA-256 manifest → unchanged files skipped, changed files atomically replaced in the index. Safe to run on a schedule.
- **Analysis is bounded.** LLM conflict-detection is capped (top 40 multi-doc topics) and report synthesis capped at 25 sections; raise the caps once you've seen cost/quality on your corpus.

## Extending

- **More formats** — register an extractor in `ingestion/extractors.py` (returns `(SourceDocument, [RawSection])`). Good next targets: `.xlsx`, `.html`, email exports, Confluence/SharePoint API pulls.
- **Vision-heavy decks** — for diagram-dense slides, add a pass that renders slides to images and asks a vision model to describe them, appending descriptions as sections. The chunker and everything downstream need no changes.
- **Access control** — add an ACL field to `Chunk`, populate at ingestion, filter at query time (both Azure AI Search and OpenSearch support security filters).
- **Evaluation** — before tuning retrieval, build a small golden set of (question, expected-doc) pairs from your corpus and track hit-rate as you change chunking/embedding settings.

## Publishing to GitHub safely

`.gitignore` already excludes `config.yaml`, `kk_data/`, `.env`, and reports. Before your first push,
also exclude your documents (often copyrighted) and verify nothing sensitive is staged:

```bash
echo "my_docs/" >> .gitignore
git init && git add . && git status    # confirm: no config.yaml, kk_data/, PDFs, or reports listed
git commit -m "knowledge-keeper"
gh repo create knowledge-keeper --public --source=. --push
```

API keys live only in environment variables; code reads them at runtime. That single habit is what
makes the repo safe to publish.

## Tests

```bash
pip install pytest && python -m pytest tests/ -v
```

Covers extraction fidelity (incl. speaker notes), incremental re-ingestion, metadata preservation, retrieval, planted-issue detection, and report generation — all offline.
