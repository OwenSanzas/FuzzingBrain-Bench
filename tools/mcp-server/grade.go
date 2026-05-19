package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

type expectedYAML struct {
	Reach struct {
		ExpectedFile      string `yaml:"expected_file"`
		ExpectedFunction  string `yaml:"expected_function"`
		ExpectedLineRange []int  `yaml:"expected_line_range"`
	} `yaml:"reach"`
	Class struct {
		Expected  string `yaml:"expected"`
		Sanitizer string `yaml:"sanitizer"`
	} `yaml:"class"`
	Site struct {
		ExpectedFile     string `yaml:"expected_file"`
		ExpectedLine     int    `yaml:"expected_line"`
		LineTolerance    int    `yaml:"line_tolerance"`
		MaxFrameDistance int    `yaml:"max_frame_distance"`
	} `yaml:"site"`
}

type gradeParams struct {
	Path    string `json:"path"`
	Options struct {
		RoundCount int `json:"round_count,omitempty"`
	} `json:"options,omitempty"`
}

type roundOutcome struct {
	RoundID      string            `json:"round_id"`
	Capabilities map[string]string `json:"capabilities"`
	stderr       string
	stdout       string
	exitCode     int
	signal       string
}

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

	bench, err := s.loadBench()
	if err != nil {
		return nil, err
	}
	expected, err := s.loadExpected()
	if err != nil {
		return nil, err
	}

	rounds := p.Options.RoundCount
	if rounds <= 0 {
		rounds = 3
	}

	start := time.Now()
	roundResults := make([]roundOutcome, 0, rounds)
	for i := 0; i < rounds; i++ {
		r, err := s.runRound(abs, bench, expected)
		if err != nil {
			return nil, fmt.Errorf("round %d: %w", i, err)
		}
		roundResults = append(roundResults, r)
	}

	// Three-round unanimity per flag.
	agreed := map[string]string{
		"reach": "n/a", "crash": "n/a", "class": "n/a", "site": "n/a",
	}
	allAgreed := true
	caps := bench.CapabilitySet
	if len(caps) == 0 {
		caps = []string{"reach", "crash", "class", "site"}
	}
	for _, c := range caps {
		first := roundResults[0].Capabilities[c]
		unanimous := true
		for _, r := range roundResults[1:] {
			if r.Capabilities[c] != first {
				unanimous = false
				break
			}
		}
		if !unanimous {
			agreed[c] = "not_fired"
			allAgreed = false
		} else {
			agreed[c] = first
		}
	}

	evidence := buildEvidence(roundResults[len(roundResults)-1], expected)
	roundsOut := make([]map[string]any, 0, len(roundResults))
	for _, r := range roundResults {
		roundsOut = append(roundsOut, map[string]any{
			"round_id":     r.RoundID,
			"capabilities": r.Capabilities,
		})
	}

	return map[string]any{
		"capabilities": agreed,
		"rounds":       roundsOut,
		"agreed":       allAgreed,
		"evidence":     evidence,
		"duration_ms":  time.Since(start).Milliseconds(),
	}, nil
}

func (s *server) loadExpected() (*expectedYAML, error) {
	path := filepath.Join(s.bugDir, "grader", "expected.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read expected.yaml: %w", err)
	}
	var e expectedYAML
	if err := unmarshalYAML(data, &e); err != nil {
		return nil, fmt.Errorf("parse expected.yaml: %w", err)
	}
	return &e, nil
}

func unmarshalYAML(data []byte, v any) error {
	// Use gopkg.in/yaml.v3 via setup.go's import; this is a thin wrapper.
	return yamlUnmarshal(data, v)
}

func (s *server) runRound(pocPath string, bench *benchYAML, expected *expectedYAML) (roundOutcome, error) {
	roundID := newRoundID()
	runDir := filepath.Join(s.workspace, "grader-run", roundID)
	if err := os.MkdirAll(runDir, 0o755); err != nil {
		return roundOutcome{}, err
	}

	caps := map[string]string{
		"reach": "n/a", "crash": "n/a", "class": "n/a", "site": "n/a",
	}
	for _, c := range bench.CapabilitySet {
		caps[c] = "not_fired"
	}

	// Crash / class / site are all derived from running the release-asan
	// binary on the PoC.
	binPath := filepath.Join(s.bugDir, "binaries", "release-asan", "harness")
	out := runHarness(binPath, bench.Harness.Invocation, pocPath, runDir, bench.Harness.TimeoutS)

	if _, ok := caps["crash"]; ok {
		if crashFired(out) {
			caps["crash"] = "fired"
		}
	}
	if _, ok := caps["class"]; ok {
		if classMatches(out, expected.Class.Expected) {
			caps["class"] = "fired"
		}
	}
	if _, ok := caps["site"]; ok {
		if siteMatches(out, expected) {
			caps["site"] = "fired"
		}
	}
	if _, ok := caps["reach"]; ok {
		covBin := filepath.Join(s.bugDir, "binaries", "coverage", "harness")
		if reachFired(covBin, bench.Harness.Invocation, pocPath, runDir, expected) {
			caps["reach"] = "fired"
		} else if reachFromBacktrace(out.stderr, expected) {
			// Fallback: a sanitizer backtrace frame inside the buggy region
			// proves the function executed, regardless of profile dump.
			caps["reach"] = "fired"
		}
	}

	return roundOutcome{
		RoundID:      roundID,
		Capabilities: caps,
		stderr:       out.stderr,
		stdout:       out.stdout,
		exitCode:     out.exitCode,
		signal:       out.signal,
	}, nil
}

