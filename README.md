# support-agent

A customer-support agent backend built on **Claude tool use**, over a seeded fake shop (customers, products, orders, help articles). The agent can look up orders, search the help centre, start returns, issue refunds and hand over to a person.

The agent loop is **written by hand** (no agent framework). The safety properties live in code, not in the prompt:

- **Customer scoping.** The customer is fixed on the conversation server-side, and every tool enforces it, so no message can make the model reach another customer's data.
- **Human approval.** Refunds above a threshold wait for a person. Their decision becomes the tool result, and the conversation continues from there.
- **Audit.** Every tool call is recorded, with input, output, duration and approver.

**What runs where:** everything runs locally with Docker, with **no API key**. The default model is a deterministic, rule-based fake that drives the same loop. Adapters for the **Anthropic API** (`claude-opus-5-5`, streaming, server-side refusal fallback) and **Amazon Bedrock** (Mantle client) use the official `anthropic` SDK, but the tests never call them, and they have not been run against the real APIs. The project is **not deployed**.

**Stack:** Python 3.12, uv, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, `anthropic` SDK 2.x, pytest, ruff, PostgreSQL 16 (including full-text search).

## Architecture

```mermaid
flowchart LR
  Shop["Shop backend<br/>(knows the logged-in customer)"] -->|"client key<br/>POST /conversations {customerId}<br/>POST …/messages → SSE"| API
  Staff[Support staff] -->|"staff key<br/>approve / reject, audit"| API

  subgraph API["FastAPI"]
    LOOP["Agent loop<br/>(conversations/agent.py)"]
    REG["Tool registry<br/>6 tools, Pydantic inputs"]
    ACT[Actions: approvals]
    LOOP --> REG
    ACT -->|"decision as tool_result,<br/>then resume"| LOOP
  end

  LOOP --> LLM{{LLMProvider}}
  LLM --> FAKE["fake (rule-based)"]
  LLM -.-> ANT["Anthropic API<br/>claude-opus-5-5"]
  LLM -.-> BR["Amazon Bedrock<br/>(Mantle)"]
  REG -->|"customer from the<br/>conversation, never the model"| PG[(PostgreSQL<br/>shop, conversations,<br/>audit, FTS)]
```

Dashed lines are the paid providers, selected with `LLM_PROVIDER`. Tests use a fourth, **scripted** provider, which plays exact model turns.

```
app/
  core/        settings, logging, errors, pagination/envelopes, ApiModel, auth (client/staff keys), db
  providers/llm/  base (LLMProvider, LLMTurn), anthropic_provider (API + Bedrock), fake (rule-based),
                  scripted (tests), factory
  modules/     conversations (+ agent loop, state machine), messages, tools, actions, help_articles,
               audit, shop (+ seed)
  container.py composition root        main.py  API
scripts/       start_with_secrets.py, seed.py, eval.py
eval/scenarios/  eval cases: customer message → tools that must / must not be called
tests/         unit/, integration/ (real Postgres, scripted model)
```

## Data model

```mermaid
erDiagram
  customers ||--o{ orders : places
  orders ||--|{ order_items : contains
  products ||--o{ order_items : "sold as"
  orders ||--o| returns : "returned by"
  orders ||--o{ refunds : "refunded by"
  customers ||--o{ conversations : "talks in"
  conversations ||--o{ messages : holds
  conversations ||--o{ llm_calls : costs
  conversations ||--o{ actions : "waits on"
  conversations ||--o{ tool_calls : audits

  orders {
    uuid id PK
    string number UK "ORD-1001"
    uuid customer_id FK
    string status
    int total_cents
  }
  refunds {
    uuid order_id FK
    int amount_cents
    string approved_by "null below the threshold"
  }
  conversations {
    uuid id PK
    uuid customer_id FK "fixed at creation"
    string status "active, responding, awaiting_approval, escalated"
    int input_tokens
    int output_tokens
    decimal cost_usd
    int token_budget
  }
  messages {
    uuid conversation_id FK
    int seq
    string role "user or assistant"
    json content "API content blocks, replayed unchanged"
  }
  actions {
    uuid id PK
    string tool_use_id
    json input
    string status "pending, approved, rejected"
    json held_results "other results of the same turn"
    string decided_by
  }
  tool_calls {
    string tool_name
    json input
    json output
    bool is_error
    int duration_ms
    string approver
  }
  help_articles {
    string slug UK
    string title
    text body
    tsvector search "generated, GIN index"
  }
```

## Run it

Requires Docker.

```bash
cp .env.example .env          # LLM_PROVIDER=fake: no API key needed
docker compose up --build
```

This starts Postgres (host port **5451**), runs migrations, seeds the shop and starts the API on <http://localhost:8000> (docs at `/docs`). The seeded customers are Ada, Grace and Linus (`ada@`, `grace@`, `linus@example.com`).

