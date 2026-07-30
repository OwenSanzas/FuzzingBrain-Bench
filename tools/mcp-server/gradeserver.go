package main

// Client-side proxy for run_poc_on_harness. Called from toolGrade: POSTs the
// candidate bytes to the remote grading oracle and returns its (sealed) result.
// The challenge image holds NO answer key and NO grading logic — that lives only
// in the private grading backend behind BENCH_GRADE_URL.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"
)

func (s *server) gradeRemote(abs string) (any, error) {
	data, err := os.ReadFile(abs)
	if err != nil {
		return nil, fmt.Errorf("read candidate: %w", err)
	}
	if s.bugID == "" {
		return nil, fmt.Errorf("BENCH_BUG_ID must be set for remote grading")
	}
	// The oracle's grading endpoint. The alias is a path segment, so escape it
	// rather than interpolating raw. (The agent never sees this URL — it only ever
	// calls the tool by name, run_poc_on_harness.)
	endpoint := s.gradeURL + "/v1/challenges/" + url.PathEscape(s.bugID) + "/grade"
	req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(data))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/octet-stream")
	// ngrok free/dev domains show an HTML interstitial to browser-like requests;
	// this header skips it so the JSON always comes back clean.
	req.Header.Set("ngrok-skip-browser-warning", "true")
	cl := &http.Client{Timeout: 600 * time.Second}
	resp, err := cl.Do(req)
	if err != nil {
		return nil, fmt.Errorf("remote grade: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("remote grade status %d: %s", resp.StatusCode, string(trunc(string(body), 300)))
	}
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, fmt.Errorf("remote grade decode: %w", err)
	}
	// Seal the verdict from the agent: return only harness_output + duration_ms
	// unless the TRUSTED runner explicitly asked to reveal it (BENCH_GRADE_REVEAL=1).
	if os.Getenv("BENCH_GRADE_REVEAL") != "1" {
		sealed := map[string]any{}
		if ho, ok := out["harness_output"]; ok {
			sealed["harness_output"] = ho
		}
		if d, ok := out["duration_ms"]; ok {
			sealed["duration_ms"] = d
		}
		return sealed, nil
	}
	return out, nil
}

func trunc(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
