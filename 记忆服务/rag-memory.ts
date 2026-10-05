// rag-memory.ts —— pi 记忆注入钩子（批次一 §二）
//
// 契约（不绑定具体 API 的那三条）：
//   1. 每次 agent 准备行动之前，拿【当前任务描述】调 POST /retrieve
//   2. 把返回的 units 拼成一段上下文，插在系统提示之后、用户任务之前
//      —— 用 context 事件实现精确插位：custom 消息插到最后一条 user 消息前，
//      系统提示不在 messages 数组里，天然在它之后
//   3. 记录本次注入了哪些 doc_id（runs.jsonl，§四）
//
// 开关（§2.2）：
//   --memory on   调 /retrieve 并注入
//   --memory off  完全不调接口，其余一字不变（不是删提示词）
//   系统提示本扩展从不改写 → 两组系统提示逐字相同由构造保证。
//
// 服务只听 127.0.0.1，扩展与它同机。服务挂了不阻塞 agent：落一条 error
// 记录后按无记忆继续跑，演示时这就是"降级路径也可演示"。

import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

const MEMORY_URL = process.env.RAG_MEMORY_URL ?? "http://127.0.0.1:8077";
const RUNS_PATH =
  process.env.RAG_RUNS_PATH ??
  path.join(os.homedir(), "RAG", "memory_service", "runs.jsonl");
const MODEL_LABEL = process.env.RAG_MODEL_LABEL ?? "unknown";
const K = Number(process.env.RAG_MEMORY_K ?? 5);

interface Unit {
  doc_id: number;
  type: string;
  text: string;
  score: number | null;
}

interface Pending {
  units: Unit[];
  docIds: number[];
  config: string;
  retrievalMs: number;
}

let pending: Pending | null = null;

function formatBlock(units: Unit[]): string {
  const lines = units.map(
    (u, i) =>
      `[${i + 1}] (${u.type}) ${u.text.replace(/\s*\n+\s*/g, " ").slice(0, 400)}`,
  );
  return [
    `=== 相关记忆（来自本机知识库，共 ${units.length} 条） ===`,
    ...lines,
    "=== 记忆结束（以上是历史经验，供参考；与当前任务冲突时以当前任务为准） ===",
  ].join("\n");
}

function appendRun(obj: Record<string, unknown>): void {
  try {
    fs.mkdirSync(path.dirname(RUNS_PATH), { recursive: true });
    fs.appendFileSync(RUNS_PATH, JSON.stringify(obj) + "\n");
  } catch {
    // 运行记录写不进去不该弄垮 agent 本体；演示主线在内存链路上
  }
}

export default function (pi: any) {
  pi.registerFlag("memory", {
    description: "RAG 记忆注入开关: on/off（off=完全不调记忆接口）",
    type: "string",
    default: "on",
  });

  pi.on("before_agent_start", async (event: any) => {
    pending = null; // 新任务开始：上一任务的记忆块失效
    if ((pi.getFlag("memory") ?? "on") !== "on") {
      return; // off 组：到此为止，一个字节都不发给记忆服务
    }

    const base = {
      run_id: `${Date.now().toString(36)}${Math.random()
        .toString(36)
        .slice(2, 8)}`,
      ts: new Date().toISOString(),
      model: MODEL_LABEL,
      task: event.prompt,
      memory: true,
      retrieval_config: null as string | null,
      injected_doc_ids: [] as number[],
    };

    try {
      const res = await fetch(`${MEMORY_URL}/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: event.prompt, k: K }),
        signal: AbortSignal.timeout(30000),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const units: Unit[] = data.units ?? [];
      pending = {
        units,
        docIds: units.map((u) => u.doc_id),
        config: data.config,
        retrievalMs: data.elapsed_ms,
      };
      appendRun({
        ...base,
        retrieval_config: data.config,
        injected_doc_ids: pending.docIds,
        retrieval_ms: data.elapsed_ms,
      });
    } catch (e) {
      appendRun({ ...base, retrieval_config: null, error: String(e) });
    }
  });

  pi.on("context", async (event: any) => {
    if (!pending || pending.units.length === 0) return;
    const msgs = event.messages;
    // 找最后一条 user 消息（=当前任务），把记忆块插在它前面
    let idx = -1;
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i]?.role === "user") {
        idx = i;
        break;
      }
    }
    if (idx === -1) return;
    const memMsg = {
      role: "custom",
      customType: "rag-memory",
      content: formatBlock(pending.units),
      display: true,
      timestamp: Date.now(),
    };
    msgs.splice(idx, 0, memMsg);
    return { messages: msgs };
  });
}