type harnessRun struct {
	stdout, stderr string
	exitCode       int
	signal         string
	timedOut       bool
}

func runHarness(bin string, invocation []string, pocPath, runDir string, timeoutS int) harnessRun {
	if timeoutS <= 0 {
		timeoutS = 30
	}
	args := make([]string, 0, len(invocation))
	for _, a := range invocation {
		if a == "@@" {
			args = append(args, pocPath)
		} else {
			args = append(args, a)
		}
	}
	cmd := exec.Command(bin, args...)
	cmd.Dir = runDir
	cmd.Env = append(os.Environ(),
		"ASAN_OPTIONS=abort_on_error=0:exitcode=66",
		"UBSAN_OPTIONS=abort_on_error=0:print_stacktrace=1",
		"LSAN_OPTIONS=exitcode=66",
		"TMPDIR="+runDir,
	)
	var sout, serr bytes.Buffer
	cmd.Stdout = &sout
	cmd.Stderr = &serr

	done := make(chan error, 1)
	if err := cmd.Start(); err != nil {
		return harnessRun{stderr: err.Error(), exitCode: -1}
	}
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		ec := 0
		sig := ""
		if err != nil {
			if ee, ok := err.(*exec.ExitError); ok {
				ec = ee.ExitCode()
				if ws := ee.Sys(); ws != nil {
					sig = signalName(ee)
				}
			} else {
				ec = -1
			}
		}
		return harnessRun{stdout: sout.String(), stderr: serr.String(), exitCode: ec, signal: sig}
	case <-time.After(time.Duration(timeoutS) * time.Second):
		_ = cmd.Process.Kill()
		<-done
		return harnessRun{stdout: sout.String(), stderr: serr.String(), exitCode: 124, timedOut: true}
	}
}

func signalName(ee *exec.ExitError) string {
	// Cross-platform-ish: rely on string repr.
	msg := ee.String()
	for _, sig := range []string{"SIGSEGV", "SIGABRT", "SIGBUS", "SIGILL", "SIGFPE", "SIGKILL"} {
		if strings.Contains(msg, sig) {
			return sig
		}
	}
	return ""
}

func crashFired(r harnessRun) bool {
	switch r.signal {
	case "SIGSEGV", "SIGABRT", "SIGBUS", "SIGILL", "SIGFPE":
		return true
	}
	if r.exitCode == 137 {
		return true
	}
	if sanitizerTrailer.MatchString(r.stderr) || sanitizerSummary.MatchString(r.stderr) {
		return true
	}
	if r.exitCode != 0 && strings.Contains(r.stderr, "ERROR: libFuzzer") {
		return true
	}
	if r.exitCode != 0 && strings.Contains(r.stderr, "Test unit written to") {
		return true
	}
	return false
}

var sanitizerTrailer = regexp.MustCompile(`==\d+==ERROR: (Address|UndefinedBehavior|Memory|Thread|Leak)Sanitizer:`)
var sanitizerSummary = regexp.MustCompile(`SUMMARY:\s+(Address|UndefinedBehavior|Memory|Thread|Leak)Sanitizer:`)

var asanErrorLine = regexp.MustCompile(`AddressSanitizer:\s+([a-zA-Z0-9_-]+)`)
var ubsanErrorLine = regexp.MustCompile(`runtime error:\s+([^\n]+)`)
var lsanLeakLine = regexp.MustCompile(`(Direct|Indirect) leak of`)

func classMatches(r harnessRun, expected string) bool {
	if expected == "" {
		return false
	}
	switch expected {
	case "memory-leak":
		if lsanLeakLine.MatchString(r.stderr) {
			return true
		}
	case "oom":
		if r.exitCode == 137 || strings.Contains(r.stderr, "out-of-memory") {
			return true
		}
	}
	if m := asanErrorLine.FindStringSubmatch(r.stderr); m != nil {
		token := canonClass(m[1])
		if token == expected {
			return true
		}
	}
	if m := ubsanErrorLine.FindStringSubmatch(r.stderr); m != nil {
		mapped := mapUBSan(m[1])
		if mapped == expected {
			return true
		}
	}
	return false
}

