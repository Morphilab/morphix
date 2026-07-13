# Prompt Examples

Tested prompts that demonstrate how to use Morphix effectively across workflows and agents. Each example includes the exact prompt text, recommended workflow/agent, expected behavior, and usage tips.

---

## 1. Create a REST API

**Prompt:**
```
Create a REST API with FastAPI for a task management system. Include:
- CRUD endpoints for tasks (title, description, status, due_date)
- SQLAlchemy async models with PostgreSQL
- Pydantic schemas for request/response validation
- Query parameters for filtering by status and pagination
- Proper HTTP status codes and error handling
```

**Workflow:** Development
**Agent:** developer (auto-selected)

**Expected behavior:**
1. TaskAnalyzer classifies as `ejecutor`, medium complexity
2. Decomposer creates 4-5 subtasks: project setup, models, endpoints, validation, tests
3. Developer creates `main.py`, `models.py`, `schemas.py`, `tests/test_api.py`
4. Global verification runs LSP diagnostics
5. Aggregator provides a summary of all created files

**Tips:**
- If you have a specific database URL, mention it in the prompt
- Add "Use async SQLAlchemy 2.0 style" for modern patterns

---

## 2. Refactor to Dataclasses

**Prompt:**
```
Refactor the User and Product classes in src/models.py to use Python dataclasses.
Replace manual __init__ methods. Keep all existing methods and properties intact.
Add type hints for all fields. Ensure the refactored classes pass the existing tests
in tests/test_models.py.
```

**Workflow:** Development
**Agent:** developer

**Expected behavior:**
1. Agent reads `src/models.py` and `tests/test_models.py`
2. Converts regular classes to `@dataclass` decorators
3. Preserves existing methods, properties, and class variables
4. Runs `test_runner` to verify tests pass
5. Applies fixes if tests fail

**Tips:**
- Include the file path explicitly so the agent knows where to look
- Reference existing tests to ensure backward compatibility

---

## 3. Add Unit Tests

**Prompt:**
```
Write comprehensive pytest tests for the calculate_discount function in src/pricing.py.
Cover edge cases: zero quantity, negative prices, maximum discount cap, tier boundaries.
Use parametrize for multiple test cases. Include a test for the ValueError exception.
```

**Workflow:** Development
**Agent:** developer

**Expected behavior:**
1. Agent reads `src/pricing.py` to understand the function
2. Creates `tests/test_pricing.py` with `@pytest.mark.parametrize`
3. Covers normal cases, edge cases, and exception handling
4. Runs tests to verify they pass

**Tips:**
- Mention specific edge cases you care about
- "Use parametrize" triggers cleaner test patterns

---

## 4. Security Analysis

**Prompt:**
```
Analyze the authentication and authorization code in src/auth/ for security vulnerabilities.
Check for:
- SQL injection in raw queries
- Hardcoded API keys or secrets
- Missing input validation on user-supplied data
- Insecure password hashing (look for md5, sha1, or plaintext)
- Missing rate limiting on login endpoints
- JWT token expiration and refresh token handling
```

**Workflow:** Development (or Chat with Analista)
**Agent:** analista

**Expected behavior:**
1. Analista reads all files in `src/auth/`
2. Uses `code_search` to find patterns like `execute(`, `password`, `secret`
3. Uses `web_search` if needed for CVE references
4. Produces a structured report: vulnerability, severity, location, recommendation

**Tips:**
- Use the Analista agent directly (Chat mode) for pure analysis tasks
- Be specific about what to check — the more detailed, the better the analysis

---

## 5. New Project Setup

**Prompt:**
```
Set up a new FastAPI project with SQLAlchemy async models for a blog platform.
Project structure:
- src/models.py (User, Post, Comment with relationships)
- src/schemas.py (Pydantic models)
- src/database.py (async engine and session)
- src/main.py (FastAPI app with CORS)
- .env.example (DATABASE_URL template)
- alembic/ directory with initial migration
- requirements.txt with fastapi, sqlalchemy[asyncio], asyncpg, alembic, pydantic
```

**Workflow:** Coordinated
**Agent:** developer (with architect for design)

**Expected behavior:**
1. Architect designs the model relationships and API structure (phase 1)
2. Developer implements models + database config in parallel with schemas (phase 2)
3. Analista reviews the setup for correctness (phase 3)
4. All files created with proper imports and relationships

**Tips:**
- Use Coordinated for project setup — models and schemas can be created in parallel
- Specify the exact file structure to guide the decomposition

---

## 6. Debug an Error

