# First evaluation

The normal qq command now uses OpenAI by default. The earlier sections record local trials before that change; the latest API verification is recorded below.

These runs check the workflow and expose weaknesses. They do not establish that the model is a reliable coach. No results have been rated by the user yet.

## OpenAI comparison on PR 15

On 1 November 2026, GPT-6.1 Sol assessed the same saved packet that repeatedly failed with the local model on [PR #15](https://github.com/suttree/commonground/pull/15). The PR's head and base commits still matched that packet when checked. This comparison used the existing prompt, output schema, citation validator, correction paths, and relevance check. Only the model call changed to the OpenAI Responses API with strict structured output and medium reasoning effort.

The API completed in 9.2 seconds with one call and returned `no_feedback`. It required no status or citation correction. It used 15,944 input tokens and 203 output tokens, including 140 reasoning tokens. The relevance check was not invoked because the draft had no points.

Its summary was: "No coaching point stands out in the supplied excerpts. The directory query retains pagination and preloads profile photos, and the visible profile changes respect the stated personal-profile boundary; truncated changes and omitted tests were not assessed."

The response passed validation and acknowledged the evidence limits. The packet contains a truncated diff and partial file excerpts, so this is not an assessment of every change in the PR. This run demonstrates successful completion on one previously failing case. It does not establish that the model can identify useful coaching opportunities or that its abstention is correct.

The result is saved locally in `.qq/pr15-openai-sol-comparison.json`. To view it, run `./qq --open .qq/pr15-openai-sol-comparison.json`. To repeat the comparison on any saved packet, use `scripts/compare_openai.py` with `--packet` and a new `--output` path. The normal qq command continues to use Ollama.

The local model was `qwen3.5:9b-coding`. The source checkout was read at each PR's head and merge base. The app did not modify Commonbound or post feedback to GitHub.

## Observations

[PR #1](https://github.com/suttree/commonground/pull/1) updates GitHub Actions checkout. The model returned `no_feedback`. That is a reasonable result for coaching, but its explanation included a generic warning about possible breaking changes. The prompt now asks for a concise reason to abstain without speculative warnings. That wording change has not been rerun on this PR.

[PR #11](https://github.com/suttree/commonground/pull/11) adds notification emails. The first run returned `needs_context`, asking for the invitation model and delivery configuration. Those files were missing from the first selection. Its explanation also mixed a scheduled rake task with web-request latency. This was not a useful coaching result.

A subsequent draft had valid citations but made an unsupported claim that a scheduled rake task blocks the web server. It also labelled concerns as strengths. This demonstrates why matching source text is insufficient. A second model pass now checks relevance and causal claims, can correct a point's kind, and can discard every point. It cannot add replacement points.

The collector now reserves space for related code, recognises code symbols rather than ordinary capitalised words, and reads excerpts around changed lines. A focused rerun explicitly includes the invitation model, production configuration and reminder task. Its first response failed exact citation validation. No finding was accepted from that response. QQ now resolves a quote to its actual source line only if the quote has one unique match, ignoring leading and trailing whitespace. It still rejects ambiguous or invented quotations.

## Final check result

The final check discarded both points from the focused PR #11 draft and returned `no_feedback`. It correctly rejected the claim that the scheduled rake task blocks the web process, and recognised that scheduling was already called out in the description.

However, its reason for discarding the request-latency point was partly wrong. Rescuing an SMTP exception does not make `deliver_now` asynchronous or stop the request from waiting. The check claimed the action completes immediately, which the supplied code does not establish. The draft's claim about affecting all users was overstated, but this rejection does not demonstrate sound reasoning.

The working conclusion is that the app mechanics are ready for trials, while this local model's coaching judgement is not yet validated. Both raw drafts and rejection reasons are retained. Do not treat the filtered result as proof that no worthwhile conversation exists. A worthwhile next experiment is to compare a stronger model on these same saved evidence packets, rather than keep adding rules for these particular PRs.

The last cached draft took about 44 seconds to generate. Cold runs on the larger PR took several minutes; the second check adds another model call. These are observations from this machine, not a benchmark.

## Replay

Results are in the local `results/` directory, which is excluded from Git because each result includes source excerpts.

```sh
./qq --open results/pr1.json
./qq --open results/pr11-first.json
./qq --open results/pr11-checked.json
```

The unchecked draft is retained as `results/pr11-draft.json` for diagnosis. It contains unsupported advice and is not the recommended replay.

Use `n` and `p` to compare points, and Tab to move between the conversation, evidence, diff and context views.

## What has been verified

Twenty-one automated checks pass. They cover valid abstention, missing context, the two-point limit, exact citations, model failure handling, historical source reads, preserving uncommitted files, and sensitive-path exclusions. A real pseudo-terminal test covers navigation, rating persistence, resizing and quitting.

The TUI was also opened manually through a terminal for the real no-feedback and needs-context results. A clearly labelled synthetic finding was used to check the positive-point and source-evidence views. That synthetic finding is not counted as a successful model result.

## What remains to prove

The model should identify a discussion worth having, explain it accurately, and avoid unnecessary advice about scaling or architecture. Exact citations alone cannot establish those qualities. Local model latency is also substantial on larger PRs.

The cases in `examples/commonbound.json` include PR #12 for a later evaluation. It has not been run through the model yet.

## PR 10 citation failure

A replay of [PR #10](https://github.com/suttree/commonground/pull/10) reproduced a citation error. The model rewrote double quotes as single quotes in a JavaScript statement and cited line 2 instead of line 3. The source check correctly rejected the quote, but the whole analysis stopped.

QQ now makes one citation correction attempt before the relevance check. It keeps the coaching text unchanged, requires the same cited files and citation counts, and validates the corrected quotes against the supplied source. Failed analyses retain the raw draft and any correction in a local diagnostic file.

Replaying the failing draft through the correction and relevance passes returned `no_feedback`. The relevance check rejected both points because they criticised the first-name heading and tab scrolling that the PR explicitly requested. This is one successful recovery, not evidence of reliable coaching judgement. The correction selected a nearby comment for the JavaScript citation, even after the prompt was clarified to ask for the original statement. The comment is exact source text, but this illustrates why a valid citation alone cannot establish support for a claim. The relevance check reads the surrounding source as well.

## Local workflow investigation on 1 November 2026

The default remains the installed on-device `qwen3.5:9b-coding` model. Ollama reports version 0.40.4 and the MLX runner for this model. A small schema probe succeeded with the other installed model, `qwen3:14b`, with thinking disabled. Its full PR trial was stopped during slow prompt processing, so it provides no evidence of review quality.

Adding the schema to the prompt and using Qwen's documented non-thinking sampling settings did not solve the original workflow. A full PR #15 replay still failed after 363 seconds because citation correction selected an unavailable source range. The draft's advice was speculative and should have been discarded before trying to repair its quotes.

QQ now checks draft claims for support and relevance before repairing retained citations. A discarded point needs an explicit rejection from that check. Retained points still require exact source citations. Source-range repair now uses one flat array whose exact length is constrained by the number of retained citations, so the model does not have to group ranges by point. After a source-range repair, the claims are checked again against the selected source lines. Invalid output and failed model calls remain errors. Both relevance decisions are retained when a repair requires a recheck.

The normal command, `./qq https://github.com/suttree/commonground/pull/15 --repo /Users/duncan/src/commonground --json`, completed with `no_feedback` in 88.7 seconds using the same local model. The draft suggested speculative query scaling work and future CSS token collision risks. The relevance check discarded both. No status or citation correction was needed. The model was already loaded and its caches had been warmed by preceding trials, so this timing is not a cold-start benchmark. The result is saved locally in `.qq/pr15-local-verified.json`.

A labelled synthetic example skips a login guard and deletes an account selected by request parameters. An early trial found an access-control concern and discarded a speculative soft-delete suggestion in 54 seconds, but a subsequent trial missed the issue and assumed an unseen ownership check might exist. Clarifying the relevance instructions did not resolve this; the model then assumed the action was inaccessible because no route had been supplied.

With the explicit route supplied, the normal local workflow returned supported authentication and ownership findings in 43.6 seconds. Their exact citations passed validation. The result is in `.qq/local-positive-flat-verified.json`.

Replaying an earlier failed draft through the new flat-array citation repair succeeded mechanically in 36.3 seconds with three live local model calls and a cached initial draft. All three citations were copied exactly from the supplied source. However, the final relevance check incorrectly discarded the real authentication issue because the PR description said the endpoint should require authentication. It confused intended behaviour with the demonstrated implementation. The result is in `.qq/local-citation-repair-verified.json`. This is a false negative, despite the workflow completing without errors.

A focused discovery-page trial also completed with `no_feedback`, but its draft falsely inferred missing search handling from a controller excerpt that omitted the start of the action. Better source selection matters, and the model must distinguish absent behaviour from code it has not seen.

The request grounding, relevance ordering, and citation-count changes improve the local workflow, but the current 9B model remains an inconsistent judge. A successful no-feedback result cannot establish that the review found all worthwhile issues. Forty-two automated tests pass, and both an actual PR result and a positive synthetic result were opened and navigated in the curses interface. The normal PR workflow remains local, and no model download or change to the Ollama service configuration was required. Sampling settings follow the [Qwen model card](https://huggingface.co/Qwen/Qwen3.5-9B); including the schema in the prompt follows [Ollama's guidance](https://docs.ollama.com/capabilities/structured-outputs).

## OpenAI as the default provider

Following the inconsistent local results, the normal qq command was switched to the OpenAI Responses API with `gpt-6.1-sol` on 1 November 2026. Ollama remains available with `--provider ollama`. The existing prompt, source checks, relevance checks, and diagnostic handling are shared by both providers. The comparison script now uses the same API client as normal reviews.

A fresh invocation of `./qq https://github.com/suttree/commonground/pull/15 --repo /Users/duncan/src/commonground --json` used OpenAI without a provider flag. It returned `no_feedback` in 8.6 seconds with one API call and no correction attempts. Its summary acknowledged the truncated views, styles, and tests. The result is saved locally in `.qq/pr15-openai-default-verified.json`.

The synthetic deletion endpoint, including its explicit route, returned one supported discussion point covering authentication and account ownership in 11.5 seconds. The relevance check retained it, and all three exact citations passed validation without correction. The result is in `.qq/openai-positive-verified.json`.

Both results were opened in the curses interface, including the Context view's provider and model label. Fifty-one automated tests pass. They cover default API routing, explicit local routing, missing credentials, offline examples and evidence collection, model overrides, API failures, response parsing, and preserving source validation. These two live cases support using the API for further trials; they do not establish dependable judgement across all PRs.