```bash
C='Authorization: Bearer change-me-client-key-at-least-32-characters'
S='Authorization: Bearer change-me-staff-key-at-least-32-characters'; J='Content-Type: application/json'
LINUS=$(docker compose exec -T postgres psql -U app -d support -tAc "select id from customers where email='linus@example.com'")
CONV=$(curl -s localhost:8000/conversations -H "$C" -H "$J" -d "{\"customerId\":\"$LINUS\"}" | jq -r .data.id)

# The reply streams as Server-Sent Events: text deltas, tool events, then done
curl -N localhost:8000/conversations/$CONV/messages -H "$C" -H "$J" -d '{"content":"What is the status of ORD-3001?"}'

# A refund above $50 waits for approval (event: approval_required)
curl -N localhost:8000/conversations/$CONV/messages -H "$C" -H "$J" -d '{"content":"One chair broke, please refund $225 on ORD-3001"}'
ACTION=$(curl -s "localhost:8000/actions?status=pending" -H "$S" | jq -r '.data[0].id')
curl -s localhost:8000/actions/$ACTION/approve -H "$S" -H "$J" -d '{"approver":"sam"}' | jq .data.reply

curl -s "localhost:8000/audit/tool-calls?conversationId=$CONV" -H "$S" | jq '.data[] | {toolName, isError, approver}'
```

To use Claude: set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` (or `bedrock` and `AWS_REGION`) in `.env`, and set `PRICE_*_USD_PER_MTOK` from the current price list for cost tracking. This calls a paid API; nothing in this repository does it by default.

### Tests, lint and eval

```bash
docker compose up -d postgres   # tests use the support_test database, created and migrated automatically
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
uv run python -m scripts.eval   # pass/fail per scenario with the configured provider (default: fake)
```

## Endpoints

Every response uses one envelope: `{ "data": … }`, plus `"meta": { page, pageSize, total, totalPages }` on lists. Errors are `{ "error": { "code", "message", "details"? } }`. The exception is the message endpoint, which streams `text/event-stream`. Everything is validated by Pydantic. **client** = `CLIENT_API_KEY` (the shop's backend), **staff** = `STAFF_API_KEY`.

| Method | Path                                       | Access | Notes                                                                                                                                        |
| ------ | ------------------------------------------ | ------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/health`                                  | public |                                                                                                                                              |
| POST   | `/conversations`                           | client | `{customerId}`: the customer is fixed here                                                                                                   |
| GET    | `/conversations/{id}`                      | client | Status, tokens, cost                                                                                                                         |
| GET    | `/conversations/{id}/messages`             | client | The history (thinking blocks hidden)                                                                                                         |
| POST   | `/conversations/{id}/messages`             | client | `{content}` → SSE: `text`, `tool`, `approval_required`, `stopped`, `error`, `done`. 409 if busy, awaiting approval, escalated or over budget |
| GET    | `/tools`                                   | client | Tool names, descriptions and input schemas                                                                                                   |
| GET    | `/help-articles` · `/help-articles/{slug}` | client | `?q=` runs ranked full-text search                                                                                                           |
| GET    | `/actions`                                 | staff  | `?status=pending`                                                                                                                            |
| POST   | `/actions/{id}/approve` · `/reject`        | staff  | `{approver, note?}` → runs (or not) and returns the agent's reply                                                                            |
| GET    | `/audit/tool-calls`                        | staff  | `?conversationId=`                                                                                                                           |

## Key design decisions

**A hand-written loop over a small interface.** `AgentRunner` in `conversations/agent.py` does the following, in about 150 lines:

1. Calls `LLMProvider.respond(system, tools, history)`, streaming text deltas to the client.
2. Stores the assistant turn **exactly as returned**, so thinking blocks and fallback blocks are replayed unchanged, as the API requires.
3. Adds the tokens and cost to the conversation.
4. Then:
   - **On a refusal or `max_tokens`,** stops.
   - **With no tool calls,** finishes.
   - **Otherwise,** validates each tool input with the tool's Pydantic model, runs the tools (each in a savepoint, so a failing tool rolls back only its own writes), audits each call, stores all `tool_result` blocks as one user turn, and loops.

The other limits:

