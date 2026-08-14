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
// One way to answer it: the image bakes the sanitizer harness, so the candidate
// is run right here and scored on distinct crashes. Nothing leaves the machine
// and no service has to be up. Still answer-free — the image carries no
// expected.yaml, no fixed build, nothing naming the defect.
//
// There is no second way. Grading used to be able to fall back to a grading
// service over the network, which is why an image that could not find its own
// harness still appeared to work: it quietly reached out instead, and the run
// depended on a host being up that nothing in the run mentioned. An image
// without a baked harness is now an error, said once and loudly.
//
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
	if s.localHarness() == "" {
		return nil, fmt.Errorf("run_poc_on_harness needs the sanitizer harness baked "+
			"into the image, and none is present under %s", s.oracleDir)
	}
	return s.gradeLocal(abs)
}
