package main

import (
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

// benchYAML mirrors bench.yaml exactly: five public fields, answer-free by
// construction.
//
// Everything the older shape carried — the upstream repo and vulnerable commit,
// the fix commit/patch, a descriptive title, notes, build reproducibility pins —
// now lives only in the private vuln.yaml. None of that is missing by accident:
// an image that shipped the upstream pin or the title would name the very bug it
// is meant to hide.
type benchYAML struct {
	BugID     string `yaml:"bug_id"`  // the neutral <project>-NN alias
	Project   string `yaml:"project"` // the oss-fuzz project name
	IsOSSFuzz bool   `yaml:"is_oss_fuzz"`
	Language  string `yaml:"language"` // c | cpp | jvm
	Harness   struct {
		Engine     string   `yaml:"engine"`     // libfuzzer | jazzer | afl
		Sanitizer  string   `yaml:"sanitizer"`  // asan | ubsan | lsan | jazzer | libfuzzer | none
		Invocation []string `yaml:"invocation"` // "@@" is the input placeholder
	} `yaml:"harness"`
}

// entrypoint is the symbol the engine hands the input bytes to. It follows from
// the engine, not from the bug, so it is derived here instead of being spelled
// out per challenge the way bench.yaml used to.
func (b *benchYAML) entrypoint() string {
	switch b.Harness.Engine {
	case "jazzer":
		return "fuzzerTestOneInput"
	case "libfuzzer", "afl":
		return "LLVMFuzzerTestOneInput"
	default:
		return ""
	}
}

func (s *server) loadBench() (*benchYAML, error) {
	path := filepath.Join(s.bugDir, "bench.yaml")
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read bench.yaml: %w", err)
	}
	var b benchYAML
	if err := yaml.Unmarshal(data, &b); err != nil {
		return nil, fmt.Errorf("parse bench.yaml: %w", err)
	}
	return &b, nil
}

func (s *server) toolSetup(_ []byte) (any, error) {
	bench, err := s.loadBench()
	if err != nil {
		return nil, err
	}
	// Task info only — the environment, the target project, and the harness
	// configuration. Deliberately answer-free: no bug id, no description, no
	// class/capability hints (blind). The sanitizer IS part of the harness config
	// (a real auditor always knows their instrumentation) and comes from the
	// PUBLIC bench.yaml harness.sanitizer — never from the answer-side expected.yaml.
	return map[string]any{
		"project":  bench.Project,
		"language": bench.Language,
		"harness": map[string]any{
			"engine":     bench.Harness.Engine,
			"entrypoint": bench.entrypoint(),
			"invocation": bench.Harness.Invocation,
			"sanitizer":  bench.Harness.Sanitizer,
		},
		"workspace_path": s.workspace,
		"bug_dir":        s.bugDir,
	}, nil
}