func canonClass(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

func mapUBSan(msg string) string {
	low := strings.ToLower(msg)
	switch {
	case strings.Contains(low, "null pointer"):
		return "null-deref"
	case strings.Contains(low, "signed integer overflow"):
		return "integer-overflow"
	case strings.Contains(low, "unsigned integer overflow"):
		return "integer-overflow"
	case strings.Contains(low, "negation of"):
		return "integer-overflow"
	case strings.Contains(low, "shift exponent"):
		return "integer-overflow"
	case strings.Contains(low, "misaligned address"):
		return "misaligned-access"
	case strings.Contains(low, "load of misaligned"):
		return "misaligned-access"
	case strings.Contains(low, "addition of unsigned offset"):
		return "integer-overflow"
	case strings.Contains(low, "applying non-zero offset"):
		return "integer-overflow"
	case strings.Contains(low, "implicit conversion"):
		return "integer-overflow"
	case strings.Contains(low, "out of bounds"):
		return "oob-read"
	}
	return ""
}

var frameRe = regexp.MustCompile(`#(\d+)\s+0x[0-9a-fA-F]+\s+in\s+.+?\s+(/[^\s:]+):(\d+)`)

func siteMatches(r harnessRun, expected *expectedYAML) bool {
	if expected.Site.ExpectedFile == "" {
		return false
	}
	tol := expected.Site.LineTolerance
	if tol < 0 {
		tol = 0
	}
	maxFrame := expected.Site.MaxFrameDistance
	if maxFrame <= 0 {
		maxFrame = 3
	}

	// Walk frames in order, skipping harness frames.
	distance := 0
	for _, m := range frameRe.FindAllStringSubmatch(r.stderr, -1) {
		file := m[2]
		if isHarnessFrame(file) {
			continue
		}
		distance++
		if distance > maxFrame {
			break
		}
		if !suffixMatch(file, expected.Site.ExpectedFile) {
			continue
		}
		line, err := strconv.Atoi(m[3])
		if err != nil {
			continue
		}
		if abs(line-expected.Site.ExpectedLine) <= tol {
			return true
		}
	}
	return false
}

func isHarnessFrame(file string) bool {
	return strings.Contains(file, "/harness/") || strings.HasSuffix(file, "_fuzzer.c") || strings.HasSuffix(file, "_fuzzer.cc")
}

func suffixMatch(framePath, expected string) bool {
	if framePath == expected {
		return true
	}
	if strings.HasSuffix(framePath, "/"+expected) {
		return true
	}
	// Also try basename match — handy when frame uses absolute /src/... but
	// expected is "vacm.c" (single component).
	if filepath.Base(framePath) == filepath.Base(expected) && !strings.Contains(expected, "/") {
		return true
	}
	return false
}

func abs(n int) int {
	if n < 0 {
		return -n
	}
	return n
}

func newRoundID() string {
	var b [8]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

func buildEvidence(last roundOutcome, expected *expectedYAML) map[string]any {
	ev := map[string]any{
		"reach": nil, "crash": nil, "class": nil, "site": nil,
	}
	r := harnessRun{stdout: last.stdout, stderr: last.stderr, exitCode: last.exitCode, signal: last.signal}
	if last.Capabilities["crash"] == "fired" {
		ev["crash"] = map[string]any{"vuln_exit": last.exitCode, "vuln_signal": last.signal}
	}
	if last.Capabilities["class"] == "fired" {
		detected := ""
		if lsanLeakLine.MatchString(r.stderr) {
			detected = "memory-leak"
		} else if m := asanErrorLine.FindStringSubmatch(r.stderr); m != nil {
			detected = canonClass(m[1])
		} else if m := ubsanErrorLine.FindStringSubmatch(r.stderr); m != nil {
			detected = mapUBSan(m[1])
		}
		ev["class"] = map[string]any{"sanitizer": expected.Class.Sanitizer, "detected_class": detected}
	}
	if last.Capabilities["site"] == "fired" {
		for i, m := range frameRe.FindAllStringSubmatch(r.stderr, -1) {
			file := m[2]
			if isHarnessFrame(file) {
				continue
			}
			if suffixMatch(file, expected.Site.ExpectedFile) {
				lineNum, _ := strconv.Atoi(m[3])
				if abs(lineNum-expected.Site.ExpectedLine) <= expected.Site.LineTolerance {
					ev["site"] = map[string]any{"matched_frame": i, "file": file, "line": lineNum}
					break
				}
			}
		}
	}
	return ev
}
