/**
 * wwApi Guard: instructor confirmation for the wwMcp tools that change what
 * students are served (tier 2 in wwMcp/PLAN.md).
 *
 * Keyed on tool NAME only, never on arguments: wwMcp gives every live action
 * its own tool so this guard cannot fail open on a status it would have to
 * look up. Keep CONFIRMED in step with CONFIRMED_TOOLS in wwmcp/server.py.
 *
 * Fails closed: a subagent, or any process without a UI, cannot approve.
 * There is deliberately no off switch.
 *
 * The MCP server must be registered under a name starting with `wwapi`
 * (pi/INSTALL.md); a tool registered under another server name is not seen.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const CONFIRMED = new Set([
	"approve_question", "unapprove_question",
	"approve_scenario", "unapprove_scenario",
	"edit_live_question", "tag_live", "untag_live",
]);
const WWAPI_TOOL = /^mcp__wwapi[A-Za-z0-9]*(?:_[A-Za-z0-9]+)*__(.+)$/;

// Which wwApi the MCP server is bound to, resolved the way wwmcp/config.py
// does (WWMCP_PROFILE, else default_profile), so the prompt names the real
// target and its URL. Never reads or shows the key.
export function target(env: NodeJS.ProcessEnv = process.env): string {
	try {
		const path = env.WWMCP_CONFIG || join(homedir(), ".config", "wwmcp", "config.json");
		const cfg = JSON.parse(readFileSync(path, "utf8"));
		const name = env.WWMCP_PROFILE || cfg.default_profile;
		const url = cfg.profiles?.[name]?.base_url;
		if (typeof name === "string" && typeof url === "string") return `${name}: ${url}`;
	} catch {
		// fall through
	}
	return "UNKNOWN TARGET (could not read the wwmcp config)";
}

// The instructor must see everything they approve: nothing is ever truncated.
// What decides correctness and placement comes first (ids, tag, the answer),
// the long text last. An input too big to show whole is refused, not clipped.
const MAX_SHOWN = 8000; // a 2000-char stem + five 500-char choices fits well inside
const FIRST = ["question_id", "scenario_id", "tag", "sub_method", "method_type"];
const ANSWER = ["is_true", "choices", "answer", "webwork", "tags", "format"];

function ordered(obj: Record<string, unknown>): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	const keys = Object.keys(obj);
	for (const k of [...FIRST, ...ANSWER]) if (k in obj) out[k] = obj[k];
	for (const k of keys) if (!(k in out) && k !== "text") out[k] = obj[k];
	if ("text" in obj) out.text = obj.text;
	return out;
}

export function summary(input: unknown): string | undefined {
	const top = ordered((input ?? {}) as Record<string, unknown>);
	if (top.changes && typeof top.changes === "object" && !Array.isArray(top.changes))
		top.changes = ordered(top.changes as Record<string, unknown>);
	const text = JSON.stringify(top, null, 2);
	return text.length > MAX_SHOWN ? undefined : text;
}

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event, ctx) => {
		const tool = WWAPI_TOOL.exec(event.toolName)?.[1];
		if (!tool || !CONFIRMED.has(tool)) return;

		if (process.env.PI_SUBAGENT === "true" || !ctx.hasUI)
			return {
				block: true,
				reason: `${tool} changes what students are served and needs the instructor's confirmation, which this process cannot ask for. Report what you would have done instead.`,
			};

		const shown = summary(event.input);
		if (shown === undefined)
			return {
				block: true,
				reason: `${tool} was refused: its arguments are too long to show the instructor in full, and nothing is approved unseen. Make the change in smaller parts.`,
			};
		const ok = await ctx.ui.confirm(`wwApi [${target()}]: ${tool}?`, shown);
		if (!ok)
			return {
				block: true,
				reason: `The instructor declined ${tool}. Do not retry it; ask what they want instead.`,
			};
	});
}
