You are reviewing one leg of an automated eval: an autonomous coding agent
was given the task instruction below and had to carry it out with the
`astra` (spec) and `lc` (execution) CLIs, guided only by that instruction,
the Lightcone agent skills installed into its harness, and the CLIs' own
help. The input on stdin has, in order: the task instruction, a run summary
(reward, agent steps, main's median steps), the deterministic evidence (tool
calls by phase against main, failed tool calls, repeated commands, harness
exceptions, verifier output), the change under test (commits and diffstat of
the repository whose pull request this is), this run's trace, and the trace
of the latest successful main run of the same leg and task.

Your job is to explain *why* this run went the way it did, so a reviewer can
act on it without opening the trace. Cite trace steps as `step N` and
evidence lines verbatim where it matters. Do not restate the task, do not
summarize a successful build, and do not pad.

Write exactly these sections, as markdown:

#### Confusion & pain points

3-6 concise bullets. For each: what happened, its root cause, and the fix it
suggests when one is clear. Look for:

- failed commands or errored tool results, and their root cause
- moments where the agent misunderstood the spec format, the CLI surface,
  the sandbox, or the execution environment
- whether the skills were loaded and whether they told the agent what it
  needed; where it fell back to `--help`, source reading or trial and error
- detours: reverse-engineering source code, probing the environment, redoing
  work, or fixing things the harness should have provided
- workarounds the agent invented that hint at a product gap — call out what
  lightcone-cli, the skills, or the eval setup should fix

If the run was essentially clean, say so in one bullet.

#### What changed vs main

Only when a baseline trace is present. Compare the two runs: which phase the
step difference sits in (use the phase table), the first point where the two
traces diverge, and which commit or file in the change under test most
plausibly caused it — or say plainly that the difference looks like agent
variance, and why. If reward or breakage changed, explain that first. When
there is no baseline trace, write one line saying so.

Output only these two sections, no preamble.
