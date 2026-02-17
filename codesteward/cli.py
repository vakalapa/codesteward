"""CLI entrypoint for CodeSteward."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from codesteward.config import load_config

app = typer.Typer(
    name="codesteward",
    help="Simulate multi-reviewer code reviews for GitHub PRs.",
    add_completion=False,
)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True)],
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    db: str = typer.Option(None, "--db", help="Path to SQLite database file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Initialize the database and cache directory."""
    _setup_logging(verbose)
    cfg = load_config(overrides={"db_path": db} if db else {})

    from codesteward.db import Database

    database = Database(cfg.db_path)
    database.init_schema()
    database.close()

    console.print(f"[green]Database initialized at {cfg.db_path}[/green]")


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

@app.command()
def ingest(
    repo: str = typer.Option(..., "--repo", help="GitHub repo (owner/name)"),
    since: str = typer.Option("180d", "--since", help="Lookback window (e.g., 180d, 365d)"),
    areas: Optional[str] = typer.Option(None, "--areas", help="Comma-separated area filters"),
    max_prs: int = typer.Option(300, "--max-prs", help="Maximum PRs to ingest"),
    resume: bool = typer.Option(False, "--resume", help="Only ingest PRs newer than last run"),
    db: str = typer.Option(None, "--db", help="Path to SQLite database file"),
    config_file: str = typer.Option(None, "--config", help="Path to config YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Ingest repo ownership and historical PR review data."""
    _setup_logging(verbose)

    overrides: dict = {"repo": repo, "max_prs": max_prs}
    if db:
        overrides["db_path"] = db

    cfg = load_config(config_path=config_file, overrides=overrides)

    # Parse since
    since_days = _parse_since(since)

    from codesteward.db import Database
    from codesteward.github_client import GitHubClient
    from codesteward.ingest import Ingestor

    database = Database(cfg.db_path)
    database.init_schema()

    try:
        gh = GitHubClient(cfg.github_token.get_secret_value())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    ingestor = Ingestor(database, gh)
    area_list = [a.strip() for a in areas.split(",")] if areas else None

    with console.status(f"Ingesting data from {repo}..."):
        stats = ingestor.ingest(
            repo=repo,
            since_days=since_days,
            max_prs=max_prs,
            areas=area_list,
            resume=resume,
        )

    table = Table(title="Ingestion Summary")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="green", justify="right")
    for key, val in stats.items():
        table.add_row(key.replace("_", " ").title(), str(val))
    console.print(table)

    database.close()


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------

@app.command()
def profile(
    repo: str = typer.Option(..., "--repo", help="GitHub repo (owner/name)"),
    top_reviewers: int = typer.Option(50, "--top-reviewers", help="Number of top reviewers to profile"),
    db: str = typer.Option(None, "--db", help="Path to SQLite database file"),
    config_file: str = typer.Option(None, "--config", help="Path to config YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Build or update reviewer skill cards."""
    _setup_logging(verbose)

    overrides: dict = {"repo": repo}
    if db:
        overrides["db_path"] = db

    cfg = load_config(config_path=config_file, overrides=overrides)

    from codesteward.db import Database
    from codesteward.profiler import ReviewerProfiler

    database = Database(cfg.db_path)

    profiler = ReviewerProfiler(database, redact_quotes=cfg.redact_quotes)

    with console.status(f"Profiling top {top_reviewers} reviewers for {repo}..."):
        cards = profiler.profile_all(repo, top_n=top_reviewers)

    if not cards:
        console.print("[yellow]No reviewers found. Run 'ingest' first.[/yellow]")
        raise typer.Exit(1)

    table = Table(title=f"Reviewer Profiles ({len(cards)})")
    table.add_column("Reviewer", style="cyan")
    table.add_column("Reviews", justify="right")
    table.add_column("Approval Rate", justify="right")
    table.add_column("Blocking", justify="center")
    table.add_column("Top Focus", style="green")
    table.add_column("Common Blockers")

    for card in cards:
        # Find top focus area
        weights = card.focus_weights.model_dump()
        top_focus = max(weights, key=weights.get) if any(v > 0 for v in weights.values()) else "general"
        blockers_str = ", ".join(card.common_blockers[:2]) if card.common_blockers else "-"
        table.add_row(
            card.reviewer,
            str(card.total_reviews),
            f"{card.approval_rate:.0%}",
            card.blocking_threshold.value,
            top_focus,
            blockers_str,
        )

    console.print(table)
    database.close()


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

@app.command()
def review(
    repo: str = typer.Option(..., "--repo", help="GitHub repo (owner/name)"),
    pr: Optional[int] = typer.Option(None, "--pr", help="PR number to review"),
    diff: Optional[str] = typer.Option(None, "--diff", help="Path to local diff/patch file"),
    reviewer_count: int = typer.Option(5, "--reviewers", "-n", help="Number of reviewers to simulate"),
    output_dir: str = typer.Option("./out", "--output", "-o", help="Output directory"),
    db: str = typer.Option(None, "--db", help="Path to SQLite database file"),
    config_file: str = typer.Option(None, "--config", help="Path to config YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run simulated multi-reviewer review on a PR or diff."""
    _setup_logging(verbose)

    if not pr and not diff:
        console.print("[red]Error: Provide either --pr or --diff[/red]")
        raise typer.Exit(1)

    overrides: dict = {"repo": repo, "reviewer_count": reviewer_count, "output_dir": output_dir}
    if db:
        overrides["db_path"] = db

    cfg = load_config(config_path=config_file, overrides=overrides)

    from codesteward.aggregator import MaintainerAggregator
    from codesteward.db import Database
    from codesteward.discovery import ReviewerDiscovery
    from codesteward.render import write_outputs
    from codesteward.repo_mapper import RepoMapper
    from codesteward.schemas import ChangedFile, ReviewerSkillCard
    from codesteward.simulator import ReviewSimulator

    database = Database(cfg.db_path)
    gh = None

    # Get diff text and changed files
    diff_text: str
    changed_files: list[ChangedFile]
    pr_title = ""
    pr_body = ""

    if pr:
        from codesteward.github_client import GitHubClient
        gh = GitHubClient(cfg.github_token.get_secret_value())
        with console.status(f"Fetching PR #{pr} from {repo}..."):
            pr_data = gh.get_pr(repo, pr)
            pr_title = pr_data.get("title", "")
            pr_body = pr_data.get("body", "") or ""
            diff_text = gh.get_pr_diff(repo, pr)
            raw_files = gh.get_pr_files(repo, pr)
            changed_files = [
                ChangedFile(
                    path=f["filename"],
                    additions=f.get("additions", 0),
                    deletions=f.get("deletions", 0),
                    patch=f.get("patch", ""),
                )
                for f in raw_files
            ]
    elif diff:
        diff_path = Path(diff)
        if not diff_path.exists():
            console.print(f"[red]Diff file not found: {diff}[/red]")
            raise typer.Exit(1)
        diff_text = diff_path.read_text(encoding="utf-8")
        changed_files = _parse_diff_to_files(diff_text)
    else:
        console.print("[red]Unreachable[/red]")
        raise typer.Exit(1)

    if not changed_files:
        console.print("[yellow]No changed files found in the diff.[/yellow]")
        raise typer.Exit(1)

    console.print(f"[cyan]Analyzing {len(changed_files)} changed file(s)...[/cyan]")

    # Step 1: Build ChangeContext
    mapper = RepoMapper(database, gh)
    ctx = mapper.build_change_context(
        repo=repo,
        changed_files=changed_files,
        pr_number=pr,
        pr_title=pr_title,
        pr_body=pr_body,
    )

    console.print(f"  Areas: {', '.join(ctx.areas) or 'none detected'}")
    console.print(f"  Risk flags: {', '.join(ctx.risk_flags) or 'none'}")

    # Step 2: Discover reviewers
    discovery = ReviewerDiscovery(database)
    reviewer_infos = discovery.discover(ctx, top_k=reviewer_count)

    if not reviewer_infos:
        console.print("[yellow]No reviewers found. Using ownership-based fallback.[/yellow]")
        # Use likely_reviewers from ChangeContext as fallback
        from codesteward.schemas import ReviewerInfo
        reviewer_infos = [ReviewerInfo(login=r) for r in ctx.likely_reviewers[:reviewer_count]]

    console.print(f"  Selected {len(reviewer_infos)} reviewer(s): {', '.join(r.login for r in reviewer_infos)}")

    # Step 3: Load skill cards (or create default ones)
    cards: list[ReviewerSkillCard] = []
    from codesteward.schemas import FocusWeights, BlockingThreshold, ReviewerCategory as RC
    for ri in reviewer_infos:
        raw_card = database.get_reviewer_card(repo, ri.login)
        if raw_card:
            cards.append(ReviewerSkillCard.model_validate_json(raw_card))
        else:
            # Create a default card with focus weights derived from reviewer categories
            focus = _default_focus_for_categories(ri.categories)
            cards.append(ReviewerSkillCard(
                reviewer=ri.login,
                focus_weights=focus,
                blocking_threshold=BlockingThreshold.MEDIUM,
                total_reviews=ri.review_count,
            ))

    # Step 4: Simulate reviews
    simulator = ReviewSimulator(
        anthropic_api_key=cfg.anthropic_api_key.get_secret_value(),
        strict_evidence=cfg.strict_evidence_mode,
        llm_model=cfg.llm.model,
        llm_max_tokens=cfg.llm.max_tokens,
        max_diff_chars=cfg.llm.max_diff_chars,
    )

    with console.status("Simulating reviews..."):
        reviews = simulator.simulate_all(ctx, diff_text, cards)

    # Step 5: Aggregate
    aggregator = MaintainerAggregator()
    summary = aggregator.aggregate(ctx, reviews)

    # Step 6: Write outputs
    md_path, json_path = write_outputs(summary, output_dir=cfg.output_dir)

    # Print summary to console
    console.print("")
    console.print(f"[bold]Merge Verdict: {summary.verdict.value}[/bold]")
    console.print(f"  Blockers: {len(summary.merged_blockers)}")
    console.print(f"  Suggestions: {len(summary.merged_suggestions)}")
    if summary.disagreements:
        console.print(f"  [yellow]Disagreements: {len(summary.disagreements)}[/yellow]")
    console.print("")
    console.print(f"[green]Report written to {md_path}[/green]")
    console.print(f"[green]JSON written to {json_path}[/green]")

    database.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_since(since: str) -> int:
    """Parse a duration string like '180d' or '6m' into days."""
    since = since.strip().lower()
    if since.endswith("d"):
        return int(since[:-1])
    if since.endswith("m"):
        return int(since[:-1]) * 30
    if since.endswith("y"):
        return int(since[:-1]) * 365
    return int(since)


def _parse_diff_to_files(diff_text: str) -> list:
    """Parse a unified diff into ChangedFile objects."""
    import re
    from codesteward.schemas import ChangedFile

    files: list[ChangedFile] = []
    current_file: str | None = None
    additions = 0
    deletions = 0
    patch_lines: list[str] = []

    for line in diff_text.split("\n"):
        if line.startswith("diff --git"):
            # Flush previous file
            if current_file:
                files.append(ChangedFile(
                    path=current_file,
                    additions=additions,
                    deletions=deletions,
                    patch="\n".join(patch_lines),
                ))
            # Parse new file path
            match = re.search(r"b/(.+)$", line)
            current_file = match.group(1) if match else None
            additions = 0
            deletions = 0
            patch_lines = []
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
            patch_lines.append(line)
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
            patch_lines.append(line)
        else:
            patch_lines.append(line)

    # Flush last file
    if current_file:
        files.append(ChangedFile(
            path=current_file,
            additions=additions,
            deletions=deletions,
            patch="\n".join(patch_lines),
        ))

    return files


def _default_focus_for_categories(categories: list) -> "FocusWeights":
    """Build sensible default focus weights when no profiled card exists."""
    from codesteward.schemas import FocusWeights, ReviewerCategory as RC

    focus = FocusWeights(
        api=0.4, tests=0.4, perf=0.3, docs=0.3,
        security=0.4, style=0.3, backward_compat=0.3,
    )
    for cat in categories:
        if cat == RC.TEST_CI_HAWK:
            focus.tests = 0.9
        elif cat == RC.API_STABILITY_HAWK:
            focus.api = 0.9
            focus.backward_compat = 0.7
        elif cat == RC.SECURITY_HAWK:
            focus.security = 0.9
        elif cat == RC.DOCS_HAWK:
            focus.docs = 0.9
    return focus


if __name__ == "__main__":
    app()
