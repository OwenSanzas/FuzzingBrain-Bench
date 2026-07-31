package main

import (
	"encoding/json"
	"fmt"
	"os"
)

type gradeParams struct {
	Path    string `json:"path"`
	Options struct {
		RoundCount int `json:"round_count,omitempty"`
	} `json:"options,omitempty"`
}

// toolGrade is the agent-facing run_poc_on_harness tool.
//
// Two ways to answer it, in preference order:
//
//   - LOCAL (self-contained image). The image bakes the sanitizer harness, so
//     the candidate is run right here and scored on distinct crashes. Nothing
//     leaves the machine and no service has to be up. Still answer-free: the
//     image carries no expected.yaml, no fixed build, nothing naming the defect.
//   - REMOTE (legacy image). No baked harness, so the candidate goes to the
//     grading oracle at BENCH_GRADE_URL, which owns the answer key and judges
//     the five-rung capability ladder.
//
// The order matters for the images already published: they have no harness
// baked in, so they keep taking the remote path unchanged.
func (s *server) toolGrade(args []byte) (any, error) {
	var p gradeParams
	if err := json.Unmarshal(args, &p); err != nil {
		return nil, err
	}
	abs, err := s.resolveAllowed(p.Path)
	if err != nil {
		return nil, err
	}
	if !under(abs, s.workspace) {
		return nil, fmt.Errorf("grade target must live under BENCH_WORKSPACE")
	}
	if st, err := os.Stat(abs); err != nil || st.IsDir() {
		return nil, fmt.Errorf("grade target not found or is a directory: %s", p.Path)
	}
	if s.localHarness() != "" {
		return s.gradeLocal(abs)
	}
	if s.gradeURL == "" {
		return nil, fmt.Errorf("run_poc_on_harness needs either a harness baked into the image " +
			"(BENCH_ORACLE_DIR) or a remote grader (BENCH_GRADE_URL); neither is present")
	}
	return s.gradeRemote(abs)
}
