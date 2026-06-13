---
name: submit-pr-review-loop
description: Use when the user asks to commit local changes, open a GitHub pull request, trigger @codex review, poll review feedback, and keep fixing actionable review comments until approval or no actionable feedback remains.
---

# Submit PR Review Loop

## Overview

Finish a branch by publishing it as a PR, requesting Codex review, and running a
review-fix loop. Do not treat review comments as automatically correct; verify
each comment against the code, product context, and tests before changing code.

## Preconditions

- Use the GitHub app tools when available; use `gh` only when connector coverage
  is insufficient.
- Read `AGENTS.md` and `RULES.md` before preparing the PR description.
- Never include unrelated user changes in a commit. Check `git status --short`
  and `git diff` before staging.
- Do not merge the PR unless the user explicitly asks.

## Workflow

1. Verify the branch.
   - Confirm branch name and upstream state.
   - Review the diff and list the files to include.
   - Run the most relevant tests, build, and lint. If full verification is not
     possible, record the exact command and reason.

2. Commit intentionally.
   - Stage only files that belong to the user's request.
   - Use a concise commit message describing the user-visible change.
   - Re-check `git status --short` after committing.

3. Push and open the PR.
   - Push the current branch.
   - Create a PR against the repository default branch unless the user specifies
     another base.
   - PR description must include the XQuant required sections:
     - linked docs/plans design and implementation docs, or why none exist
     - requirement background
     - design constraints
     - data flow
     - non-goals
     - compatibility
     - key review scope
     - verification results

4. Trigger Codex review.
   - Add a top-level PR comment containing exactly:

     ```text
     @codex review
     ```

   - Record the current time and current known review/comment IDs so the next
     polling step can distinguish new feedback from older comments.

5. Poll for review outcome.
   - Poll PR comments, review submissions, and unresolved review threads.
   - Prefer structured review-thread APIs when available; otherwise use `gh` to
     fetch reviews and comments.
   - Continue until one of these happens:
     - a Codex approval review appears
     - new Codex review comments appear
     - a clear timeout occurs; report that review has not returned yet

6. Triage comments.
   - For each new comment, read the referenced code and surrounding behavior.
   - Classify it as:
     - valid and actionable
     - valid but already covered or out of scope
     - incorrect or based on a wrong assumption
     - unclear and requiring clarification
   - Do not blindly implement comments. Explain non-implemented comments on the
     PR with concrete reasoning and source references.

7. Fix valid comments.
   - For valid actionable comments, implement the smallest correct fix.
   - Add or update tests when the comment concerns behavior.
   - Run targeted verification and any required broader checks.
   - Commit and push the fixes.
   - Reply to the comment with what changed and the verification run.
   - Resolve threads only when the fix or explanation fully addresses them.

8. Repeat the loop.
   - After pushing fixes or replying to comments, add `@codex review` again.
   - Poll again.
   - Continue until Codex approves or there are no valid actionable comments
     left and any remaining comments have been answered.

## Reporting

When returning to the user, include:

- PR link and branch.
- Commit SHA(s) created.
- Review loop status: approved, waiting, fixed comments, or blocked.
- Verification commands and outcomes.
- Any review comments intentionally not implemented and why.

Use concrete status, not "should be fine". If polling timed out, say so and
provide the latest observed PR review/comment state.