**Prompt:**
```
Debug this error from my FastAPI app:

  File "src/routes/users.py", line 47, in get_user
    user = await db.execute(select(User).where(User.id == user_id))
  AttributeError: 'async_generator' object has no attribute 'first'

I'm using SQLAlchemy 2.0 async. The database session is injected via dependency.
File: src/routes/users.py
```

**Workflow:** Development
**Agent:** developer

**Expected behavior:**
1. Agent reads `src/routes/users.py` and `src/database.py`
2. Identifies the issue: missing `.scalars()` before `.first()`
3. Fixes the line to: `result = await db.execute(...); user = result.scalars().first()`
4. Explains the fix: `execute()` returns a `Result`, need `.scalars()` to get ORM objects

**Tips:**
- Always include the **full traceback** and **file path**
- Mention your framework versions (SQLAlchemy 2.0, FastAPI version)

---

## 7. Architecture Diagram

**Prompt:**
```
Generate a description of the system architecture for an e-commerce platform with:
- Next.js frontend (SSR + client-side)
- FastAPI backend (REST + WebSocket for real-time inventory)
- PostgreSQL for orders and users
- Redis for cart sessions and rate limiting
- RabbitMQ for async order processing
- S3 for product images

Describe the data flow for: user places an order, payment is processed, inventory is updated,
and confirmation email is sent.
```

**Workflow:** Development (or Chat with Architect)
**Agent:** architect

**Expected behavior:**
1. Architect produces a component diagram description with responsibilities
2. Maps data flow: Frontend → API Gateway → Order Service → RabbitMQ → Payment/Inventory/Email workers
3. Identifies async boundaries and failure modes
4. Provides an implementation order

**Tips:**
- For pure architecture questions, use Chat mode with the Architect agent
- The output is text, not a rendered diagram. Paste into Mermaid.live for visualization

---

## 8. Optimize Database Query

**Prompt:**
```
Optimize this SQLAlchemy query in src/reports.py. It's taking 3+ seconds with 100k orders:

  orders = await db.execute(
      select(Order).where(Order.created_at >= start_date)
                   .where(Order.status == 'completed')
                   .options(joinedload(Order.items), joinedload(Order.customer))
  )

Analyze the N+1 problem, suggest indexes, and rewrite the query.
The database is PostgreSQL 16.
```

**Workflow:** Development
**Agent:** developer (with analista for analysis)

**Expected behavior:**
1. Analista reads the query and related models
2. Identifies N+1: `Order.items` and `Order.customer` each trigger separate queries
3. Suggests indexes on `(created_at, status)` and `(order_id)` on items
4. Developer rewrites using `selectinload` for collections and adds index creation SQL

**Tips:**
- Include the **actual query code**, not just a description
- Mention the database type and version for index recommendations

---

## 9. CLI Tool with Argparse

**Prompt:**
```
Create a CLI tool called 'repogen' that generates project scaffolds from templates.
Features:
- argparse with subcommands: init, generate, list
- 'init' creates a .repogen.yaml config file with template URLs
- 'generate' creates a project from a named template (from config)
- 'list' shows available templates from the config
- Use Jinja2 for template rendering with project name, author variables
- Add --dry-run flag to preview without writing files
- Include proper error messages for missing config, invalid template names
```

**Workflow:** Coordinated
**Agent:** developer

**Expected behavior:**
1. Architect designs the CLI structure (subcommands, config format)
2. Developer implements in parallel: CLI argument parsing, template engine, config reader
3. Tests created for each subcommand
4. Output: `repogen.py`, `templates/`, `tests/test_repogen.py`

**Tips:**
- Coordinated works well here because subcommand implementations are independent
- Include example usage in the prompt for better decomposition

---

## 10. Code Review

**Prompt:**
```
Review the PR branch 'feature/user-preferences' compared to 'main'.
Focus on:
- Code style and consistency with the existing codebase
- Proper error handling (no bare except, no pass in except blocks)
- Test coverage for new code
- Performance issues (unnecessary queries in loops)
- Security: user input validation, authentication checks on new endpoints
```

**Workflow:** Development
**Agent:** analista

**Expected behavior:**
1. Analista uses `git_manager diff` to see changes
2. Reads modified files with `file_manager.read`
3. Checks for anti-patterns, security issues, and missing tests
4. Produces a structured review: summary, issues by severity, recommendations

**Tips:**
- Mention the branch names explicitly for git diff
- Use Chat mode with Analista for code reviews (single-agent, faster)

---

## 11. Data Processing Pipeline

