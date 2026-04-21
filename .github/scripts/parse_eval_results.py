import json
import os
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: parse_eval_results.py <results.json>", file=sys.stderr)
        sys.exit(1)

    results_path = sys.argv[1]
    if not results_path:
        print("results.json not found — eval may have crashed", file=sys.stderr)
        sys.exit(1)

    github_output = os.environ["GITHUB_OUTPUT"]

    with open(results_path) as f:
        data = json.load(f)

    v = data.get("version", {})
    trials = data.get("trials", [])
    if not trials:
        print("No trials completed", file=sys.stderr)
        sys.exit(1)

    t = trials[0]
    score = t["composite_score"]
    cost = t["total_cost_usd"]
    turns = t["total_turns"]
    duration = t["total_duration_seconds"]
    build_ok = t["build_complete"]
    error = t.get("error", "")

    graders = []
    for g in t.get("grader_results", []):
        status = "✅" if g["passed"] else "❌"
        graders.append(f'{status} {g["name"]} ({g["score"]:.2f})')

    with open(github_output, "a") as f:
        f.write(f"score={score:.2f}\n")
        f.write(f"cost={cost:.2f}\n")
        f.write(f"turns={turns}\n")
        f.write(f"duration={duration:.0f}\n")
        f.write(f"build_complete={build_ok}\n")
        f.write(f'lightcone_version={v.get("lightcone_version", "unknown")}\n')
        f.write(f'lightcone_commit={v.get("lightcone_commit", "unknown")[:8]}\n')

    with open("grader-details.txt", "w") as f:
        f.write("\n".join(graders))

    if score < 1.0 or error:
        sys.exit(1)


if __name__ == "__main__":
    main()
