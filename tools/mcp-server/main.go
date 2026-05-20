// FuzzingBrain Bench MCP server.
//
// Speaks line-delimited JSON-RPC 2.0 on stdin/stdout per the MCP transport
// convention. Implements the 6-tool contract from SPEC §4.
//
// Environment:
//   BENCH_BUG_DIR    absolute path to the agent-facing bug dir (no oracle files)
//   BENCH_WORKSPACE  absolute path to the runner's per-episode tmpdir
//   BENCH_ORACLE_DIR absolute path the grader reads expected.yaml + binaries
//                    from. Defaults to BENCH_BUG_DIR (so the no-AI fb-bench
//                    CLI, which points BUG_DIR at the real bug dir, still works).
//   BENCH_AGENT_UID  numeric uid to run exec() as. When set and the server is
//   BENCH_AGENT_GID  root, exec subprocesses drop to this (uid,gid) so the
//                    agent's shell cannot read root-owned oracle files even by
//                    absolute path / `find /`. No-op when unset or not root.
//
// Diagnostics go to stderr; nothing else.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
)

type rpcRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      json.RawMessage `json:"id,omitempty"`
	Result  interface{}     `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type server struct {
	bugDir    string
	workspace string
	oracleDir string
	// agentUID/agentGID are >0 and dropPrivs true only when BENCH_AGENT_UID is
	// set and the server runs as root; exec() then drops to this credential.
	agentUID  uint32
	agentGID  uint32
	dropPrivs bool
	enc       *json.Encoder
}

func main() {
	log.SetPrefix("mcp-server: ")
	log.SetOutput(os.Stderr)

	bugDir := os.Getenv("BENCH_BUG_DIR")
	workspace := os.Getenv("BENCH_WORKSPACE")
	if bugDir == "" || workspace == "" {
		log.Fatal("BENCH_BUG_DIR and BENCH_WORKSPACE must be set")
	}
	oracleDir := os.Getenv("BENCH_ORACLE_DIR")
	if oracleDir == "" {
		oracleDir = bugDir
	}

	if err := os.MkdirAll(workspace, 0o755); err != nil {
		log.Fatalf("workspace: %v", err)
	}

	srv := &server{
		bugDir:    bugDir,
		workspace: workspace,
		oracleDir: oracleDir,
		enc:       json.NewEncoder(os.Stdout),
	}

	// Tier 2: privilege separation for exec(). Only engages when we are root
	// and an agent uid is configured — otherwise exec runs as the server uid
	// (unchanged behaviour, relying on Tier 1 sandbox staging by the runner).
	if uidStr := os.Getenv("BENCH_AGENT_UID"); uidStr != "" && os.Geteuid() == 0 {
		uid, err := strconv.Atoi(uidStr)
		if err != nil || uid <= 0 {
			log.Fatalf("BENCH_AGENT_UID must be a positive integer, got %q", uidStr)
		}
		gid := uid
		if g := os.Getenv("BENCH_AGENT_GID"); g != "" {
			if gv, err := strconv.Atoi(g); err == nil && gv > 0 {
				gid = gv
			}
		}
		srv.agentUID = uint32(uid)
		srv.agentGID = uint32(gid)
		srv.dropPrivs = true
		// The agent's exec/write_file land in workspace; make it owned by the
		// unprivileged uid so the shell can create and edit files there.
		if err := chownTree(workspace, uid, gid); err != nil {
			log.Printf("warn: chown workspace: %v", err)
		}
		log.Printf("privilege separation on: exec() runs as uid=%d gid=%d", uid, gid)
	}

	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 0, 64*1024), 16*1024*1024)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var req rpcRequest
		if err := json.Unmarshal(line, &req); err != nil {
			srv.writeError(nil, -32700, "parse error", err.Error())
			continue
		}
		srv.dispatch(&req)
	}
	if err := scanner.Err(); err != nil {
		log.Printf("stdin: %v", err)
	}
}

func (s *server) dispatch(req *rpcRequest) {
	switch req.Method {
	case "initialize":
		s.writeResult(req.ID, map[string]any{
			"protocolVersion": "2024-11-05",
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo": map[string]any{
				"name":    "fuzzingbrain-bench",
				"version": "0.1.0",
			},
		})
	case "notifications/initialized":
		// no response for notifications
	case "tools/list":
		s.writeResult(req.ID, map[string]any{"tools": toolSchemas()})
	case "tools/call":
		s.handleToolCall(req)
	default:
		s.writeError(req.ID, -32601, "method not found", req.Method)
	}
}

type toolCallParams struct {
	Name      string          `json:"name"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
}

