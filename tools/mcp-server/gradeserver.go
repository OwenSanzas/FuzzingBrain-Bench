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
	"strconv"
	"time"
)

// How a submission is retried when the network, rather than the candidate,
// is what failed.
//
// Measured on a 68-challenge sweep run with no concurrency at all: 2242
// submissions, 23 gateway errors. The oracle's own logs account for every
// request it ever saw, and 18 of those 23 are not among them -- the request
// never arrived. They were also instant: the edge refused in milliseconds
// rather than timing out. Nothing about them clusters, in time or by challenge,
// so they are isolated blips rather than an outage.
//
// That shape argues for few attempts and short waits. A blip is over in
// seconds; a real outage will not clear in a couple of minutes either, so
// paying for one buys nothing. Guessing too short merely returns today's
// behaviour, which is the submission being lost; guessing too long spends an
// episode's clock on a request that was never going to succeed. The downside is
// bounded on one side and not on the other.
//
// Two of the 23 kinds are NOT fixed by any of this: five requests reached the
// oracle, were graded, and lost only their response. Retrying those grades the
// same candidate twice, and the second grade reports `duplicate` for a crash the
// agent had in fact just found for the first time -- a wrong signal rather than
// a missing one. Fixing that needs an idempotency key the oracle can replay
// from, which is a change on both sides and is not attempted here.
var gradeRetryWaits = []time.Duration{2 * time.Second, 8 * time.Second}

const (
	gradeRequestTimeout = 600 * time.Second // one attempt; a real grade can take minutes
	gradeRetryBudget    = 900 * time.Second // all attempts together
)

func (s *server) gradeRemote(abs string, turn int) (any, error) {
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

	// A fresh request per attempt, deliberately. A *bytes.Reader carries a cursor
	// that Do() reads to the end, and a Request is not reusable after Do() closes
	// its body -- so a retry that reuses either sends an EMPTY body. That failure
	// is silent: the oracle accepts a zero-byte candidate as a real input, runs
	// the harness on nothing, and reports a clean run. Losing a submission is
	// better than answering it with a fabricated result, so the construction
	// lives inside the loop where it cannot be shared by accident.
	newReq := func() (*http.Request, error) {
		req, err := http.NewRequest(http.MethodPost, endpoint, bytes.NewReader(data))
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", "application/octet-stream")
		// ngrok free/dev domains show an HTML interstitial to browser-like requests;
		// this header skips it so the JSON always comes back clean.
		req.Header.Set("ngrok-skip-browser-warning", "true")
		// Which turn of the episode this submission came from. It arrives through
		// the call's _meta, which the runner attaches after the model is done, so it
		// is outside the tool schema and the agent can neither read nor forge it.
		if turn > 0 {
			req.Header.Set("FB-Turn", strconv.Itoa(turn))
		}
		// Which episode this submission belongs to, forwarded from the runner's
		// environment. The oracle needs it to tell "one run submitted twenty inputs"
		// from "twenty runs each submitted one" — without it every submission looks
		// like a separate attempt. The agent cannot see or set these either: they
		// arrive as container environment, and nothing in the tool surface exposes
		// them.
		for header, env := range map[string]string{
			"FB-Run-Uid":   "BENCH_RUN_UID",
			"FB-Batch-Uid": "BENCH_RUN_BATCH",
			"FB-Model":     "BENCH_RUN_MODEL",
			"FB-Arm":       "BENCH_RUN_ARM",
			"FB-Seed":      "BENCH_RUN_SEED",
		} {
			if v := os.Getenv(env); v != "" {
				req.Header.Set(header, v)
			}
		}
		return req, nil
	}

	cl := &http.Client{Timeout: gradeRequestTimeout}
	started := time.Now()
	var body []byte
	for attempt := 0; ; attempt++ {
		if attempt > 0 {
			time.Sleep(gradeRetryWaits[attempt-1])
		}
		req, err := newReq()
		if err != nil {
			return nil, err
		}
		resp, err := cl.Do(req)
		var status int
		if err == nil {
			status = resp.StatusCode
			body, _ = io.ReadAll(resp.Body)
			resp.Body.Close()
			if status == http.StatusOK {
				break
			}
		}
		// Only a transport failure or a 5xx is worth sending again. A 4xx is a
		// verdict on the candidate itself -- an oversized input is oversized on
		// every attempt -- and repeating it just burns the episode's clock.
		retryable := err != nil || status >= 500
		last := attempt >= len(gradeRetryWaits)
		// The clock matters more than the count, and the budget has to cover what
		// the next attempt could cost rather than only what the last one did --
		// otherwise a hung endpoint spends `budget + one timeout` and the constant
		// means nothing. Counting the timeout in makes the arithmetic pick the
		// right cases by itself: an instant refusal (0 + 2 + 600) retries, while a
		// first attempt that hung to the timeout (600 + 2 + 600) does not, which
		// is what should happen anyway. A wedged endpoint is not worth a second
		// ten minutes of an episode that only gets thirty.
		next := gradeRetryWaits[min(attempt, len(gradeRetryWaits)-1)]
		overBudget := time.Since(started)+next+gradeRequestTimeout > gradeRetryBudget
		if !retryable || last || overBudget {
			if err != nil {
				return nil, fmt.Errorf("remote grade: %w", err)
			}
			return nil, fmt.Errorf("remote grade status %d: %s", status, trunc(string(body), 300))
		}
	}
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, fmt.Errorf("remote grade decode: %w", err)
	}
	// Seal the verdict from the agent: return only the allow-listed fields unless
	// the TRUSTED runner explicitly asked to reveal it (BENCH_GRADE_REVEAL=1).
	// This allow-list is the last gate on blind evaluation, so it stays an
	// allow-list -- a field is forwarded because someone decided it may be, never
	// because the oracle happened to send it.
	//
	// crash_novelty says whether THIS run has already produced the same crash.
	// It carries nothing about the global pool, about other runs, or about which
	// crash the challenge was built around; the agent could derive it by diffing
	// its own stack traces, and forwarding it only makes that reliable. It is
	// omitted when the oracle sends null -- there is a difference between "not a
	// new crash" and "we cannot say", and the agent must not read one as the other.
	if os.Getenv("BENCH_GRADE_REVEAL") != "1" {
		sealed := map[string]any{}
		for _, k := range []string{"harness_output", "duration_ms", "crash_novelty"} {
			if v, ok := out[k]; ok && v != nil {
				sealed[k] = v
			}
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
