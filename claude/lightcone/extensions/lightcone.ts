import * as fs from "node:fs";
import * as path from "node:path";

import {
	createBashTool,
	type ExtensionAPI,
	type ExtensionContext,
} from "@earendil-works/pi-coding-agent";

const SESSION_CONTEXT_TYPE = "lightcone-session-start";
const EXEC_TIMEOUT_MS = 15_000;
const SESSION_ERROR_TAIL_LINES = 20;
const VALIDATION_ERROR_TAIL_LINES = 40;

const SKILL_ALIASES = [
	["lc-new", "Scope a new ASTRA analysis from a research question"],
	["lc-from-code", "Wrap an existing codebase in ASTRA"],
	["lc-from-paper", "Reproduce a paper end-to-end in ASTRA"],
	["lc-feedback", "File a GitHub issue against the right Lightcone repo"],
	["paper-extraction", "Acquire a paper into the standardized ASTRA substrate"],
	["narrative", "Author ASTRA narrative and rationale prose"],
	["figure-comparison", "Build a paper-vs-reproduction comparison view"],
	["check-sentence-by-sentence", "Audit paper claims against code locations"],
	["astra", "Load the ASTRA specification reference skill"],
	["lc-cli", "Load the lc workflow reference skill"],
] as const;

type ExecResult = {
	stdout: string;
	stderr: string;
	code: number;
};

type StatusCounts = {
	ok: number;
	stale: number;
	missing: number;
	alias: number;
};

type ValidationTarget = {
	projectRoot: string;
	targetArg: string;
	displayName: string;
};

function findAstraProjectRoot(start: string): string | null {
	let current = path.resolve(start);

	while (true) {
		if (fs.existsSync(path.join(current, "astra.yaml"))) {
			return current;
		}

		const parent = path.dirname(current);
		if (parent === current) {
			return null;
		}
		current = parent;
	}
}

function getProjectVenv(projectRoot: string | null): { venv: string; bin: string } | null {
	if (!projectRoot) {
		return null;
	}

	const venv = path.join(projectRoot, ".venv");
	const bin = path.join(venv, "bin");
	return fs.existsSync(bin) ? { venv, bin } : null;
}

function withProjectVenvEnv(cwd: string, env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
	const venv = getProjectVenv(findAstraProjectRoot(cwd));
	if (!venv) {
		return env;
	}

	const existingPath = env.PATH ?? process.env.PATH ?? "";
	return {
		...env,
		VIRTUAL_ENV: venv.venv,
		PATH: existingPath ? `${venv.bin}:${existingPath}` : venv.bin,
	};
}

function resolveProjectExecutable(projectRoot: string, executable: string): string {
	const candidate = path.join(projectRoot, ".venv", "bin", executable);
	return fs.existsSync(candidate) ? candidate : executable;
}

function combineOutput(result: ExecResult | undefined): string {
	if (!result) {
		return "";
	}

	return [result.stdout, result.stderr].filter(Boolean).join("\n").trim();
}

function tailLines(text: string, maxLines: number): string {
	const lines = text.split(/\r?\n/);
	if (lines.length <= maxLines) {
		return text.trim();
	}

	return `${lines.slice(-maxLines).join("\n")}\n[... ${lines.length - maxLines} earlier line(s) omitted]`;
}

function isMissingCommand(result: ExecResult | undefined): boolean {
	if (!result) {
		return true;
	}

	if (result.code === 127) {
		return true;
	}

	const combined = combineOutput(result);
	return result.code !== 0 && /\b(not found|No such file or directory)\b/i.test(combined);
}

function countMaterializationStatuses(statusJson: string): StatusCounts {
	const counts: StatusCounts = { ok: 0, stale: 0, missing: 0, alias: 0 };

	try {
		const payload = JSON.parse(statusJson) as {
			universes?: Array<{ outputs?: Array<{ status?: string }> }>;
		};
		for (const universe of payload.universes ?? []) {
			for (const output of universe.outputs ?? []) {
				const status = output.status;
				if (status === "ok" || status === "stale" || status === "missing" || status === "alias") {
					counts[status] += 1;
				}
			}
		}
	} catch {
		// Keep the zero counts — lc status can fail while the project is mid-edit.
	}

	return counts;
}

function hasPrimerMessage(ctx: ExtensionContext): boolean {
	return ctx.sessionManager.getEntries().some((entry) => {
		if (entry.type !== "message") {
			return false;
		}
		const message = (entry as { message?: { customType?: string } }).message;
		return message?.customType === SESSION_CONTEXT_TYPE;
	});
}

function validationTargetFor(cwd: string, rawPath: string): ValidationTarget | null {
	const cleanedPath = rawPath.startsWith("@") ? rawPath.slice(1) : rawPath;
	const absolutePath = path.resolve(cwd, cleanedPath);
	const filename = path.basename(absolutePath);
	const parent = path.basename(path.dirname(absolutePath));

	if (filename === "astra.yaml") {
		return {
			projectRoot: path.dirname(absolutePath),
			targetArg: "astra.yaml",
			displayName: filename,
		};
	}

	if (parent === "universes" && filename.endsWith(".yaml")) {
		return {
			projectRoot: path.dirname(path.dirname(absolutePath)),
			targetArg: absolutePath,
			displayName: filename,
		};
	}

	return null;
}

