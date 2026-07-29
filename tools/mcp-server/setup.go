package main

import (
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

type benchYAML struct {
	BugID          string `yaml:"bug_id"`
	Project        string `yaml:"project"`
	Title          string `yaml:"title"`
	UpstreamReport string `yaml:"upstream_report"`
	Target         struct {
		Language    string `yaml:"language"`
		BuildSystem string `yaml:"build_system"`
	} `yaml:"target"`
	Harness struct {
		Type       string   `yaml:"type"`
		Entrypoint string   `yaml:"entrypoint"`
		Invocation []string `yaml:"invocation"`
		Sanitizer  string   `yaml:"sanitizer"`
		RSSLimitMB int      `yaml:"rss_limit_mb"`
		TimeoutS   int      `yaml:"timeout_s"`
		Provenance string   `yaml:"provenance"`
	} `yaml:"harness"`
	Reproducibility struct {
		BaseImageDigest    string `yaml:"base_image_digest"`
		SnapshotDebianDate string `yaml:"snapshot_debian_date"`
		SourceDateEpoch    int64  `yaml:"source_date_epoch"`
	} `yaml:"reproducibility"`
	Status    string `yaml:"status"`
	CVE       string `yaml:"cve"`
	Disclosed string `yaml:"disclosed"`
	Notes     string `yaml:"notes"`
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
		"language": bench.Target.Language,
		"harness": map[string]any{
			"type":       bench.Harness.Type,
			"entrypoint": bench.Harness.Entrypoint,
			"invocation": bench.Harness.Invocation,
			"sanitizer":  bench.Harness.Sanitizer,
		},
		"workspace_path": s.workspace,
		"bug_dir":        s.bugDir,
		"notes":          bench.Notes,
	}, nil
}
