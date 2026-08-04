package main

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
)

var errPermissionDenied = errors.New("permission denied")

// resolveAllowed resolves p (absolute or relative to BENCH_BUG_DIR) to an
// absolute path and confirms it lives under either BENCH_BUG_DIR or
// BENCH_WORKSPACE. Used by run_poc_on_harness to bound the candidate path.
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
