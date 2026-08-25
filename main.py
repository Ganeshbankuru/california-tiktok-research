import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.utils.config import ensure_dirs, load_keywords, load_seeds, load_settings, project_path  # noqa: E402
from src.utils.helpers import setup_logging  # noqa: E402


def db_path(settings):
    return project_path(settings["app"]["db_path"])


def build_pipeline(settings):
    from src.database.db import Database
    from src.pipeline import ResearchPipeline

    db = Database(db_path(settings))
    return db, ResearchPipeline(db, settings, KEYWORDS, SEEDS, logger=LOG)


def cmd_new_run(args):
    settings = SETTINGS
    db, pipe = build_pipeline(settings)
    try:
        run_id = pipe.new_run(limit=args.limit, seed_filter=args.seed, reverify=args.reverify)
        print(f"Run completed: {run_id}")
    finally:
        pipe.close()
    paths, rows = export(settings, db)
    print(f"Qualified creators exported: {len(rows)}")
    for k, v in paths.items():
        print(f"  {k}: {v}")


def cmd_resume(args):
    settings = SETTINGS
    db, pipe = build_pipeline(settings)
    try:
        run_id = pipe.resume(run_id=args.run_id, extra_limit=args.limit)
        print(f"Run resumed/completed: {run_id}")
    finally:
        pipe.close()
    paths, rows = export(settings, db)
    print(f"Qualified creators exported: {len(rows)}")
    for k, v in paths.items():
        print(f"  {k}: {v}")


def export(settings, db):
    from src.exporters.exporters import export_all
    paths, rows = export_all(db, settings)
    db.close()
    return paths, rows


def cmd_export(_args):
    from src.database.db import Database
    from src.exporters.exporters import export_all
    settings = SETTINGS
    db = Database(db_path(settings))
    paths, rows = export_all(db, settings)
    print(f"Exported {len(rows)} qualified creators.")
    for k, v in paths.items():
        print(f"  {k}: {v}")


def cmd_report(_args):
    from src.database.db import Database
    from src.exporters.exporters import write_summary
    settings = SETTINGS
    db = Database(db_path(settings))
    path = write_summary(db, settings)
    print(f"Summary written: {path}")


def cmd_status(_args):
    from src.database.db import Database
    db = Database(db_path(SETTINGS))
    runs = db.query("SELECT * FROM research_runs ORDER BY start_time DESC LIMIT 10")
    if not runs:
        print("No runs recorded yet.")
        return
    for r in runs:
        print(f"{r['run_id']}  status={r['status']:<10} limit={r['limit_candidates']} seed={r['seed_filter'] or '-'} start={r['start_time'][:19]} end={(r['end_time'] or '-')[:19]}")
    stats = db.stats()
    print(f"Candidates total={stats['total']} verified={stats['verified']}")


def main():
    global SETTINGS, KEYWORDS, SEEDS, LOG
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="California Trucking TikTok Creator Researcher",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--new-run", action="store_true", help="start a new research run")
    mode.add_argument("--resume", action="store_true", help="resume the latest incomplete run")
    mode.add_argument("--export", action="store_true", help="export qualified creators to csv/xlsx/json")
    mode.add_argument("--report", action="store_true", help="regenerate output/research_summary.md")
    mode.add_argument("--status", action="store_true", help="show recent runs and counts")
    parser.add_argument("--limit", type=int, default=None, help="max candidates to verify this run (hard cap enforced)")
    parser.add_argument("--seed", type=str, default=None, help='restrict discovery to one seed account, e.g. --seed "Alex Nino"')
    parser.add_argument("--run-id", type=str, default=None, help="resume a specific run id")
    parser.add_argument("--reverify", action="store_true", help="with --new-run: re-attempt verification of candidates that were previously unreachable/blocked")
    args = parser.parse_args()

    SETTINGS = load_settings()
    KEYWORDS = load_keywords()
    SEEDS = load_seeds()
    ensure_dirs(SETTINGS)
    LOG = setup_logging(SETTINGS["app"].get("logs_dir", "logs"))
    LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

    if args.new_run:
        cmd_new_run(args)
    elif args.resume:
        cmd_resume(args)
    elif args.export:
        cmd_export(args)
    elif args.report:
        cmd_report(args)
    elif args.status:
        cmd_status(args)


if __name__ == "__main__":
    main()