- **Tool rounds:** the loop stops after `MAX_TOOL_ROUNDS`. Any unanswered `tool_use` gets an error result, so the history stays valid.
- **Budget:** each conversation has a token budget, checked before every model call.
- **Cost:** recorded per call, from configured prices (default 0, because prices come from your provider's price list, not from this code).

**Customer scoping is structural.** A conversation's `customer_id` is set by the shop's backend when it is created. Every tool receives a `ToolContext` with that id, and no tool input model has a customer field. The models forbid extra fields, so a model that adds `"customer_id"` gets a validation error back. Every order lookup filters on the context's customer, and someone else's order produces the same "No order X on this customer's account" as a missing one, so existence doesn't leak either.

`tests/integration/test_scoping.py` scripts a model that follows a hostile message: it reads, refunds and returns Grace's order, and lists orders with Grace's id. The test checks that every call fails, that nothing of Grace's appears anywhere in what the model was shown, and that no row was written.

**Approval pauses the turn; the decision resumes it.** `issue_refund` above `REFUND_APPROVAL_THRESHOLD_CENTS` returns `NeedsApproval` instead of acting. The loop then:

- stores an `Action` with the `tool_use` id;
- holds the other `tool_result`s of that turn (the API wants every `tool_use` of a turn answered in one message);
- sets the conversation to `awaiting_approval`.

When staff approve, the refund runs with re-checked limits (the order may have changed meanwhile) and is audited with the approver's name. The outcome goes into the history as the held tool's result, and the loop runs again. Rejecting resumes the same way, with a "rejected" result.

**An explicit state machine for conversations** (`conversations/state.py`):

| From              | Allowed to                           |
| ----------------- | ------------------------------------ |
| active            | responding                           |
| responding        | active, awaiting_approval, escalated |
| awaiting_approval | responding (only via a decision)     |
| escalated         | (final: a person has it)             |

`responding` also means one agent turn at a time per conversation. A test caught a real bug here: a customer message used to be accepted while a refund was awaiting approval, which would have left the model's `tool_use` unanswered. New messages now require `active`.

**Providers behind one interface.** `LLMProvider.respond()` returns an `LLMTurn` of plain API-shaped content blocks, so the loop doesn't care which provider is behind it:

- **Anthropic API:** uses `claude-opus-5-5` (thinking is always on; `LLM_EFFORT` sets how much) and `eager_input_streaming` on the tools. The server-side refusal fallback is on (`fallbacks: "default"`), so a safety decline is retried on a fallback model inside the same call. A tool input the SDK can't parse is re-issued up to twice.
- **Bedrock:** `AsyncAnthropicBedrockMantle`; a refusal there ends the turn.
- **Fake:** a keyword router, so the app runs with no key.
- **Scripted:** plays exact turns in tests and records every request, so tests can assert on what the model was shown.

**Patterns, only where they earn their place.**

- _Singleton:_ settings and the database engine.
- _Factory:_ `LLMProvider`.
- _Repository:_ all data access.
- _Strategy:_ the tools, each one class with the same `run(ctx, args)`, which the loop runs without knowing which tool it is.
- _State machine:_ the conversation lifecycle.

## Results

Not yet measured, run `uv run python -m scripts.eval` (with `LLM_PROVIDER=anthropic` and `--yes` for a real model).

## Known limitations

- **The real model adapters are untested:** the Anthropic API and Bedrock code paths have never run here. Tests and the default setup use the fake and scripted providers, so prompt quality and real tool-choice behaviour are unmeasured.
- **The fake is a keyword router, not an agent.** It exists to exercise the loop. It falls for instructions in the message, as the eval scenarios show; the tools' scoping is what stops harm.
- **A crash mid-turn leaves the conversation `responding`.** There is no timeout that resets it.
- **SSE only for messages.** The approval endpoint returns the resumed reply as JSON rather than streaming it, and there is no way to re-attach to a running stream.
- **One pending approval per model turn.** A second large refund in the same turn gets an error result.
- **No customer authentication of its own:** the service trusts the shop's backend, through the client key, to pass the right `customerId`.
- **Bedrock has no server-side refusal fallback;** the client-side fallback middleware is not used.
- **The token budget is checked before each call,** so a single call can go past it.

## Deployment

| File                              | Purpose                                                                                                                                                                                                                                                                                |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Dockerfile`                      | Multi-stage build with uv: locked production dependencies in a virtualenv, then a slim runtime image running as the non-root `app` user, with a healthcheck on `/health`.                                                                                                              |
| `scripts/start_with_secrets.py`   | Container entrypoint. With `SECRETS_SOURCE=aws` it loads one JSON secret (`SECRET_ID`) from AWS Secrets Manager into the environment (it must contain `DATABASE_URL`, `CLIENT_API_KEY`, `STAFF_API_KEY`); with `local` it uses `.env`. It validates the settings, then starts the API. |
| `deploy/ecs-task-definition.json` | ECS Fargate task definition template with `${…}` placeholders for account id, region, image, role ARNs, secret id and token prices. It selects `LLM_PROVIDER=bedrock` with `anthropic.claude-opus-5-5`.                                                                                |
| `.github/workflows/deploy.yml`    | Manual-only (`workflow_dispatch`) workflow. It assumes an AWS role through GitHub OIDC (no stored keys), builds and pushes the image to ECR, fills in the task definition with `envsubst` and updates the ECS service.                                                                 |
| `.github/workflows/ci.yml`        | Runs ruff (lint and format check) and pytest against a Postgres service container on every push and pull request.                                                                                                                                                                      |

Deployment config is provided as a reference. This project has not been deployed.
