---
name: commit
description: Draft a commit message from the current diff (stops short of pushing)
---

Draft a commit message for the current working tree.

Status:
!`git status --short`

Staged/unstaged diff stat:
!`git diff --stat`

Rules:
- Read the full diff with `git diff` yourself before writing the message.
- Subject line under 60 chars, imperative mood. Body explains why, not what.
- Never commit or push — output the message plus the exact `git commit` command only.
- Extra focus: $ARGUMENTS
