"""Dashboard static UI invariants."""
from pathlib import Path
import unittest

from planning_factory.adapters.dashboard_api import ASSETS, WEB

EXPECTED_VIEWS = (
    'machines', 'orders', 'nesting', 'loads', 'materials', 'progress', 'staged', 'deliveries',
    'calendars', 'daily', 'parameters', 'assistant', 'memory',
)


class DashboardAssetsTests(unittest.TestCase):
    def test_assets_are_in_packaged_source_and_no_external_framework_needed(self):
        for location, (name, _) in ASSETS.items():
            with self.subTest(location=location):
                self.assertTrue((WEB / name).is_file())
        html = (WEB / 'index.html').read_text(encoding='utf-8')
        self.assertIn('lang="it"', html)
        self.assertIn('id="main"', html)
        self.assertIn('id="inspector"', html)
        self.assertIn('id="download-scenario"', html)
        self.assertIn('id="download-plan"', html)
        self.assertIn('id="chat-panel"', html)
        self.assertIn('id="chat-toggle"', html)
        self.assertIn('src="/app.js"', html)
        self.assertIn('href="/styles.css"', html)
        for view in EXPECTED_VIEWS:
            with self.subTest(view=view):
                self.assertIn('data-view="' + view + '"', html)

    def test_browser_api_stays_read_only_and_clear_about_partial_features(self):
        js = (WEB / 'app.js').read_text(encoding='utf-8')
        self.assertIn("fetch('/api/bootstrap'", js)
        self.assertIn("fetch(state.archiveBase?'/api/snapshots/preview':simulated?'/api/simulate':'/api/recalculate'", js)
        self.assertIn('Nessun programma CAM', js)
        self.assertIn('Analisi deterministica', js)
        self.assertIn('trackHTML(bookingList,start,end,r.kind)', js)
        self.assertIn('for(const a of list)for(const seg of a.segments)', js)
        self.assertIn("fetch('/api/descriptions'", js)
        self.assertIn('a.segments.some(seg => ms(seg.end)>start', js)
        self.assertIn("state.archiveBase={scenario_id:data.scenario.id,revision:data.meta.saved_revision}", js)
        self.assertIn('function renderMemory()', js)
        self.assertIn('function renderMaterials()', js)
        self.assertIn('function renderProgress()', js)
        self.assertIn('function renderStaged()', js)
        self.assertIn('function renderStagedTimeline(result)', js)
        self.assertIn('class="stage-block ', js)
        self.assertIn('for(const segment of b.segments||[])', js)
        self.assertIn('async function loadStaged()', js)
        self.assertIn("fetch('/api/v03-preview'", js)
        self.assertIn('function answerOperationalQuestion(question)', js)
        self.assertIn("function downloadSnapshot(kind)", js)
        self.assertIn("data.plan.scenario_digest", js)
        self.assertIn('id="assistant-form"', js)
        self.assertIn('data-assistant-prompt', js)
        self.assertIn('function sendChat(raw)', js)
        self.assertIn("fetch('/api/assistant'", js)
        self.assertIn('scenario_digest:digest', js)
        self.assertIn("postAnalysis('/api/explain'", js)
        self.assertIn("postAnalysis('/api/loads'", js)
        self.assertIn('data-chat-apply', js)
        # Model output is escaped before the minimal formatting is applied.
        self.assertIn("const lines=esc(text)", js)
        self.assertNotIn('innerHTML=answer', js)
        self.assertNotIn('fetch(\'https://', js)
        self.assertIn('Consuntivi ≠ stato attuale macchina', js)
        self.assertIn('source_publishable===false', js)
        self.assertNotIn('publish_factory', js)
        self.assertNotIn('sqlcmd', js)
        self.assertNotIn('cdn.jsdelivr.net', js)
        css = (WEB / 'styles.css').read_text(encoding='utf-8')
        self.assertIn('@media(max-width:760px)', css)
        self.assertIn('prefers-reduced-motion', css)


if __name__ == '__main__':
    unittest.main()
