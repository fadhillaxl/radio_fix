from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    """Membangun dashboard HTML sederhana dari file report benchmark/profiling."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = []
    if isinstance(data, dict):
        data = [data]
    for entry in data:
        if "baseline" in entry and "fastpath" in entry:
            rows.append(
                f"<tr><td>{entry['mode']}</td><td>{entry['size_bytes']}</td>"
                f"<td>{entry['baseline']['useful_bit_rate_bps']:.2f}</td>"
                f"<td>{entry['fastpath']['useful_bit_rate_bps']:.2f}</td>"
                f"<td>{entry['fastpath_metrics']['average_window_latency_ms']:.2f}</td>"
                f"<td>{entry['fastpath_metrics']['max_rss_kb']}</td></tr>"
            )
        elif "result" in entry:
            rows.append(
                f"<tr><td>continuous</td><td>{entry['timestamp']}</td>"
                f"<td>-</td><td>{entry['result']['useful_bit_rate_bps']:.2f}</td>"
                f"<td>{entry['metrics']['average_window_latency_ms']:.2f}</td>"
                f"<td>{entry['metrics']['max_rss_kb']}</td></tr>"
            )
    html = (
        "<html><head><title>Performance Dashboard</title></head><body>"
        "<h1>Optimized Transfer Dashboard</h1>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<tr><th>Mode</th><th>Payload/Time</th><th>Baseline bps</th><th>Fast-path bps</th><th>Avg Latency ms</th><th>Max RSS KB</th></tr>"
        + "".join(rows)
        + "</table></body></html>"
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
