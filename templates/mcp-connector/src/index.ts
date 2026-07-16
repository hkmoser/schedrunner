// Minimal MCP server on a Cloudflare Worker (JSON-RPC over HTTP POST).
// Replace the example tool with real tools. Enforce business logic here.

interface JsonRpcReq { jsonrpc: "2.0"; id?: number | string; method: string; params?: any }

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const TOOLS = [
  {
    name: "ping",
    description: "Health check. Returns pong with an optional echo.",
    inputSchema: { type: "object", properties: { echo: { type: "string" } }, required: [] },
  },
];

async function callTool(name: string, args: any) {
  switch (name) {
    case "ping":
      return { content: [{ type: "text", text: `pong${args?.echo ? `: ${args.echo}` : ""}` }] };
    default:
      throw new Error(`unknown tool: ${name}`);
  }
}

export default {
  async fetch(req: Request): Promise<Response> {
    if (req.method !== "POST") return new Response("MCP endpoint. POST JSON-RPC.", { status: 405 });
    const rpc = (await req.json()) as JsonRpcReq;
    const ok = (result: unknown) => json({ jsonrpc: "2.0", id: rpc.id, result });
    const err = (code: number, message: string) => json({ jsonrpc: "2.0", id: rpc.id, error: { code, message } });

    try {
      switch (rpc.method) {
        case "initialize":
          return ok({
            protocolVersion: "2024-11-05",
            capabilities: { tools: {} },
            serverInfo: { name: "mcp-connector", version: "0.1.0" },
          });
        case "tools/list":
          return ok({ tools: TOOLS });
        case "tools/call":
          return ok(await callTool(rpc.params?.name, rpc.params?.arguments ?? {}));
        default:
          return err(-32601, `method not found: ${rpc.method}`);
      }
    } catch (e: any) {
      return err(-32603, e?.message ?? "internal error");
    }
  },
};
