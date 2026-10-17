from __future__ import annotations

import argparse
import html

from .app import app


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m router")
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit", help="show the append-only audit log")
    audit_parser.add_argument("--limit", type=int, default=50)
    audit_parser.add_argument("--html", action="store_true")
    args = parser.parse_args()
    if args.command == "audit":
        events = app.state.manager.audit_events(args.limit)
        if args.html:
            rows = "".join(
                f"<tr><td>{e['id']}</td><td>{html.escape(e['timestamp'])}</td>"
                f"<td>{html.escape(e['event_type'])}</td>"
                f"<td>{html.escape(e.get('tenant') or '')}</td>"
                f"<td>{html.escape(e.get('reason') or '')}</td></tr>"
                for e in events
            )
            print(
                f"<table><tr><th>id</th><th>timestamp</th><th>event</th><th>tenant</th><th>reason</th></tr>{rows}</table>"
            )
        else:
            for event in events:
                print(
                    f"{event['id']:>4} {event['timestamp']} {event['event_type']:<20} "
                    f"{event.get('tenant') or '-':<18} {event.get('reason') or ''}"
                )


if __name__ == "__main__":
    main()
