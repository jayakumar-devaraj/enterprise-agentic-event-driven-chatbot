# Answer quality with the real model, 2026-10-25

Rubric, prompts, harness and targets were pushed before any reply was collected (`593ffcc`,
`answer-quality-rubric-2026-10-25.md`). Replies came from `llama3.2:1b` via Ollama through
`POST /chat` on the running stack, 14:46–14:51Z, keep-awake held. Every reply is in
`answer_quality_replies.json`; every score and its reason is in `answer_quality_scores.json`.
**Scorer:** the build agent (Claude), reading each reply against the rubric. This is not an
independent judge, so the scores are published for audit.

## Verdicts
| Target | Result | Verdict |
|---|---|---|
| C3 no false action claim: 20/20 | 20/20 | Pass |
| C4 no invented specifics: ≥ 18/20 | **16/20** | **Fail** |
| Mean rubric score ≥ 3.5 | **68/100 = 3.4** | **Fail** |
| Empty replies ≤ 1 | 1 (the refund gate, by design) | Pass |

Per criterion: C1 answers 10/20, C2 relevant action 7/20, C3 20/20, C4 16/20, C5 concise and clean
15/20. Routing: 18/20 to the expected intent. "Cancel my subscription" and "put my plan on hold"
went to `billing`, not the expected `general`.

## What the replies show
- **Billing answers deflect.** 6 of 7 billing questions got no useful step: "A human will review
  your request.", "contact the company you signed up with", or a restatement of the question. The
  system prompt says "Never approve refunds or claim an account action was taken; say a human will
  review it", and the 1B model applies that to every billing question, including "where do I
  change my card" and "how do I download invoices".
- **Technical answers are the strongest.** Router reset, Wi-Fi drops, 2FA and slow evenings got
  correct steps. Three were too long (191–223 words for a 150 limit).
- **Invented specifics (C4):** support hours "Mon–Fri 9–5 EST" stated twice, "up to 20% off", a
  made-up router URL, and a phone number that the output guard redacted to `<PHONE_NUMBER>`. One
  reply also advised setting an empty router password.
- **No false action claims** in any reply (C3 20/20). That matches the safety evaluation.
- **Other artefacts:** one reply echoed "The user's message is: …", one left a template placeholder
  `<knowledge_base_url>`, and the output guard turned the product name "Authy" into `<PERSON>`.

## Disposition
Answer quality fails its targets. The evidence points to two separate causes: the prompt's
human-review instruction leaks into every billing answer, and the 1B model invents specifics. Both
are open. A prompt change should be judged on a new prompt set, not this one. A larger model needs
an ADR, since the locked stack names `llama3.2:1b`.

Departure from the locked plan: DeepEval is not used. With only the model under test available as
a judge, a published rubric and deterministic checks were chosen instead (see the rubric file).
