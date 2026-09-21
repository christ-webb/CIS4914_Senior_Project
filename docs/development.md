# Development guide

## Branches and pull requests

Create a short-lived branch for each issue. Keep PRs scoped to one outcome and
include tests or explain why a test is not applicable. Do not commit directly
to `main` after branch protection is enabled.

## Commands

Run `make bootstrap` once, then `make check` before requesting review. Use
`make api-dev`, `make web-dev`, and `make demo` for the first vertical slice.

## Contract changes

Changing a trace or span field requires coordinated updates to:

1. JSON Schema in `packages/contracts`
2. SDK model and serialization test
3. API model and endpoint test
4. Frontend TypeScript type
5. ClickHouse schema or migration, once persistence is enabled

## Definition of done

- Behavior is covered by an automated check.
- Public contracts and setup instructions are current.
- No secrets or customer telemetry are committed.
- The end-to-end demo still works.


## Windows local development and integration testing

The Makefile commands are the normal development workflow. On Windows, use the
commands below if `make api-dev` cannot find `uv` or `make web-dev` cannot find
`next`.

### One-time setup

From the repository root, copy the environment template and install Python
dependencies:

```powershell
Copy-Item .env.example .env
$env:Path = "C:\Users\<your-user>\.local\bin;$env:Path"
uv sync --all-packages --dev
```

Install frontend dependencies:

```powershell
cd .\apps\web
npm install
```

### Run the local stack

Open three terminals.

Terminal 1 - API:

```powershell
$env:Path = "C:\Users\<your-user>\.local\bin;$env:Path"
uv run --package no-more-500s-api uvicorn no_more_500s_api.main:app --reload --port 8000
```

Terminal 2 - web dashboard:

```powershell
cd .\apps\web
npm run dev
```

Terminal 3 - deterministic demo:

```powershell
$env:Path = "C:\Users\<your-user>\.local\bin;$env:Path"
uv run --package no-more-500s python .\examples\simple-agent\main.py
```

### Manual integration test checklist

1. Open `http://localhost:8000/docs` and confirm the API documentation loads.
2. Open `http://localhost:3000` and confirm the dashboard loads.
3. Run the deterministic demo and refresh the dashboard.
4. Confirm that a new run appears in the dashboard.
5. Select the run and confirm that its trace details load correctly.
6. If a trace does not load, save the API or browser error details and report the issue before merging changes.