async function safeExec(
	pi: ExtensionAPI,
	command: string,
	args: string[],
	cwd: string,
): Promise<ExecResult | undefined> {
	return pi.exec(command, args, { cwd, timeout: EXEC_TIMEOUT_MS }).catch(() => undefined);
}

async function buildSessionStartSummary(
	pi: ExtensionAPI,
	projectRoot: string,
): Promise<string | null> {
	const astra = resolveProjectExecutable(projectRoot, "astra");
	const lc = resolveProjectExecutable(projectRoot, "lc");
	const [validationResult, statusResult] = await Promise.all([
		safeExec(pi, astra, ["validate", "astra.yaml"], projectRoot),
		safeExec(pi, lc, ["status", "--json"], projectRoot),
	]);

	if (isMissingCommand(validationResult) || isMissingCommand(statusResult)) {
		return null;
	}

	const counts = countMaterializationStatuses(statusResult?.stdout ?? "");
	const validationOk = (validationResult?.code ?? 1) === 0;
	let summary = validationOk
		? "ASTRA project — validation: valid"
		: "ASTRA project — validation: has errors";

	summary += `
Materialization: ok=${counts.ok} stale=${counts.stale} missing=${counts.missing} alias=${counts.alias}

Substrate CLIs (use --help on any):
  lc init / lc run / lc status / lc verify / lc build / lc export wrroc
  astra validate / astra paper add / astra universe generate

ACTIVATE THE /lightcone SKILL before running commands or editing files. It is
the entry point — the lay of the land, the references you almost always need
(the astra.yaml spec and the lc workflow, bundled with it), and a router to
task skills like /lc-from-paper.`;

	if (!validationOk) {
		const validationPreview = tailLines(combineOutput(validationResult), SESSION_ERROR_TAIL_LINES);
		summary += `

Validation errors (run 'astra validate astra.yaml' for full output):
${validationPreview}`;
	}

	const needsRun = counts.missing + counts.stale;
	if (needsRun > 0) {
		summary += `

ACTION REQUIRED: ${needsRun} output(s) need \`lc run\` (${counts.missing} missing, ${counts.stale} stale).`;
	}

	return summary;
}

async function buildValidationMessage(
	pi: ExtensionAPI,
	target: ValidationTarget,
): Promise<string | null> {
	const astra = resolveProjectExecutable(target.projectRoot, "astra");
	const result = await safeExec(pi, astra, ["validate", target.targetArg], target.projectRoot);
	if (isMissingCommand(result)) {
		return null;
	}

	if ((result?.code ?? 1) === 0) {
		return `ASTRA validation passed for ${target.displayName}`;
	}

	const detail = tailLines(combineOutput(result), VALIDATION_ERROR_TAIL_LINES) || "(no validator output)";
	return `ASTRA validation FAILED for ${target.displayName}:\n${detail}`;
}

function registerSkillAlias(pi: ExtensionAPI, skillName: string, description: string): void {
	pi.registerCommand(skillName, {
		description: `${description} (alias for /skill:${skillName})`,
		handler: async (args) => {
			const suffix = args.trim() ? ` ${args.trim()}` : "";
			pi.sendUserMessage(`/skill:${skillName}${suffix}`);
		},
	});
}

export default function lightconeExtension(pi: ExtensionAPI) {
	let primerInjected = false;

	for (const [skillName, description] of SKILL_ALIASES) {
		registerSkillAlias(pi, skillName, description);
	}

	pi.on("session_start", async (_event, ctx) => {
		primerInjected = hasPrimerMessage(ctx);
	});

	const baseBashTool = createBashTool(process.cwd(), {
		spawnHook: ({ command, cwd, env }) => ({
			command,
			cwd,
			env: withProjectVenvEnv(cwd, env),
		}),
	});

	pi.registerTool({
		...baseBashTool,
		execute: async (toolCallId, params, signal, onUpdate, ctx) => {
			const bashTool = createBashTool(ctx.cwd, {
				spawnHook: ({ command, cwd, env }) => ({
					command,
					cwd,
					env: withProjectVenvEnv(cwd, env),
				}),
			});
			return bashTool.execute(toolCallId, params, signal, onUpdate);
		},
	});

	pi.on("before_agent_start", async (_event, ctx) => {
		if (primerInjected) {
			return;
		}

		const projectRoot = findAstraProjectRoot(ctx.cwd);
		if (!projectRoot) {
			return;
		}

		const summary = await buildSessionStartSummary(pi, projectRoot);
		if (!summary) {
			return;
		}

		primerInjected = true;
		return {
			message: {
				customType: SESSION_CONTEXT_TYPE,
				content: summary,
				display: false,
			},
		};
	});

	pi.on("tool_result", async (event, ctx) => {
		if (event.isError || !["edit", "write"].includes(event.toolName)) {
			return;
		}

		const rawPath = (event.input as { path?: unknown }).path;
		if (typeof rawPath !== "string") {
			return;
		}

		const target = validationTargetFor(ctx.cwd, rawPath);
		if (!target) {
			return;
		}

		const message = await buildValidationMessage(pi, target);
		if (!message) {
			return;
		}

		return {
			content: Array.isArray(event.content)
				? [...event.content, { type: "text", text: message }]
				: [{ type: "text", text: message }],
		};
	});
}
