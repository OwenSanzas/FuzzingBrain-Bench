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

// toolGrade is the agent-facing run_poc_on_harness tool. The challenge image is
// answer-free: it holds no grading logic and no answer key. The candidate is
// shipped to the remote oracle (BENCH_GRADE_URL), which judges it. There is no
// local grading here — the grading engine lives only in the private backend.
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
	if s.gradeURL == "" {
		return nil, fmt.Errorf("run_poc_on_harness requires a remote grader (BENCH_GRADE_URL is not set)")
	}
	return s.gradeRemote(abs)
}
