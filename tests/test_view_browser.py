"""The built page follows newly created directories and late run records.

Install Chromium with ``python -m playwright install chromium``. Like the
other UI smoke tests, LC_UI_TESTS_REQUIRED=1 makes missing prerequisites fail.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from astra.universes import Place
from playwright.sync_api import Browser, Error, expect, sync_playwright
from test_serve import _gate, _record
from test_serve import project as project
from test_serve import state as state

from lightcone.engine import serve as engine


@pytest.fixture
def browser() -> Iterator[Browser]:
    _gate()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Error as error:
            if os.environ.get("LC_UI_TESTS_REQUIRED"):
                pytest.fail(str(error))
            pytest.skip("Chromium is not installed: python -m playwright install chromium")
        yield browser
        browser.close()


def test_open_dialog_follows_artifacts_and_late_provenance(
    browser: Browser, state: Path, project: Path
) -> None:
    (project / "astra.yaml").write_text("""\
spec_version: "0.1.0"
label: Viewer regression
description: Live viewer regression
decisions:
  binning:
    options: {coarse: {}, fine: {}}
universes:
  baseline:
    decisions: {binning: coarse}
outputs:
  metric:
    type: metric
    format: json
    description: A changing metric
    decisions: [binning]
    recipe:
      command: python metric.py '{decisions.binning}' '{output}'
""")
    # A project-local link must follow edits to the target, too.
    spec = project / "specs" / "main.yaml"
    spec.parent.mkdir()
    (project / "astra.yaml").rename(spec)
    (project / "astra.yaml").symlink_to(spec)
    page = browser.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.add_init_script("""
      const NativeWebSocket = window.WebSocket;
      window.sentSubscriptions = [];
      window.WebSocket = class extends NativeWebSocket {
        send(message) {
          window.sentSubscriptions.push(JSON.parse(message));
          return super.send(message);
        }
      };
    """)
    page.goto(engine.serve(project, "outputs.metric").url)
    provenance = page.locator('[data-slot="output-provenance"]')
    expect(provenance).to_contain_text("No recorded run yet.")
    wanted = "results/baseline/metric.json"
    page.wait_for_function(
        "path => window.sentSubscriptions.some(m => m.type === 'client_opened' && m.path === path)",
        arg=wanted,
    )
    # The upstream protocol has no acknowledgement that inotify is ready.
    page.wait_for_timeout(500)
    # Reasserting subscriptions must start watches for each new ancestor.
    for directory in (project / "results", project / "results/baseline"):
        page.evaluate("window.sentSubscriptions = []")
        directory.mkdir()
        page.wait_for_function(
            "path => window.sentSubscriptions.some("
            "m => m.type === 'client_opened' && m.path === path)",
            arg=wanted,
        )
        page.wait_for_timeout(500)
    artifact = project / wanted
    artifact.write_text('{"value": "first-value"}')
    expect(page.get_by_text("first-value", exact=True).first).to_be_visible()
    artifact.write_text('{"value": "second-value"}')
    expect(page.get_by_text("second-value", exact=True).first).to_be_visible()
    expect(provenance).to_contain_text("No recorded run yet.")
    before = artifact.stat().st_mtime_ns
    place = Place("metric", "metric#baseline", ".astra/records/baseline/metric.json", wanted)
    (project / place.record).parent.mkdir(parents=True)
    (project / place.record).write_text(json.dumps(_record(place.address).to_json()))
    expect(provenance).to_contain_text("abc123")
    assert artifact.stat().st_mtime_ns == before
    spec.write_text(spec.read_text().replace("Viewer regression", "Updated analysis"))
    expect(page).to_have_title("Updated analysis")
    assert not errors
    page.close()
