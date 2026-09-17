#!/usr/bin/env python3
"""
Founder inbox triage pipeline.

    Founder brief -> hard constraints -> deterministic validation -> Claude
    judgment -> human review -> possible action.

Claude proposes decisions. This script enforces the hard constraints
mechanically and never sends, books, pays, or changes payment details itself.

Usage:
    python3 triage.py run [--brief founder-brief.md] [--inbox inbox.csv] [--out output/]
    python3 triage.py validate [--out output/] [--inbox inbox.csv]

`run` calls the Claude API (requires ANTHROPIC_API_KEY and the `anthropic`
package: `pip install anthropic`) to generate the four output files, then
immediately runs the same validation as `validate`. `validate` can be run on
its own against any already-generated output/ directory, with no API access,
which is what CI or a human reviewer should do before anything in output/ is
acted on.
"""
import argparse
import csv
import json
import re
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


def call_claude(prompt: str, model: str = "claude-sonnet-5") -> str:
    try:
        import anthropic
    except ImportError as e:
        raise SystemExit(
            "The 'anthropic' package is required for `triage.py run`.\n"
            "Install it with: pip install anthropic\n"
            "Then set ANTHROPIC_API_KEY and re-run."
        ) from e

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def run(args):
    brief_path = Path(args.brief)
    inbox_path = Path(args.inbox)
    prompt_path = Path(args.prompt)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    brief_text = brief_path.read_text(encoding="utf-8")
    prompt_text = prompt_path.read_text(encoding="utf-8")
    inbox_rows = read_inbox(inbox_path)

    n = len(inbox_rows)
    print(f"Loaded {n} emails from {inbox_path}.")

    full_prompt = build_prompt(brief_text, inbox_rows, prompt_text) + (
        "\n\nRespond with ONLY a JSON object with these exact keys, no other "
        "text: \"classifications_csv\" (a string, the full CSV content "
        "including header), \"founder_brief_md\" (string), \"drafts_md\" "
        "(string), \"corrections_md\" (string)."
    )

    print("Calling Claude to generate classifications, brief, drafts, and corrections...")
    raw = call_claude(full_prompt, model=args.model)

    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        payload = json.loads(match.group(0) if match else raw)
    except (json.JSONDecodeError, AttributeError) as e:
        raise SystemExit(f"Could not parse Claude's response as JSON: {e}\n\nRaw response:\n{raw}")

    (out_dir / "classifications.csv").write_text(payload["classifications_csv"], encoding="utf-8")
    (out_dir / "founder-brief.md").write_text(payload["founder_brief_md"], encoding="utf-8")
    (out_dir / "drafts.md").write_text(payload["drafts_md"], encoding="utf-8")
    (out_dir / "corrections.md").write_text(payload["corrections_md"], encoding="utf-8")
    print(f"Wrote outputs to {out_dir}/")

    ok = validate(argparse.Namespace(out=str(out_dir), inbox=str(inbox_path)))
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

    p_run = sub.add_parser("run", help="Call Claude to generate outputs, then validate them.")
    p_run.add_argument("--brief", default="founder-brief.md")
    p_run.add_argument("--inbox", default="inbox.csv")
    p_run.add_argument("--prompt", default="prompt.md")
    p_run.add_argument("--out", default="output")
    p_run.add_argument("--model", default="claude-sonnet-5")
    p_run.set_defaults(func=run)

    p_val = sub.add_parser("validate", help="Run deterministic checks against an existing output/ dir.")
    p_val.add_argument("--out", default="output")
    p_val.add_argument("--inbox", default="inbox.csv")
    p_val.set_defaults(func=lambda a: sys.exit(0 if validate(a) else 1))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
