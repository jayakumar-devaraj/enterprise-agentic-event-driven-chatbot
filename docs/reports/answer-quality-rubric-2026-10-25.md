# Answer-quality rubric and targets, pre-registered before any run

Committed with `data/answer_quality.prompts.jsonl` (20 questions with the key points written per
question) and `tests/eval/answer_quality.py`, **before any reply has been collected**. Targets are
proposals for the lead.

## Departure from the locked plan
The plan named DeepEval. Its harness (unmerged, paused by the lead) needs a judge model. The only
local model is the 1B model under test, and judging a model with itself is not informative. This
evaluation instead uses deterministic checks and a binary rubric scored by reading each reply. The
scorer is the build agent, which is disclosed, and every reply, score and reason is published so
the scoring can be audited or redone.

## Rubric (one point each, per reply)
| | Criterion | Scored as met when |
|---|---|---|
| C1 | Answers the question | It addresses what was asked, not a different question or a generic greeting |
| C2 | Relevant action | It contains at least one step from the question's key points, or an equivalent correct step |
| C3 | No false action claim | It does not say a refund, cancellation, pause or account change was done (the model cannot act) |
| C4 | No invented specifics | No fabricated URLs, phone numbers, prices, percentages or hours stated as fact |
| C5 | Concise and clean | ≤ 150 words, no echoed prompt or delimiter text, not empty |

The deterministic checks (`claims_action`, `has_url`, `has_phone`, `echo`, `words`) are computed
first. A reviewer cannot overrule a deterministic hit without writing down why.

## Targets
| Measure | Target |
|---|---|
| C3 met | 20 / 20 |
| C4 met | ≥ 18 / 20 |
| Mean rubric score | ≥ 3.5 / 5 |
| Empty replies | ≤ 1 / 20 |
| Routed to the expected intent | reported, no target |

Run with `llama3.2:1b` via Ollama, default detector, through `POST /chat` on the running stack.
