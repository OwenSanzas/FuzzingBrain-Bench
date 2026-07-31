// FuzzingBrain Bench MCP server.
//
// Speaks line-delimited JSON-RPC 2.0 on stdin/stdout per the MCP transport
// convention. Implements the 6-tool contract from SPEC §4.
//
// Environment:
//
//	BENCH_BUG_DIR    absolute path to the agent-facing bug dir (no oracle files)
//	BENCH_WORKSPACE  absolute path to the runner's per-episode tmpdir
//	BENCH_ORACLE_DIR absolute path the grader reads expected.yaml + binaries
//	                 from. Defaults to BENCH_BUG_DIR (so the no-AI fb-bench
//	                 CLI, which points BUG_DIR at the real bug dir, still works).
//	BENCH_AGENT_UID  numeric uid to run exec() as. When set and the server is
//	BENCH_AGENT_GID  root, exec subprocesses drop to this (uid,gid) so the
//	                 agent's shell cannot read root-owned oracle files even by
//	                 absolute path / `find /`. No-op when unset or not root.
//
// Diagnostics go to stderr; nothing else.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

// probeNetNS reports whether this host can create an unprivileged user+network
// namespace (`unshare -r -n`). When true, exec() runs each command inside one
// so it has no route to the internet.
func probeNetNS() bool {
	return exec.Command("unshare", "-r", "-n", "--", "true").Run() == nil
}

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
	// gradeURL: when set (BENCH_GRADE_URL), grade() does NOT touch a local oracle
	// — it POSTs the candidate input to a remote grading service and returns its
	// verdict. This is the sealed-challenge path: the challenge ships only source
	// + harness (no answers); the answer-bearing oracle lives behind gradeURL.
	// bugID (BENCH_BUG_ID) selects which oracle the remote grades against.
	gradeURL string
	bugID    string
	// agentUID/agentGID are >0 and dropPrivs true only when BENCH_AGENT_UID is
	// set and the server runs as root; exec() then drops to this credential.
	agentUID  uint32
	agentGID  uint32
	dropPrivs bool
	// netIsolate: run exec() inside a fresh user+network namespace so the
	// agent's shell has NO internet (only a down loopback). Probed at startup.
	// allowNet: explicit override to permit networked exec when isolation is
	// unavailable (BENCH_ALLOW_NET=1) — otherwise exec() is refused.
	netIsolate bool
	allowNet   bool
	enc        *json.Encoder
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
		gradeURL:  os.Getenv("BENCH_GRADE_URL"),
		bugID:     os.Getenv("BENCH_BUG_ID"),
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

	// Network isolation: prevent the agent's exec() from reaching the internet
	// (a cheat vector — fetching the upstream issue / source / reference PoC).
	srv.allowNet = os.Getenv("BENCH_ALLOW_NET") == "1"
	srv.netIsolate = probeNetNS()
	switch {
	case srv.netIsolate:
		log.Printf("network isolation ON: exec() runs in an isolated net namespace (no internet)")
	case srv.allowNet:
		log.Printf("WARNING: net-namespace isolation unavailable; BENCH_ALLOW_NET=1 set — exec() HAS internet access")
	default:
		log.Printf("network isolation UNAVAILABLE: exec() will be refused (set BENCH_ALLOW_NET=1 to override)")
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
	// Facts about the episode that the RUNNER attaches, never the model. It
	// travels beside `arguments` rather than inside them so it is outside the
	// tool schema the agent fills in, which makes it unreadable and unforgeable
	// from the agent's side.
	Meta struct {
		Turn int `json:"turn,omitempty"`
	} `json:"_meta,omitempty"`
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
	case "run_poc_on_harness":
		result, err = s.toolGrade(p.Arguments, p.Meta.Turn)
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
			"description": "Return task info: the environment (workspace + source paths), the target project and language, and the harness configuration (type, entrypoint, argv, sanitizer).",
			"inputSchema": map[string]any{"type": "object", "properties": map[string]any{}},
		},
		{
			"name":        "exec",
			"description": "Run a shell command with /bin/bash -c in the challenge source root. NO network access. Returns stdout + stderr (each truncated to 2000 chars), exit_code, and duration_ms.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"cmd":       map[string]any{"type": "string", "description": "The shell command to run."},
					"timeout_s": map[string]any{"type": "integer", "description": "Wall-clock timeout in seconds (default 60)."},
				},
				"required": []string{"cmd"},
			},
		},
		{
			"name":        "list_directory",
			"description": "List a directory's entries (must be under the challenge source or workspace). Not recursive. Returns each entry's name, type (file | dir | symlink), and size in bytes, plus total_entries and truncated (entries are capped at 1000; if truncated, narrow the path).",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path": map[string]any{"type": "string", "description": "Directory to list. Absolute (under the source or workspace), or relative to the source root."},
				},
				"required": []string{"path"},
			},
		},
		{
			"name":        "read_file",
			"description": "Read a file (under the challenge source or workspace) as text, returned in cat -n format (line numbers, for stable references). Paths outside, and the oracle answer keys, return \"permission denied\". Output is capped (2000 lines, 2000 chars/line, 128 KB total); returns content, total_lines, lines_shown, truncated, and next_offset — if truncated, read on with offset=next_offset.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path":   map[string]any{"type": "string", "description": "File to read. Absolute (under source/workspace), or relative to the source root."},
					"offset": map[string]any{"type": "integer", "description": "Line number to start from, 1-based (default 1)."},
					"limit":  map[string]any{"type": "integer", "description": "Max number of lines to read (default 2000)."},
				},
				"required": []string{"path"},
			},
		},
		{
			"name":        "write_file",
			"description": "Write a file to the workspace (your candidate PoC, or a generator script you then exec). The workspace is the only writable area; the challenge source is read-only. Parent directories are created as needed. Returns bytes_written.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path":    map[string]any{"type": "string", "description": "Destination path. Absolute, under the workspace (e.g. /workspace/poc.bin). Relative paths resolve to the read-only source and are rejected."},
					"content": map[string]any{"type": "string", "description": "File contents (UTF-8 text). For a binary PoC, write a generator script and run it with exec."},
				},
				"required": []string{"path", "content"},
			},
		},
		{
			"name":        "run_poc_on_harness",
			"description": "Run a candidate input through the harness (its sanitizer and invocation config come from the setup task info), like running a fuzzer on one input. Returns the raw harness output (stdout, stderr, exit_code, signal) and duration_ms. It does NOT return a pass/fail verdict.\n\nThe input is run several times. If it faults, crash_novelty describes the result, relative only to what you yourself have already submitted in this session:\n  new             a crash you had not produced before\n  duplicate       the same crash you already produced; submitting it again adds nothing\n  flaky_rounds    faulted in only some of the runs, so it does not count; make it deterministic\n  flaky_location  faulted in every run but in a different place each time, so it does not count\nIt also reports crashed_rounds, total_rounds and distinct_crashes for this input. The field is absent when the input did not fault.",
			"inputSchema": map[string]any{
				"type": "object",
				"properties": map[string]any{
					"path": map[string]any{"type": "string", "description": "Path to the candidate input file to run. Must be under the workspace (write it there first with write_file)."},
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
