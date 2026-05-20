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

const defaultReadLimit = 65536

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
	return map[string]any{"path": abs, "entries": out}, nil
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
		return nil, fmt.Errorf("permission denied: %s is on the oracle deny list (see SPEC §4.4)", p.Path)
	}
	st, err := os.Stat(abs)
	if err != nil {
		return nil, fmt.Errorf("stat: %w", err)
	}
	if st.IsDir() {
		return nil, fmt.Errorf("is a directory")
	}
	limit := p.Limit
	if limit <= 0 {
		limit = defaultReadLimit
	}
	f, err := os.Open(abs)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	if p.Offset > 0 {
		if _, err := f.Seek(p.Offset, 0); err != nil {
			return nil, err
		}
	}
	buf := make([]byte, limit)
	n, _ := f.Read(buf)
	return map[string]any{
		"content":     string(buf[:n]),
		"total_bytes": st.Size(),
		"truncated":   int64(n)+p.Offset < st.Size(),
	}, nil
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
