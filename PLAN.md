# PLAN — support-agent

A customer-support agent backend built on Claude tool use, over a seeded fake shop (customers, products, orders, help articles). A customer's message goes into a hand-written agent loop: the model can look up orders, search help articles, create returns, issue refunds and hand over to a human.

Safety comes from the code, not the prompt:

- **Customer scoping.** The customer is fixed on the conversation, server-side, and every tool enforces it.
- **Refund approval.** Large refunds wait for a person, and the conversation resumes with their decision.
- **Audit.** Every tool call is recorded.

**Stack:** Python 3.12 with uv, FastAPI, SQLAlchemy 2 (async) with asyncpg, Alembic, Pydantic v2, the official `anthropic` SDK (2.x), pytest with pytest-asyncio, ruff, PostgreSQL 16 (also full-text search). No Redis: nothing needs it.

## Modules

| Module        | Responsibility                                                                                                                                                                                                                                 |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| shop          | The fake shop's data: customers, products, orders with items, returns and refunds. Read and written only by the tools (and the seed).                                                                                                          |
| conversations | Create a conversation for a customer; send a message (the agent loop, streamed as SSE); read the history. The conversation state machine, token budget and cost live here.                                                                     |
| messages      | Stored turns in Anthropic content-block form (text, tool_use, tool_result, thinking), so the history is replayed to the model unchanged, plus per-call usage records.                                                                          |
| tools         | The six tools, one class each with a Pydantic input model: `get_order`, `list_customer_orders`, `search_help_articles`, `create_return`, `issue_refund`, `escalate_to_human`. A registry builds the tool definitions. `GET /tools` lists them. |
| actions       | Pending approvals: a refund above `REFUND_APPROVAL_THRESHOLD_CENTS` becomes an action. A person approves or rejects it, the refund runs (or not), and the conversation resumes with that outcome as the tool result.                           |
| help-articles | Help centre articles with Postgres full-text search (`tsvector` generated column, GIN index, `websearch_to_tsquery`, `ts_rank`).                                                                                                               |
| audit         | One row per tool call: input, output, error flag, duration, and the approver when a person decided.                                                                                                                                            |

**Shared (`app/core/`):** settings, logging, errors and handlers, pagination and envelopes, API-model base, auth (client key, staff key), database and base repository.

**LLMProvider (factory, chosen from settings):**

- `anthropic`: `AsyncAnthropic`, streaming, `claude-opus-5-5`, with the server-side refusal fallback.
- `bedrock`: `AsyncAnthropicBedrockMantle`, `anthropic.claude-opus-5-5`.
- `fake`: deterministic and rule-based, for local runs and the demo.

Tests use a **scripted** provider: a list of steps that drives the very same loop. Neither real adapter is ever called by the tests. The default is `fake`, so nothing needs an API key.

## The agent loop (written by hand, no framework)

```
check the conversation can respond (state machine) and has budget left
loop:
  stream a model call (system + tools + full history); forward text deltas as SSE
  store the assistant turn exactly as returned; add tokens and cost to the conversation
  stop_reason refusal / max_tokens  -> stop, tell the customer
  no tool_use blocks                -> done (end_turn)
  round > MAX_TOOL_ROUNDS           -> answer every tool_use with an error result, stop
  for each tool_use: validate input (Pydantic) -> run with the conversation's customer -> audit
      a refund above the threshold  -> create a pending action, hold the other results, pause
  store all tool_result blocks as one user turn, then loop
```

Approving or rejecting an action executes the refund (or not), audits it with the approver's name, appends the outcome as the held `tool_result`, and runs the loop again from there.

**Conversation state machine** (one table):

| From              | Allowed to                           |
| ----------------- | ------------------------------------ |
| active            | responding                           |
| responding        | active, awaiting_approval, escalated |
| awaiting_approval | responding                           |
| escalated         | (final for the agent)                |

`responding` also stops a second message from being processed in parallel.

## Data model

```
customers       id, email, name                 products  id, sku, name, price_cents
orders          id, number (ORD-1001), customer_id, status, total_cents, placed_at, delivered_at?
order_items     id, order_id, product_id, quantity, unit_price_cents
returns         id, order_id, reason, status, created_at
refunds         id, order_id, amount_cents, reason, approved_by?, created_at
help_articles   id, slug, title, body, search (tsvector, generated)
conversations   id, customer_id, status, input_tokens, output_tokens, cost_usd, token_budget, created_at
messages        id, conversation_id, seq, role (user|assistant), content (json blocks), created_at
llm_calls       id, conversation_id, model, input_tokens, output_tokens, cost_usd, stop_reason, created_at
actions         id, conversation_id, tool_use_id, kind, input, status (pending|approved|rejected), held_results,
                decided_by?, decision_note?, decided_at?, created_at
tool_calls      id, conversation_id, tool_use_id, tool_name, input, output, is_error, duration_ms, approver?, action_id?
```

## Endpoints

```
GET  /health
POST /conversations                       [client] {customerId}
GET  /conversations/{id}                  [client]
GET  /conversations/{id}/messages         [client] paginated
POST /conversations/{id}/messages         [client] {content} → text/event-stream
GET  /tools                               [client]
GET  /help-articles?q=                    [client] paginated     GET /help-articles/{slug}
GET  /actions?status=                     [staff]  paginated
POST /actions/{id}/approve | /reject      [staff]  {approver, note?} → resumes the conversation
GET  /audit/tool-calls?conversationId=    [staff]  paginated
```

## Tests (scripted model, real Postgres)

- **Scoping:** a scripted model tries to read another customer's order, list orders with an injected `customer_id`, refund and return another customer's order. Every attempt fails, no other-customer data appears in any tool result or audit row, and nothing is written.
- **Approval:**
  - A small refund executes directly.
  - A large one creates a pending action, and the conversation is `awaiting_approval`.
  - Approving executes it, audits the approver, and resumes with the outcome as the tool result.
  - Rejecting resumes without a refund.
  - Deciding twice is a 409.
- **Loop limits:** stops after `MAX_TOOL_ROUNDS` (every `tool_use` still answered); stops when the token budget is spent; invalid tool input becomes an `is_error` result; refusal and `max_tokens` stop cleanly.
- **SSE:** the event order is text deltas, tool events, then `done`.
- **Shop rules:** a refund can't exceed what remains refundable; returns only within 30 days of delivery.
- Full-text search ranking; cost from configured prices; history replayed unchanged.
- **Unit:** state-machine table, tool input models, cost maths, fake provider rules, settings, `start_with_secrets`.

**Eval script (`scripts/eval.py`):** runs scenario files (`eval/scenarios/*.json`: customer message → tools that must and must not be called) through the configured provider and prints pass/fail per scenario. No results are recorded.

## Build order

1. Scaffold. 2. Core, models, migration with FTS, seed. 3. shop and help-articles. 4. tools, audit. 5. LLM providers. 6. conversations, messages and the loop. 7. actions. 8. Tests. 9. Eval script. 10. Deployment files. 11. README.
