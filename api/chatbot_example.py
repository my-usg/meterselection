#!/usr/bin/env python3
"""A worked example of the conversation loop, for whoever wires the chatbot.

Run it to watch a sizing play out turn by turn. It calls the algorithm
directly so it needs no server; swap `size_meters(payload)` for a POST to
/api/meter-sizing and the loop is unchanged.

    python api/chatbot_example.py
    python api/chatbot_example.py --inlet 5 --flow 1500

The shape to copy is the loop in `converse`: keep one payload, add each answer
to it, call again, stop when `questions` is empty. There is no session to
manage and no step counter to keep in step with the questions -- the algorithm
tells you what is still outstanding every time.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from algorithm.meter_sizing import size_meters  # noqa: E402


def answer_question(question, auto):
    """Stand in for the customer.

    `auto` takes the first option every time, which is what makes this
    runnable unattended. A real bot puts `question["label"]` and
    `question["options"]` to the customer and sends back what they say --
    verbatim, since the algorithm matches on the exact strings.
    """
    options = question["options"]
    if question["type"] == "multi_select":
        return [options[0]]
    if auto:
        return options[0]
    print(f"\n{question['label']}")
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input("  > ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw in options:
            return raw
        print("  Pick one of the numbers above.")


def converse(inlet, inlet_units, flow, flow_units, auto=True):
    payload = {
        "inlet": inlet,
        "inlet_units": inlet_units,
        "flow": flow,
        "flow_units": flow_units,
        "meter_types": [],
        "answers": {},
    }

    turn = 0
    while True:
        turn += 1
        result = size_meters(payload)          # <- or POST /api/meter-sizing

        if not result["ok"]:
            print("Cannot size this job:")
            for err in result["errors"]:
                print("  -", err)
            return result

        print(f"\n--- turn {turn}: stage {result['stage']} ---")
        for w in result["warnings"]:
            print("  note:", w)

        questions = result["questions"]
        if not questions:
            # `questions` is empty exactly when the stage is "complete".
            return result

        # Answer one question per turn: a conditional branch can add a
        # question that only exists because of the answer just given, so
        # answering the whole batch at once would ask about things the
        # customer has not been offered yet.
        q = questions[0]
        reply = answer_question(q, auto)
        print(f"  Q: {q['label']}?  A: {reply}")

        if q["id"] == "meter_type":
            payload["meter_types"] = reply
        else:
            payload["answers"][q["id"]] = reply


def present(result):
    """Read the finished selection back the way a bot would say it."""
    print("\n" + "=" * 60)
    if not result.get("selected"):
        print(result.get("message", "No meter selected."))
        return

    for entry in result["results"]:
        print(f"\n{entry['heading']}")
        if not entry.get("available"):
            print("  " + entry["message"])
            continue
        for line in entry["lines"]:
            print("  " + line)
        print("  Part number: " + (entry["part_number"] or entry["quote_note"] or "-"))

    if result.get("eagle"):
        print("\nEagle Option")
        for line in result["eagle"]["lines"]:
            print("  " + line)
        print("  Part number: " + result["eagle"]["part_number"])

    if result["warnings"]:
        print("\nNotes")
        for w in result["warnings"]:
            print("  - " + w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inlet", type=float, default=60)
    ap.add_argument("--inlet-units", default="psi")
    ap.add_argument("--flow", type=float, default=9000)
    ap.add_argument("--flow-units", default="CFH")
    ap.add_argument("--interactive", action="store_true",
                    help="answer the questions yourself instead of taking the "
                         "first option each time")
    args = ap.parse_args()

    print(f"Sizing: {args.inlet} {args.inlet_units}, "
          f"{args.flow} {args.flow_units}")

    result = converse(
        args.inlet, args.inlet_units, args.flow, args.flow_units,
        auto=not args.interactive,
    )
    present(result)


if __name__ == "__main__":
    main()
