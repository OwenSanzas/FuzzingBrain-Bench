package main

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

// reachFired runs the PoC against the coverage build and uses `llvm-cov
// export` to check whether any executed line lives inside the expected
// function/line-range region.
//
// Fallback chain when llvm-cov isn't available: try `gdb` to set a
// breakpoint at <file>:<line_range_mid> and check whether the PoC hits it.
// Per the user's hint ("如果llvm-cov不行，可以试试gdb"). Implementation of
// gdb fallback is deferred to a future revision; for now we surface the
// llvm-cov result and fall back to "not_fired" if it fails.
func reachFired(covBin string, invocation []string, pocPath, runDir string, expected *expectedYAML) bool {
	if expected.Reach.ExpectedFunction == "" {
		return false
	}
	if _, err := os.Stat(covBin); err != nil {
		return false
	}
	profraw := filepath.Join(runDir, "default.profraw")
	profdata := filepath.Join(runDir, "default.profdata")
	// Run coverage build with profile env.
	args := []string{}
	for _, a := range invocation {
		if a == "@@" {
			args = append(args, pocPath)
		} else {
			args = append(args, a)
		}
	}
	cmd := exec.Command(covBin, args...)
	cmd.Dir = runDir
	cmd.Env = append(os.Environ(),
		"LLVM_PROFILE_FILE="+profraw,
		"ASAN_OPTIONS=abort_on_error=0:detect_leaks=0",
		"TMPDIR="+runDir,
	)
	_ = cmd.Run()

	if _, err := os.Stat(profraw); err != nil {
		return false
	}
	merge := exec.Command("llvm-profdata", "merge", "-sparse", profraw, "-o", profdata)
	if err := merge.Run(); err != nil {
		// Try llvm-profdata-14 fallback.
		merge = exec.Command("llvm-profdata-14", "merge", "-sparse", profraw, "-o", profdata)
		if err := merge.Run(); err != nil {
			return false
		}
	}
	export := exec.Command("llvm-cov", "export", "--format=text", "-instr-profile", profdata, covBin)
	out, err := export.Output()
	if err != nil {
		export = exec.Command("llvm-cov-14", "export", "--format=text", "-instr-profile", profdata, covBin)
		out, err = export.Output()
		if err != nil {
			return false
		}
	}
	return llvmCovHit(out, expected)
}

// reachFromBacktrace is the fallback when the coverage build crashes
// before its profile is flushed. The sanitizer backtrace lists every
// function on the call stack at crash time; any frame whose file/line
// lies inside the buggy region proves execution.
func reachFromBacktrace(stderr string, expected *expectedYAML) bool {
	if expected.Reach.ExpectedFile == "" && expected.Reach.ExpectedFunction == "" {
		return false
	}
	lo, hi := 0, 0
	if len(expected.Reach.ExpectedLineRange) == 2 {
		lo = expected.Reach.ExpectedLineRange[0]
		hi = expected.Reach.ExpectedLineRange[1]
	}
	for _, m := range frameRe.FindAllStringSubmatch(stderr, -1) {
		file := m[2]
		if isHarnessFrame(file) {
			continue
		}
		if expected.Reach.ExpectedFile != "" && !suffixMatch(file, expected.Reach.ExpectedFile) {
			continue
		}
		line, err := strconv.Atoi(m[3])
		if err != nil {
			continue
		}
		if lo > 0 && hi > 0 {
			if line >= lo && line <= hi {
				return true
			}
			continue
		}
		return true
	}
	// Java fallback: walk Java frames.
	for _, m := range javaFrameRe.FindAllStringSubmatch(stderr, -1) {
		file := m[1]
		if isJavaHarnessFrame(file) {
			continue
		}
		if expected.Reach.ExpectedFile != "" && !javaSuffixMatch(file, expected.Reach.ExpectedFile) {
			continue
		}
		line, err := strconv.Atoi(m[2])
		if err != nil {
			continue
		}
		if lo > 0 && hi > 0 {
			if line >= lo && line <= hi {
				return true
			}
			continue
		}
		return true
	}
	return false
}

// Minimal llvm-cov export schema: top-level data[].files[].segments[][line,col,count,hasCount,isRegionEntry].
// We only need: for each file whose suffix matches expected_file, walk
// segments and check whether any executed segment line lies in the buggy
// region.
type llvmCovExport struct {
	Data []struct {
		Files []struct {
			Filename string          `json:"filename"`
			Segments [][]json.Number `json:"segments"`
		} `json:"files"`
	} `json:"data"`
}

func llvmCovHit(raw []byte, expected *expectedYAML) bool {
	var doc llvmCovExport
	if err := json.Unmarshal(raw, &doc); err != nil {
		return false
	}
	lo, hi := 0, 0
	if len(expected.Reach.ExpectedLineRange) == 2 {
		lo = expected.Reach.ExpectedLineRange[0]
		hi = expected.Reach.ExpectedLineRange[1]
	}
	for _, d := range doc.Data {
		for _, f := range d.Files {
			if !suffixMatch(f.Filename, expected.Reach.ExpectedFile) {
				continue
			}
			for _, seg := range f.Segments {
				if len(seg) < 4 {
					continue
				}
				line, err := seg[0].Int64()
				if err != nil {
					continue
				}
				count, err := seg[2].Int64()
				if err != nil {
					continue
				}
				if count == 0 {
					continue
				}
				if lo == 0 && hi == 0 {
					return true
				}
				if int(line) >= lo && int(line) <= hi {
					return true
				}
			}
		}
	}
	return false
}
