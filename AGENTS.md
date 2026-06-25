# Talemate Repository Instructions

## Project overview

Talemate is an AI-backed roleplaying and narrative application focused on dialogue, narration, long-term memory, world-state tracking, character management, text-to-speech, visual generation, and configurable prompt templates.

The project contains:

- A Python 3.11+ backend under `src/talemate`.
- A Vue 3 and Vuetify frontend under `talemate_frontend`.
- Jinja2 prompt templates under `src/talemate/prompts/templates`.
- World-state and other reusable templates under `templates`.
- Python tests under `tests`.
- User and developer documentation under `docs`.

Important project files:

- `pyproject.toml`: Python dependencies, tooling, and test configuration.
- `talemate_frontend/package.json`: frontend dependencies and scripts.
- `mkdocs.yml`: documentation configuration.
- `start.bat`: Windows launcher using the repository's embedded runtimes.

## Development commands

Use the repository-managed environment where available.

Backend tests:

```powershell
embedded_python\python.exe -m uv run --extra dev python -m pytest -q
```

Targeted backend tests:

```powershell
embedded_python\python.exe -m uv run --extra dev python -m pytest tests/<test_file>.py -q
```

Python lint:

```powershell
embedded_python\python.exe -m uv run --extra dev ruff check <paths>
```

Frontend install and build:

```powershell
Set-Location talemate_frontend
..\embedded_node\npm.cmd ci
..\embedded_node\npm.cmd run build
```

Do not commit generated frontend build output, virtual environments, embedded runtimes, temporary attachments, caches, or other machine-local artifacts.

## Mandatory Git workflow

### One branch per feature or fix

- Every feature, bug fix, refactor, or distinct maintenance task must use its own branch.
- Do not implement feature work directly on `main`.
- Branch names should describe the work, for example `feature/background-agent-updates` or `fix/character-rename-state`.
- Keep unrelated changes on separate branches.

### Commit every completed change

- Every logical change must be committed.
- Keep commits focused and independently understandable.
- Separate unrelated changes into separate commits.
- Include relevant tests and documentation in the commit for the behavior they cover.
- Do not leave completed implementation work only in the working tree.
- Do not include unrelated pre-existing modifications in a commit.

### Worktree location

- Worktrees are optional. A branch may be checked out directly in the main repository when appropriate.
- If a worktree is used, it must be created inside the main repository at:

  `C:\Users\danie\projects\talemate\.worktrees\<branch-name>`

- Never create a worktree as a sibling of the repository.
- Never create a worktree elsewhere under `C:\Users\danie\projects` or anywhere else on the filesystem.
- Ensure `.worktrees/` remains ignored by Git.
- Remove temporary worktrees after their branch is merged or no longer needed.

### Repository boundary

- The repository root is `C:\Users\danie\projects\talemate`.
- Do not create, edit, move, or delete project files outside this directory.
- Do not create temporary implementation repositories, worktrees, generated assets, scripts, or support files outside this directory.
- Reading external files explicitly supplied by the user is allowed, but project output and modifications must remain inside the repository root.
- Commands that install dependencies or generate files must target paths inside the repository.

## Working-tree safety

- Inspect `git status --short --branch` before making changes.
- Treat existing modifications as user-owned unless their purpose is explicitly established.
- Preserve unrelated dirty files.
- Stage files explicitly by path; do not use broad staging when unrelated changes exist.
- Never discard, reset, clean, or overwrite user changes without explicit approval.
- Before committing, inspect the staged diff and run `git diff --cached --check`.

## Browser cleanup

- Close all browser tabs and browser instances opened for repository work before finishing the task, unless the user explicitly asks to keep them open.
- When testing Talemate in the Codex browser, verify that no Codex-owned WebSocket connection to the Talemate backend remains after browser cleanup; an empty automation tab list does not guarantee that the browser renderer has closed.

## Implementation expectations

- Trace existing behavior before changing it.
- Prefer the established agent, websocket, signal, configuration, and frontend-component patterns.
- Keep conversation and scene-content generation asynchronous and serial unless the task explicitly changes that contract.
- Put user-configurable agent behavior in the existing agent configuration system where practical.
- Maintain accurate busy/background status so the frontend does not incorrectly lock scene interaction.
- Add or update tests for behavioral changes.
- Run focused tests first, then broader validation proportional to the affected area.
- Build the frontend when Vue components, frontend state, or frontend dependencies change.

## Documentation

- Update user documentation when visible behavior or configuration changes.
- Place technical assessments and internal design notes under `docs/dev`.
- Keep documentation consistent with the implemented behavior and current UI.
