// Logic test for wwapi-guard.ts against a fake pi API. Needs a wwmcp config:
//   node --experimental-strip-types pi/test-wwapi-guard.ts

import guard, { target } from "./wwapi-guard.ts";
let handler: any;
guard({ on: (_e: string, h: any) => (handler = h) } as any);
const asked: string[] = [];
const ui = (answer: boolean) => ({ hasUI: true, ui: { confirm: async (t: string) => (asked.push(t), answer) } });
const run = async (name: string, ctx: any) => handler({ toolName: name, input: { question_id: "q_x_0001" } }, ctx);
const out: [string, boolean][] = [];
const ok = (label: string, cond: boolean) => out.push([label, cond]);
ok("free tool passes untouched", (await run("mcp__wwapi__save_question_drafts", ui(false))) === undefined);
ok("other server's tool ignored", (await run("mcp__papers__approve_question", ui(false))) === undefined);
ok("confirmed + yes passes", (await run("mcp__wwapi__approve_question", ui(true))) === undefined);
ok("confirmed + no blocks", (await run("mcp__wwapi__approve_question", ui(false)))?.block === true);
ok("wwapi_dev server name also guarded", (await run("mcp__wwapi_dev__tag_live", ui(false)))?.block === true);
ok("no UI blocks without asking", (await run("mcp__wwapi__untag_live", { hasUI: false }))?.block === true);
process.env.PI_SUBAGENT = "true";
const n = asked.length;
ok("subagent blocks without asking", (await run("mcp__wwapi__edit_live_question", ui(true)))?.block === true && asked.length === n);
delete process.env.PI_SUBAGENT;
process.env.PI_WWAPI_GUARD = "0";
ok("no off switch", (await run("mcp__wwapi__unapprove_scenario", ui(false)))?.block === true);
ok("prompt names target and URL", /wwApi \[.+: https?:\/\/.*\]: approve_question\?/.test(asked[0]));
ok("unknown config says so", target({ WWMCP_CONFIG: "/nonexistent" } as any).startsWith("UNKNOWN"));
for (const [l, c] of out) console.log((c ? "PASS " : "FAIL ") + l);
console.log(asked[0]);
process.exit(out.every(([, c]) => c) ? 0 : 1);