func (s *server) handleToolCall(req *rpcRequest) {
	var p toolCallParams
	if err := json.Unmarshal(req.Params, &p); err != nil {
		s.writeError(req.ID, -32602, "invalid params", err.Error())
		return
	}
	var (
		result any
		err    error
	)
	switch p.Name {
	case "setup":
		result, err = s.toolSetup(p.Arguments)
	case "exec":
		result, err = s.toolExec(p.Arguments)
	case "list_directory":
		result, err = s.toolListDirectory(p.Arguments)
	case "read_file":
		result, err = s.toolReadFile(p.Arguments)
	case "write_file":
		result, err = s.toolWriteFile(p.Arguments)
	case "grade":
		result, err = s.toolGrade(p.Arguments)
	default:
		s.writeError(req.ID, -32602, "unknown tool", p.Name)
		return
	}
	if err != nil {
		s.writeError(req.ID, -32000, "tool error", err.Error())
		return
	}
	payload, _ := json.Marshal(result)
	s.writeResult(req.ID, map[string]any{
		"content": []map[string]any{
			{"type": "text", "text": string(payload)},
		},
		"structuredContent": result,
	})
}

func (s *server) writeResult(id json.RawMessage, v any) {
	if id == nil {
		return
	}
	if err := s.enc.Encode(rpcResponse{JSONRPC: "2.0", ID: id, Result: v}); err != nil {
		log.Printf("encode result: %v", err)
	}
}

func (s *server) writeError(id json.RawMessage, code int, msg string, data any) {
	resp := rpcResponse{JSONRPC: "2.0", ID: id, Error: &rpcError{Code: code, Message: msg, Data: data}}
	if id == nil {
		resp.ID = json.RawMessage("null")
	}
	if err := s.enc.Encode(resp); err != nil {
		log.Printf("encode error: %v", err)
	}
}

func toolSchemas() []map[string]any {
	return []map[string]any{
		{
			"name":        "setup",
			"description": "Return bug metadata and workspace pointers.",
			"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}},
		},
		{
			"name":        "exec",
			"description": "Run a shell command via /bin/bash -c. cwd = BENCH_BUG_DIR.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"cmd":       map[string]any{"type": "string"},
					"timeout_s": map[string]any{"type": "integer"},
				},
				"required": []string{"cmd"},
			},
		},
		{
			"name":        "list_directory",
			"description": "List directory entries.",
			"inputSchema": map[string]any{
				"type":       "object",
				"properties": map[string]any{"path": map[string]any{"type": "string"}},
				"required":   []string{"path"},
			},
		},
		{
			"name":        "read_file",
			"description": "Read a file. Denied for oracle answer keys; see SPEC §4.4.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path":   map[string]any{"type": "string"},
					"offset": map[string]any{"type": "integer"},
					"limit":  map[string]any{"type": "integer"},
				},
				"required": []string{"path"},
			},
		},
		{
			"name":        "write_file",
			"description": "Write a file under BENCH_WORKSPACE.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path":    map[string]any{"type": "string"},
					"content": map[string]any{"type": "string"},
				},
				"required": []string{"path", "content"},
			},
		},
		{
			"name":        "grade",
			"description": "Grade a candidate PoC. Returns capability bitmap.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path": map[string]any{"type": "string"},
					"options": map[string]any{
						"type": "object",
						"properties": map[string]any{
							"round_count": map[string]any{"type": "integer"},
						},
					},
				},
				"required": []string{"path"},
			},
		},
	}
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return fmt.Sprintf("%v", v)
	}
	return string(b)
}

// chownTree recursively chowns root to (uid,gid). Best-effort: it logs and
// continues past individual failures so a single odd entry can't abort startup.
func chownTree(root string, uid, gid int) error {
	return filepath.Walk(root, func(p string, _ os.FileInfo, err error) error {
		if err != nil {
			return nil
		}
		if err := os.Lchown(p, uid, gid); err != nil {
			log.Printf("warn: chown %s: %v", p, err)
		}
		return nil
	})
}
