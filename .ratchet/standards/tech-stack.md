---
tag: tech-stack
---

# Tech Stack

> Concern: architecture / toolchain

## Intent

Pin the project's canonical technology choices so every change uses the same
runtime, package managers, frameworks, and datastore. This prevents toolchain
drift (a stray `npm`, a second ORM, a different web framework) that fragments the
build, the dependency graph, and onboarding.

## Guidelines

**Backend**

- The backend targets **Python 3.14**. Code MUST NOT rely on APIs newer than 3.14
  or use syntax/features removed before it; `requires-python` MUST be `>=3.14`.
- Dependencies and virtualenvs are managed exclusively with **uv**. Use
  `uv add` / `uv sync` / `uv run`; do not introduce `pip install`, `poetry`,
  `pipenv`, `conda`, or a hand-edited `requirements.txt` as the source of truth.
  The committed lockfile is `uv.lock`.
- The HTTP/API layer is built with **FastAPI**. New endpoints are FastAPI routes
  (with Pydantic models for request/response); do not add a second web framework
  (Flask, Django, Starlette-direct, aiohttp) for app routes.
- The datastore is **PostgreSQL**. Persistent application data lives in
  PostgreSQL; do not introduce another relational engine (SQLite, MySQL) without
  updating this standard first. Schema changes go through versioned migrations,
  and connection configuration is read from the environment (no hard-coded
  credentials or connection strings).

**Frontend**

- The frontend package manager is **pnpm**. Install/scripts use `pnpm`; do not
  commit a `package-lock.json` or `yarn.lock`. The committed lockfile is
  `pnpm-lock.yaml`.
- The build tool and dev server is **Vite**. Do not add a competing bundler
  (webpack, Create React App, Parcel) for the app build.
- The UI is **React with TypeScript**. Application source files are `.ts`/`.tsx`
  (not `.js`/`.jsx`), and new components are typed React components.

## Applies to

Every change that adds or modifies backend code, frontend code, dependencies,
build tooling, or datastore access. A change that introduces a tool or framework
outside this list MUST first update this standard to record the decision.
