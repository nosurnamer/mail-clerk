# mail-clerk

A lightweight, repeatable inbox-triage pipeline that turns an arbitrary
founder inbox into five actionable buckets, applies the founder's operating
rules before making any judgment calls, and produces a decision-focused
brief rather than simply summarizing emails.

```text
Founder Brief
      |
Hard constraints
      |
Deterministic validation
      |
Claude judgment
      |
Human review
      |
Possible action
```

Claude proposes decisions. Deterministic rules enforce hard constraints. A
human reviews every judgment call before anything leaves the system.

This is a **decision-support system, not an autonomous email agent.** It
never sends an email, books a meeting, pays an invoice, or changes payment
details - it only reads, classifies, and drafts for a human to review.

## What it does

Given a founder's operating rules and a CSV of inbox messages, it produces:

1. `output/classifications.csv` - every email classified into exactly one of
   `SAM_NOW`, `SAM_LATER`, `DRAFT_READY`, `DELEGATE`, `NO_ACTION`, with
   flags, owner, urgency, deadline, reason, and a recommended (not taken)
   action.
2. `output/inbox-brief.md` - the concise brief the founder actually reads,
   organized by decision rather than by email. (Named differently from the
   input `founder-brief.md` on purpose, so the two are never confused.)
3. `output/drafts.md` - four draft replies: two the BA is authorized to send
   as-is, two clearly marked as requiring the founder's sign-off.
4. `output/corrections.md` - two worked examples of where a naive classifier
   would get the call wrong and why the founder's actual rules say
   otherwise.

## How to run it

**No Anthropic API key or billing required.** This run's outputs are already
committed under `output/` for the sample inbox in `inbox.csv`. To validate
them on their own:

```bash
python3 triage.py validate
```

To regenerate them (or run against a new inbox), you only need a Claude Pro,
Max, or Team subscription:

```bash
python3 triage.py run
```

`run` drives Claude through the local Claude Code CLI (`claude -p`), using
whatever account you're already logged into `claude` with - your
subscription login, not per-token API billing. It writes the four output
files and then immediately runs the same deterministic checks as `validate`
- if validation fails, it exits non-zero and prints exactly what's wrong
instead of silently producing an output that looks fine.

No Claude Code CLI installed? Use the copy/paste path instead:

```bash
python3 triage.py prepare              # writes claude_prompt.txt
# paste claude_prompt.txt into a claude.ai chat, save the reply as claude_response.txt
python3 triage.py ingest claude_response.txt
```

**See [`HOW-TO-USE-ON-YOUR-MAILBOX.md`](HOW-TO-USE-ON-YOUR-MAILBOX.md) for
the full step-by-step walkthrough**, including how to swap in your own
founder brief and inbox.

## Input format

- **`founder-brief.md`** - free-form markdown: the founder's VIP senders,
  what always requires the founder personally, what the BA is authorized to
  handle, tone preferences, and calendar rules (including the actual
  calendar for the relevant week). Treated as the source of truth; nothing
  is inferred that isn't stated here or in the inbox.
- **`inbox.csv`** - one row per email: `id, from, email, org, to, cc, date,
  subject, attachment, body`. `id` must be unique and sequential; the
  pipeline classifies exactly the emails present in this file.

## Outputs

See `output/classifications.csv`, `output/inbox-brief.md`,
`output/drafts.md`, and `output/corrections.md` for this run's results
against the sample inbox (50 emails, week of 09/14/2026).

Classification counts for this run:

| Bucket | Count |
|---|---|
| SAM_NOW | 14 |
| SAM_LATER | 13 |
| DRAFT_READY | 10 |
| DELEGATE | 2 |
| NO_ACTION | 11 |

## How hard constraints are enforced

Claude's judgment is not trusted alone to keep the system safe. `triage.py`
runs deterministic, code-level checks against any generated `output/`
directory:

- Exactly one classification row per input email; no duplicates, no missing
  IDs, no extra IDs.
- Every `primary_classification` and every flag is from the allowed enum -
  anything else fails validation.
- Every required output file exists.
- No `payment_change`-flagged row has a recommended action that actually
  changes bank/payment details (a "do not act, escalate" recommendation
  passes; "update the account to..." fails).
- No output file describes sending, booking, paying, or a payment change as
  something already done, rather than something recommended for a human to
  do.
- `drafts.md` must clearly mark the founder-approval-required drafts.

If any check fails, the script reports every failure and exits non-zero
rather than producing a result that looks valid. This is intentionally
separate from Claude's own judgment: the prompt in `prompt.md` also asks
Claude to respect these constraints, but the enforcement that matters is the
code in `triage.py`, not the model's compliance.

## What still requires human review

Everything. Specifically:

- Every `SAM_NOW` and `SAM_LATER` item is a recommendation to read something
  and decide, not a decision already made.
- Every `DRAFT_READY` reply in `drafts.md` is a draft - even the two marked
  BA-safe should be read before sending, and the two marked
  `REQUIRES SAM APPROVAL` must not be sent without the founder's own
  sign-off.
- Every `DELEGATE` row is a routing suggestion, not a message actually sent
  to that owner.
- The two `corrections.md` entries are judgment calls Claude made about
  ambiguous rule interactions (e.g. "VIP scheduling" vs. "accept/decline an
  investor meeting") - a human familiar with the founder's actual intent
  should confirm the read is right, especially the first time this runs
  against a new founder brief.

## Using it with a different inbox tomorrow

1. Replace `founder-brief.md` with the new founder's rules (VIP senders,
   always-founder categories, BA authority, tone, calendar rules and the
   current week's calendar).
2. Replace `inbox.csv` with the new set of emails, keeping the same column
   format and sequential `id`s.
3. Run `python3 triage.py run`. `prompt.md` doesn't need to change - it's
   written generically against "the founder brief" and "the inbox," not
   against Verdano specifically.
4. Review `output/` before anyone acts on any of it.

## If this were connected to live Gmail

The 50 messages would arrive via the Gmail API (or a push/watch
subscription) instead of a hand-exported CSV, with `triage.py` pulling the
relevant thread window on a schedule or on demand. That requires OAuth
credentials with at least Gmail read scope for the founder's mailbox (and
write/send scope only if the system were ever allowed to act directly,
which this design deliberately avoids). Compared to the file-based version -
which is read-only, has no external side effects, and keeps a human in
control of both input and output - a live integration exposes real mailbox
content to the pipeline and opens a path toward accidental sending,
booking, paying, or deleting if write permissions are ever granted or
misconfigured, which is exactly the risk the hard constraints and
deterministic validation in this repo are designed to prevent.
