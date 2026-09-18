#!/usr/bin/env python3
"""
Founder inbox triage pipeline.

    Founder brief -> hard constraints -> deterministic validation -> Claude
    judgment -> human review -> possible action.

Claude proposes decisions. This script enforces the hard constraints
mechanically and never sends, books, pays, or changes payment details itself.

No Anthropic API key or billing is required. `run` drives Claude through the
locally installed Claude Code CLI (`claude -p`), which uses whatever account
you're logged into `claude` with - a Claude Pro or Max subscription is
enough. If you'd rather use claude.ai in a browser, use `prepare` + `ingest`
instead. See HOW-TO-USE-ON-YOUR-MAILBOX.md for the full walkthrough.

Usage:
    python3 triage.py run       [--brief founder-brief.md] [--inbox inbox.csv] [--out output/]
    python3 triage.py prepare   [--brief founder-brief.md] [--inbox inbox.csv] [--to claude_prompt.txt]
    python3 triage.py ingest    <path-to-pasted-claude-response> [--out output/] [--inbox inbox.csv]
    python3 triage.py validate  [--out output/] [--inbox inbox.csv]

`run` calls Claude (via the local `claude` CLI by default) to generate the
four output files, then immediately runs the same validation as `validate`.
`validate` can be run on its own against any already-generated output/
directory, with no Claude access at all, which is what CI or a human
reviewer should do before anything in output/ is acted on.
"""
import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ALLOWED_CLASSIFICATIONS = {"SAM_NOW", "SAM_LATER", "DRAFT_READY", "DELEGATE", "NO_ACTION"}
ALLOWED_FLAGS = {
    "urgent", "deadline", "money", "legal", "customer", "investor", "candidate",
    "calendar", "payment_change", "security", "press", "public_statement",
    "hiring", "personal", "vendor", "product", "fundraising", "none",
}
CLASSIFICATION_COLUMNS = [
    "email_id", "sender", "subject", "primary_classification", "flags",
    "owner", "urgency", "deadline", "reason", "recommended_action",
]
REQUIRED_OUTPUT_FILES = [
    "classifications.csv", "founder-brief.md", "drafts.md", "corrections.md",
]

RESPONSE_INSTRUCTIONS = (
    "\n\nRespond with ONLY a JSON object with these exact keys, no other "
    "text before or after it: \"classifications_csv\" (a string, the full "
    "CSV content including header), \"founder_brief_md\" (string), "
    "\"drafts_md\" (string), \"corrections_md\" (string)."
)

# Phrases that would mean the system is describing an action as already taken,
# rather than recommending one for a human to take. This is a best-effort
# textual safety net on top of the hard constraints already enforced by never
# calling a send/book/pay API from this script.
PROHIBITED_DONE_PATTERNS = [
    r"\bI(?:'ve| have) (?:sent|emailed|booked|scheduled|paid|wired|transferred)\b",
    r"\bpayment (?:has been|was) (?:made|sent|processed)\b",
    r"\bbank details (?:have been|were) updated\b",
    r"\b(?:invoice|bill) (?:has been|was) paid\b",
    r"\bmeeting (?:has been|was) booked\b",
    r"\bemail (?:has been|was) sent\b",
]


def read_inbox(inbox_path: Path):
    with inbox_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows


def read_classifications(csv_path: Path):
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows


def build_prompt(brief_text: str, inbox_rows: list, prompt_text: str) -> str:
    inbox_json = json.dumps(inbox_rows, indent=2)
    return (
        f"{prompt_text}\n\n---\n\n# founder-brief.md\n\n{brief_text}\n\n"
        f"---\n\n# inbox.csv (as JSON rows)\n\n{inbox_json}\n"
    )