**Prompt:**
```
Create a data processing pipeline that:
1. Reads a CSV of sales transactions (columns: date, product_id, quantity, price, region)
2. Cleans data: removes rows with missing values, converts date strings to datetime
3. Groups by region and month, calculates total revenue
4. Generates a summary CSV with columns: region, month, total_revenue, transaction_count
5. Creates a bar chart of monthly revenue by region using matplotlib

Sample data format:
date,product_id,quantity,price,region
2026-01-15,PROD001,5,29.99,North
2026-01-16,PROD002,3,49.99,South
```

**Workflow:** Development
**Agent:** developer

**Expected behavior:**
1. Agent creates `pipeline.py` with pandas for data processing
2. Creates sample CSV data for testing
3. Implements cleaning, grouping, and aggregation
4. Generates chart using matplotlib (code_exec sandbox supports it)
5. Runs pipeline end-to-end and verifies output

**Tips:**
- Provide sample data format so the agent knows the column structure
- `code_exec` supports matplotlib for chart generation

---

## 12. Web Scraper

**Prompt:**
```
Build a simple web scraper that extracts Python job listings from a jobs site.
Use httpx and BeautifulSoup. The scraper should:
- Fetch the first 3 pages of search results
- Extract job title, company name, location, and posting date
- Save results to a CSV file
- Respect robots.txt (check before scraping)
- Add a 2-second delay between requests
- Handle connection errors and rate limiting gracefully
```

**Workflow:** Development
**Agent:** developer

**Expected behavior:**
1. Agent creates `scraper.py` with httpx + BeautifulSoup
2. Implements robots.txt checking, pagination, and delay
3. Includes error handling for timeouts and 429 responses
4. Creates `requirements.txt` with httpx, beautifulsoup4
5. Tests with a mock response

**Tips:**
- Mention specific libraries you want used
- The agent cannot actually scrape live sites (sandbox blocks network) — it writes the code for you to run

---

## 13. Design Decision (Collaborative)

**Prompt:**
```
We're building a real-time collaborative editor. Should we use WebSockets or Server-Sent Events (SSE)
for the real-time sync protocol?

Consider:
- Bidirectional communication needs (cursor positions, text changes)
- Browser compatibility and polyfills
- Server load with 1000+ concurrent users
- Reconnection handling
- Our stack: FastAPI backend, React frontend, deployed on AWS ECS
```

**Workflow:** Collaborative
**Agent:** developer + analista (panel)

**Expected behavior:**
- Round 1: Developer argues for WebSockets (bidirectional, mature); Analista argues for SSE (simpler, HTTP/2 multiplexing)
- Round 2: Both consider the other's points — developer acknowledges SSE's simplicity for server→client sync; analista concedes cursor sync needs bidirectional
- Round 3: Converge on WebSocket for cursor sync + SSE for document state updates (hybrid)
- Moderator synthesizes the final consensus with implementation notes

**Tips:**
- Collaborative is perfect for "should we use X or Y" questions
- Provide concrete constraints (stack, scale, environment) for grounded debate

---

## 14. TDD Feature

**Prompt:**
```
Build a URL shortener service with TDD. Requirements:
- encode(url) returns a short code (6 chars, alphanumeric)
- decode(code) returns the original URL
- Same URL always gets the same code
- Different URLs get different codes
- Invalid codes raise ValueError
```

**Workflow:** TDD
**Agent:** developer

**Expected behavior:**
1. Agent detects no test files → green-field mode
2. Writes `test_shortener.py` with test cases for all requirements
3. Writes `shortener.py` implementing encode/decode with hashlib
4. Runs tests → all pass in iteration 1 (or 2 with fixes)

**Tips:**
- TDD works best when requirements are clear and testable
- List explicit requirements with expected inputs/outputs

---

## 15. Multi-Agent Project (Coordinated)

**Prompt:**
```
Build a microservice for user notifications with these components:
1. Notification model with types (email, sms, push) and status tracking
2. Provider abstraction (EmailProvider, SMSProvider, PushProvider) with factory pattern
3. Async task queue for sending notifications (use asyncio.Queue)
4. REST API for creating and checking notification status
5. Retry logic with exponential backoff for failed deliveries
6. Rate limiting per user (max 10 notifications per minute)
```

**Workflow:** Coordinated
**Agent:** developer + architect + analista

**Expected behavior:**
- Phase 1 (design): Architect designs the provider abstraction, queue architecture, and API structure
- Phase 2 (implement): Developer creates models, providers (parallel), API, and retry logic
- Phase 3 (verify): Analista reviews rate limiting implementation and tests
- Blackboard shares model definitions between provider and API subtasks

**Tips:**
- Coordinated shines when components have clear separation
- Mention "factory pattern" or specific design patterns to guide the architect
- The blackboard prevents duplicate model definitions across subtasks
