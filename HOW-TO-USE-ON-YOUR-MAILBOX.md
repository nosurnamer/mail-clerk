# Using mail-clerk on your own mailbox

> A step-by-step guide for running this triage pipeline on a real inbox,
> using nothing but a **Claude Pro, Max, or Team subscription**. No API key,
> no billing setup, no code changes required for the basic path.

There are two ways to run it. Pick whichever matches what you have installed.

| | You have | You'll run | Speed |
|---|---|---|---|
| **Path A** | Claude Code installed and logged in | one command | fully automatic |
| **Path B** | Just a browser and claude.ai | copy, paste, save | a few manual steps |

Both paths use the exact same rules, the exact same inbox format, and
produce the exact same four files in `output/`. Neither one ever sends an
email, books a meeting, or pays anything — this system only drafts and
recommends; a human decides what actually goes out.

---

## Before you start: swap in your own data

The repo ships with a worked example (Sam Okonkwo / Verdano). To use it on
yourself or a client, replace two files:

1. **`founder-brief.md`** — rewrite this with *your* rules:
   - Who your VIP senders are (investors, your biggest customer, counsel,
     family — whoever should never get lost in the noise)
   - What always needs your personal sign-off (pricing, legal, hiring,
     press, security, spend over some dollar threshold — pick your own
     numbers)
   - What your assistant/BA is explicitly trusted to handle on their own
   - Your tone preferences for drafted replies
   - Your actual calendar rules and this week's actual calendar

2. **`inbox.csv`** — export or transcribe the emails you want triaged, one
   row per email, matching the existing column headers:

   ```
   id,from,email,org,to,cc,date,subject,attachment,body
   ```

   `id` must be a unique whole number per row (1, 2, 3, ...). Everything
   else is free text — copy subject lines and bodies as they actually read.

You don't need to touch `prompt.md` or `triage.py` — both are written
generically and already know how to read whatever you put in the two files
above.

---

## Path A — you have Claude Code

This is the fastest path: one command drives everything.

### 1. Confirm you're logged in

```bash
claude auth status
```

If that says you're not logged in, run `claude login` and sign in with your
Claude Pro, Max, or Team account. No API key needed — this uses your
subscription login, not per-token billing.

### 2. Run the pipeline

```bash
cd mail-clerk
python3 triage.py run
```

This will:

1. Read `founder-brief.md` and `inbox.csv`
2. Hand them to Claude (through the `claude` CLI, using your existing login)
3. Write `output/classifications.csv`, `output/founder-brief.md`,
   `output/drafts.md`, and `output/corrections.md`
4. Immediately run the deterministic safety checks and tell you if anything
   looks wrong (missing rows, an invalid category, a draft that quietly
   changes bank details, and so on)

A run over ~50 emails typically takes a couple of minutes.

### 3. Review the output

Open `output/founder-brief.md` first — that's the thing meant to be read at
a glance. Then check `output/drafts.md` before sending anything, and skim
`output/classifications.csv` for anything that looks off.

---

## Path B — you just have claude.ai in a browser

No CLI, no install — just your subscription and a browser tab.

### 1. Build the prompt file

```bash
cd mail-clerk
python3 triage.py prepare
```

This writes everything Claude needs — the rules, the calendar, all the
emails, and the output instructions — into a single file, `claude_prompt.txt`.

### 2. Paste it into Claude

1. Open [claude.ai](https://claude.ai) and start a new chat.
2. Open `claude_prompt.txt`, select all, copy it.
3. Paste the whole thing into the chat and send it.

For a large inbox this can be a long message — that's fine, Claude Pro's
context window handles it. If your inbox is very large (hundreds of
emails), consider splitting it into a couple of runs.

### 3. Save Claude's reply

Copy Claude's *entire* response — it will look like one big block of JSON —
and save it into a new file, e.g. `claude_response.txt`, in the `mail-clerk`
folder.

### 4. Turn it into your output files

```bash
python3 triage.py ingest claude_response.txt
```

This splits Claude's reply into the four output files and immediately runs
the same deterministic safety checks as Path A. If it complains, re-copy
Claude's reply (a common cause is copying only part of a long response) and
try again.

### 5. Review the output

Same as Path A: start with `output/founder-brief.md`, then `output/drafts.md`
before sending anything.

---

## Checking the output on its own

You can run the safety checks any time, independent of how the output was
generated (or even someone else's output you were handed):

```bash
python3 triage.py validate
```

This checks, without calling Claude at all:

- every input email got exactly one classification, no duplicates, none missing
- every classification and flag is one of the allowed values
- nothing describes a payment/bank-detail change as something to actually do
- nothing in the brief or drafts describes an email as already sent, a
  meeting as already booked, or an invoice as already paid
- the two founder-approval-required drafts are clearly labeled as such

If it fails, it tells you exactly which line and why — that's the point of
keeping this check separate from Claude's own judgment.

---

## A note on what this doesn't do

This tool never touches your real mailbox, calendar, or bank account. It
reads a CSV you gave it and writes markdown/CSV files back. Turning any of
its recommendations into a real sent email, a real calendar invite, or a
real payment is a decision a human makes outside this tool, on purpose.
