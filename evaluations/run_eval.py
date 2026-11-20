"""
Score the assistant on 15 questions with known answers.

A question passes when the answer contains the expected text (case-insensitive).
Run from the repo root (needs ANTHROPIC_API_KEY in .env):
    python evaluations/run_eval.py
Results are written to evaluations/results.csv.
"""

import asyncio
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from assistant.agent import ask, build_graph  # noqa: E402

QUESTIONS = ROOT / "evaluations/questions.csv"
RESULTS = ROOT / "evaluations/results.csv"
TARGET = 12


async def main() -> None:
    graph = await build_graph()
    rows = list(csv.DictReader(QUESTIONS.open(encoding="utf-8")))
    passed = 0
    with RESULTS.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "passed", "question", "must_contain", "tools_used", "answer"])
        for row in rows:
            out = await ask(graph, row["question"])
            ok = row["must_contain"].lower() in out["answer"].lower()
            passed += ok
            print(f"{row['id']} {'PASS' if ok else 'FAIL'}  {row['question']}")
            writer.writerow([row["id"], ok, row["question"], row["must_contain"],
                             ";".join(out["tools_used"]), out["answer"]])
    print(f"\nScore: {passed}/{len(rows)} (target {TARGET}). Details in {RESULTS}")
    sys.exit(0 if passed >= TARGET else 1)


if __name__ == "__main__":
    asyncio.run(main())