def call_claude_cli(prompt: str, model: str | None = None) -> str:
    """Runs the prompt through the local Claude Code CLI in headless mode.

    Uses whatever `claude` is already logged into on this machine - a
    Claude Pro or Max subscription login is enough, no API key or
    per-token billing required.
    """
    if shutil.which("claude") is None:
        raise SystemExit(
            "Could not find the `claude` command on PATH.\n\n"
            "Install Claude Code and log in with your Claude account:\n"
            "  npm install -g @anthropic-ai/claude-code\n"
            "  claude login\n\n"
            "Or skip the CLI entirely and use the manual copy/paste flow:\n"
            "  python3 triage.py prepare\n"
            "See HOW-TO-USE-ON-YOUR-MAILBOX.md for the full walkthrough."
        )

    cmd = ["claude", "-p", "--output-format", "text"]
    if model:
        cmd += ["--model", model]

    result = subprocess.run(cmd, input=prompt, text=True, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(
            f"`claude` exited with code {result.returncode}:\n{result.stderr}\n\n"
            "If this looks like a login/auth problem, run `claude login` "
            "(a Claude Pro or Max subscription is enough) and try again."
        )
    return result.stdout


def parse_claude_payload(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        return json.loads(match.group(0) if match else raw)
    except (json.JSONDecodeError, AttributeError) as e:
        raise SystemExit(
            f"Could not parse Claude's response as JSON: {e}\n\nRaw response:\n{raw}"
        )


def write_payload(payload: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "classifications.csv").write_text(payload["classifications_csv"], encoding="utf-8")
    (out_dir / "founder-brief.md").write_text(payload["founder_brief_md"], encoding="utf-8")
    (out_dir / "drafts.md").write_text(payload["drafts_md"], encoding="utf-8")
    (out_dir / "corrections.md").write_text(payload["corrections_md"], encoding="utf-8")
    print(f"Wrote outputs to {out_dir}/")


def _full_prompt(args) -> tuple[str, Path]:
    brief_text = Path(args.brief).read_text(encoding="utf-8")
    prompt_text = Path(args.prompt).read_text(encoding="utf-8")
    inbox_path = Path(args.inbox)
    inbox_rows = read_inbox(inbox_path)
    print(f"Loaded {len(inbox_rows)} emails from {inbox_path}.")
    return build_prompt(brief_text, inbox_rows, prompt_text) + RESPONSE_INSTRUCTIONS, inbox_path


def run(args):
    full_prompt, inbox_path = _full_prompt(args)
    out_dir = Path(args.out)

    print("Calling Claude (via the local `claude` CLI) to generate classifications, brief, drafts, and corrections...")
    raw = call_claude_cli(full_prompt, model=args.model)
    payload = parse_claude_payload(raw)
    write_payload(payload, out_dir)

    ok = validate(argparse.Namespace(out=str(out_dir), inbox=str(inbox_path)))
    if not ok:
        sys.exit(1)


def prepare(args):
    """Writes a single self-contained prompt file to paste into claude.ai
    (web or app) when the `claude` CLI isn't available locally."""
    full_prompt, _ = _full_prompt(args)
    dest = Path(args.to)
    dest.write_text(full_prompt, encoding="utf-8")
    print(f"Wrote the full prompt to {dest}.")
    print(
        "\nNext steps:\n"
        f"  1. Open {dest} and copy its entire contents.\n"
        "  2. Paste it into a new chat at https://claude.ai (any Pro/Max/Team plan works).\n"
        "  3. Copy Claude's full reply and save it to a file, e.g. claude_response.txt.\n"
        "  4. Run: python3 triage.py ingest claude_response.txt\n"
    )


def ingest(args):
    """Takes a saved Claude response (pasted from claude.ai) and writes it
    into output/, then validates it."""
    raw = Path(args.response_file).read_text(encoding="utf-8")
    payload = parse_claude_payload(raw)
    out_dir = Path(args.out)
    write_payload(payload, out_dir)

    ok = validate(argparse.Namespace(out=str(out_dir), inbox=args.inbox))
    if not ok:
        sys.exit(1)


def validate(args) -> bool:
    out_dir = Path(args.out)
    inbox_path = Path(args.inbox)
    failures = []

    # 1. Required output files exist.
    for name in REQUIRED_OUTPUT_FILES:
        if not (out_dir / name).exists():
            failures.append(f"Missing required output file: {out_dir / name}")

    if failures:
        _report(failures)
        return False

    # 2. Exactly N input emails, matching classification row count.
    inbox_rows = read_inbox(inbox_path)
    n_input = len(inbox_rows)
    input_ids = [row["id"] for row in inbox_rows]

    class_rows = read_classifications(out_dir / "classifications.csv")
    n_output = len(class_rows)

    if n_output != n_input:
        failures.append(
            f"Expected {n_input} classification rows (one per input email), found {n_output}."
        )

    # 3. No duplicate / missing email IDs, columns present, enum valid.
    seen_ids = []
    for i, row in enumerate(class_rows, start=1):
        missing_cols = [c for c in CLASSIFICATION_COLUMNS if c not in row]
        if missing_cols:
            failures.append(f"classifications.csv row {i}: missing columns {missing_cols}")
            continue

        eid = row["email_id"]
        seen_ids.append(eid)

        cls = row["primary_classification"]
        if cls not in ALLOWED_CLASSIFICATIONS:
            failures.append(
                f"classifications.csv row {i} (email_id={eid}): invalid "
                f"primary_classification '{cls}', must be one of {sorted(ALLOWED_CLASSIFICATIONS)}"
            )

        flags = [f.strip() for f in row.get("flags", "").split(";") if f.strip()]
        bad_flags = [f for f in flags if f not in ALLOWED_FLAGS]
        if bad_flags:
            failures.append(f"classifications.csv row {i} (email_id={eid}): invalid flags {bad_flags}")

    dup_ids = {eid for eid in seen_ids if seen_ids.count(eid) > 1}
    if dup_ids:
        failures.append(f"Duplicate email_id(s) in classifications.csv: {sorted(dup_ids)}")

    missing_ids = set(input_ids) - set(seen_ids)
    if missing_ids:
        failures.append(f"Input email_id(s) missing from classifications.csv: {sorted(missing_ids)}")

    extra_ids = set(seen_ids) - set(input_ids)
    if extra_ids:
        failures.append(f"classifications.csv has email_id(s) not present in the input inbox: {sorted(extra_ids)}")

    # 4. payment_change rows must never recommend an actual bank/payment change.
    change_verbs = re.compile(
        r"\b(update|change|redirect|switch)\b.{0,40}\b(bank|payment|account)\b.{0,20}\bdetails\b",
        re.IGNORECASE,
    )
    for row in class_rows:
        flags = row.get("flags", "")
        if "payment_change" in flags:
            action = row.get("recommended_action", "")
            if change_verbs.search(action) and "do not" not in action.lower() and "never" not in action.lower():
                failures.append(
                    f"email_id={row['email_id']}: payment_change flag but recommended_action "
                    f"does not clearly refuse the change: '{action}'"
                )

    # 5. No output file describes an action as already taken (send/book/pay).
    for name in ["founder-brief.md", "drafts.md", "corrections.md"]:
        text = (out_dir / name).read_text(encoding="utf-8")
        for pattern in PROHIBITED_DONE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                failures.append(f"{name}: contains a phrase implying an action was already taken (matched /{pattern}/)")

    # 6. drafts.md must clearly mark founder-approval-required drafts.
    drafts_text = (out_dir / "drafts.md").read_text(encoding="utf-8")
    if "REQUIRES SAM APPROVAL" not in drafts_text and "REQUIRES APPROVAL" not in drafts_text.upper():
        failures.append("drafts.md does not contain a clearly labeled approval-required marker.")

    _report(failures)
    return not failures


def _report(failures):
    if failures:
        print(f"VALIDATION FAILED ({len(failures)} issue(s)):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
    else:
        print("Validation passed: all deterministic checks OK.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Drive Claude Code (local `claude` CLI) to generate outputs, then validate them.")
    p_run.add_argument("--brief", default="founder-brief.md")
    p_run.add_argument("--inbox", default="inbox.csv")
    p_run.add_argument("--prompt", default="prompt.md")
    p_run.add_argument("--out", default="output")
    p_run.add_argument("--model", default=None, help="Optional model override, e.g. claude-sonnet-5. Defaults to your `claude` CLI's own default.")
    p_run.set_defaults(func=run)

    p_prep = sub.add_parser("prepare", help="Write a single prompt file to paste into claude.ai manually.")
    p_prep.add_argument("--brief", default="founder-brief.md")
    p_prep.add_argument("--inbox", default="inbox.csv")
    p_prep.add_argument("--prompt", default="prompt.md")
    p_prep.add_argument("--to", default="claude_prompt.txt")
    p_prep.set_defaults(func=prepare)

    p_ing = sub.add_parser("ingest", help="Turn a saved claude.ai reply into output/, then validate it.")
    p_ing.add_argument("response_file")
    p_ing.add_argument("--out", default="output")
    p_ing.add_argument("--inbox", default="inbox.csv")
    p_ing.set_defaults(func=ingest)

    p_val = sub.add_parser("validate", help="Run deterministic checks against an existing output/ dir.")
    p_val.add_argument("--out", default="output")
    p_val.add_argument("--inbox", default="inbox.csv")
    p_val.set_defaults(func=lambda a: sys.exit(0 if validate(a) else 1))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
