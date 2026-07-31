package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type listDirParams struct {
	Path string `json:"path"`
}

type readFileParams struct {
	Path   string `json:"path"`
	Offset int64  `json:"offset,omitempty"`
	Limit  int    `json:"limit,omitempty"`
}

type writeFileParams struct {
	Path    string `json:"path"`
	Content string `json:"content"`
}

const (
	defaultReadLines = 2000       // max lines returned when `limit` is unset (Claude Code parity)
	maxLineChars     = 2000       // per-line char cap; longer lines are truncated with a marker
	maxReadBytes     = 128 * 1024 // total read_file output cap; lines x per-line could else hit ~4MB
	maxDirEntries    = 1000       // list_directory entry cap; a huge dir would blow the context
)

var errPermissionDenied = errors.New("permission denied")

// resolveAllowed resolves p (absolute or relative to BENCH_BUG_DIR) to an
// absolute path and confirms it lives under either BENCH_BUG_DIR or
// BENCH_WORKSPACE. Symlinks are not followed for existence checks (so
// list_directory can still report denied entries by name); callers do their
// own existence handling.
func (s *server) resolveAllowed(p string) (string, error) {
	if p == "" {
		return "", fmt.Errorf("path required")
	}
	if !filepath.IsAbs(p) {
		p = filepath.Join(s.bugDir, p)
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return "", err
	}
	abs = filepath.Clean(abs)
	if !under(abs, s.bugDir) && !under(abs, s.workspace) {
		return "", errPermissionDenied
	}
	return abs, nil
}

func under(p, root string) bool {
	rel, err := filepath.Rel(root, p)
	if err != nil {
		return false
	}
	return !strings.HasPrefix(rel, "..")
}

// isDeniedRead returns true when p (already resolved under bugDir/workspace)
// matches a deny-listed path: oracle answer keys, the reference PoC, and
// grader-run state. The whole grader/ and poc/ subtrees are denied (not just
// named files) so a renamed or future oracle artifact can't slip through.
func (s *server) isDeniedRead(abs string) bool {
	rel, err := filepath.Rel(s.bugDir, abs)
	if err == nil && !strings.HasPrefix(rel, "..") {
		top := rel
		if i := strings.IndexByte(rel, os.PathSeparator); i >= 0 {
			top = rel[:i]
		}
		if top == "grader" || top == "poc" {
			return true
		}
	}
	relW, err := filepath.Rel(s.workspace, abs)
	if err == nil && !strings.HasPrefix(relW, "..") {
		if strings.HasPrefix(relW, "grader-run"+string(os.PathSeparator)) || relW == "grader-run" {
			return true
		}
	}
	return false
}

func (s *server) toolListDirectory(args []byte) (any, error) {
	var p listDirParams
	if err := json.Unmarshal(args, &p); err != nil {
		return nil, err
	}
	abs, err := s.resolveAllowed(p.Path)
	if err != nil {
		return nil, err
	}
	entries, err := os.ReadDir(abs)
	if err != nil {
		return nil, fmt.Errorf("read dir: %w", err)
	}
	// Cap the number of entries so a huge directory can't blow the agent's
	// context. os.ReadDir returns entries sorted by name, so the cap is stable.
	total := len(entries)
	truncated := total > maxDirEntries
	if truncated {
		entries = entries[:maxDirEntries]
	}
	out := make([]map[string]any, 0, len(entries))
	for _, e := range entries {
		info, _ := e.Info()
		typ := "file"
		switch {
		case e.IsDir():
			typ = "dir"
		case info != nil && info.Mode()&os.ModeSymlink != 0:
			typ = "symlink"
		}
		size := int64(0)
		if info != nil {
			size = info.Size()
		}
		out = append(out, map[string]any{
			"name": e.Name(),
			"type": typ,
			"size": size,
		})
	}
	return map[string]any{
		"path":          abs,
		"entries":       out,
		"total_entries": total,
		"truncated":     truncated,
	}, nil
}

func (s *server) toolReadFile(args []byte) (any, error) {
	var p readFileParams
	if err := json.Unmarshal(args, &p); err != nil {
		return nil, err
	}
	abs, err := s.resolveAllowed(p.Path)
	if err != nil {
		return nil, err
	}
	if s.isDeniedRead(abs) {
		return nil, fmt.Errorf("permission denied: %s is on the oracle deny list", p.Path)
	}
	st, err := os.Stat(abs)
	if err != nil {
		return nil, fmt.Errorf("stat: %w", err)
	}
	if st.IsDir() {
		return nil, fmt.Errorf("is a directory")
	}
	data, err := os.ReadFile(abs)
	if err != nil {
		return nil, err
	}
	// Line-based, cat -n style — the Claude Code Read contract. LLMs reason in
	// lines, not bytes, and line numbers give stable references for later edits.
	lines := strings.Split(string(data), "\n")
	// A trailing newline yields a final empty element; drop it so total_lines is honest.
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	total := len(lines)

	start := int(p.Offset) // 1-based start line
	if start <= 0 {
		start = 1
	}
	limit := p.Limit
	if limit <= 0 {
		limit = defaultReadLines
	}
	end := start - 1 + limit
	if end > total {
		end = total
	}
	var b strings.Builder
	shown := 0
	i := start - 1
	for ; i >= 0 && i < end; i++ {
		ln := lines[i]
		if len(ln) > maxLineChars {
			ln = ln[:maxLineChars] + "... [line truncated]"
		}
		fmt.Fprintf(&b, "%6d\t%s\n", i+1, ln)
		shown++
		// Total-output cap: 2000 lines x 2000 chars could otherwise reach ~4MB and
		// blow the agent's context in a single call. Stop at the byte cap; the agent
		// continues from next_offset via the offset/limit params.
		if b.Len() >= maxReadBytes {
			i++
			break
		}
	}
	// i is one past the last line included.
	res := map[string]any{
		"content":     b.String(),
		"total_lines": total,
		"lines_shown": shown,
		"truncated":   i < total,
	}
	if i < total {
		res["next_offset"] = i + 1 // 1-based line to resume from
	}
	return res, nil
}

func (s *server) toolWriteFile(args []byte) (any, error) {
	var p writeFileParams
	if err := json.Unmarshal(args, &p); err != nil {
		return nil, err
	}
	abs, err := s.resolveAllowed(p.Path)
	if err != nil {
		return nil, err
	}
	if !under(abs, s.workspace) {
		return nil, fmt.Errorf("write_file restricted to BENCH_WORKSPACE")
	}
	if err := os.MkdirAll(filepath.Dir(abs), 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(abs, []byte(p.Content), 0o644); err != nil {
		return nil, err
	}
	// When exec() runs unprivileged, hand ownership of written files to the
	// agent uid so its shell can read/modify what write_file produced.
	if s.dropPrivs {
		if err := os.Chown(abs, int(s.agentUID), int(s.agentGID)); err != nil {
			return nil, fmt.Errorf("chown written file: %w", err)
		}
	}
	return map[string]any{"bytes_written": len(p.Content)}, nil
}
