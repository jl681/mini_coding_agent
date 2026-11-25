# Roadmap

[Back to README](../README.md)

## Next steps: isolated execution and reviewable patches

The following is planned work, not functionality currently available. The next
milestone is to solve a small real-repository issue in an isolated workspace and
export a patch with verification evidence.

### 1. Separate workspace operations from their execution backend

Define a workspace interface for listing, reading, editing, and running tests.
Keep the current local implementation and add a container-backed implementation.
Inject the selected backend into tool construction so model adapters and the
agent decision loop remain independent of Docker. All file operations and test
execution must target the same repository copy; changing only the pytest command
would leave an inconsistent execution boundary.

Acceptance: run the same workspace contract tests against local and container
backends, including path restrictions and edit behavior.

### 2. Run repository operations in a restricted container

Create a fresh container workspace per run. Keep model API calls and credentials
on the host; expose only controlled repository operations to the execution
backend. Do not mount the user's home directory or Docker socket, and do not
forward the host environment or API keys into repository processes.

Separate dependency preparation from task execution. Prepare dependencies in a
restricted environment without secrets, then disable runtime network access when
the task permits. Use a non-root user, drop unnecessary capabilities, restrict
privilege escalation, and bound CPU, memory, process count, output, and execution
time. A timeout must stop the container-side process, not merely the host's wait.
Always clean up containers after success, failure, or interruption.

Acceptance: container integration tests verify credential omission, configured
network and resource restrictions, timeout termination, and cleanup. A temporary
folder alone is not isolation; containers also are not an absolute boundary
against arbitrary hostile code.

### 3. Add a reproducible real-repository task

Select one small public issue with a reproducible failure. Pin the repository
commit, dependency setup, task description, and grading procedure. Start each
trial from that fixed revision and retain independent regression checks outside
the agent's available workspace until grading. Record model configuration,
execution-image identity, and run events alongside the result.

Acceptance: demonstrate the original failure, verify the repaired behavior, and
confirm relevant existing behavior still passes. Expand beyond the current small
fixtures gradually rather than treating one successful issue as general ability.

### 4. Export a patch and verification record

Compare the final workspace against its starting commit and export a patch,
test results, termination reason, and trace references before removing the
container. Preserve useful diagnostics on unsuccessful runs as well. Keep patch
creation separate from applying changes to the user's working tree, merging, or
deploying; those actions are not part of this milestone.

Acceptance: apply the exported patch to a fresh checkout of the pinned commit
and rerun independent checks. The deliverable should be reviewable without
keeping the original execution container alive.

