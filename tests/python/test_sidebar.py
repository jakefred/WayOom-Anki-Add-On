import importlib.util
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class SidebarTests(unittest.TestCase):
    def setUp(self):
        aqt = ModuleType("aqt")
        aqt.mw = Mock()
        aqt.mw.pm.name = "profile-a"
        aqt.gui_hooks = SimpleNamespace(profile_did_open=[], profile_will_close=[])
        qt = ModuleType("aqt.qt")
        qt.QAction, qt.QDockWidget, qt.QTimer, qt.QWidget, qt.Qt = Mock(), Mock(), Mock(), Mock(), Mock()
        qt.QDockWidget.return_value.setWidget.side_effect = lambda web: setattr(qt.QDockWidget.return_value.widget, "return_value", web)
        self.visible = False
        def set_visible(value):
            self.visible = value
        qt.QDockWidget.return_value.setVisible.side_effect = set_visible
        qt.QDockWidget.return_value.isHidden.side_effect = lambda: not self.visible
        webview = ModuleType("aqt.webview")
        webview.AnkiWebView = Mock()
        modules = patch.dict("sys.modules", {"aqt": aqt, "aqt.qt": qt, "aqt.webview": webview})
        modules.start()
        self.addCleanup(modules.stop)
        path = Path(__file__).resolve().parents[2] / "__init__.py"
        spec = importlib.util.spec_from_file_location("wayoom_addon_test", path)
        self.addon = importlib.util.module_from_spec(spec)
        package = patch.dict("sys.modules", {spec.name: self.addon})
        package.start()
        self.addCleanup(package.stop)
        spec.loader.exec_module(self.addon)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addon.runtime.user_files = Path(directory.name)
        recovery = patch(f"{spec.name}.wayoom.runtime.recover_in_background")
        self.recovery = recovery.start()
        self.addCleanup(recovery.stop)
        self.aqt, self.qt, self.webview = aqt, qt, webview

    def test_import_registers_hook_without_opening_ui_or_collection(self):
        self.assertEqual(self.aqt.gui_hooks.profile_did_open, [self.addon.profile_opened])
        self.qt.QDockWidget.assert_not_called()
        self.aqt.mw.form.menuTools.addAction.assert_called_once_with(self.addon.menu_action)

    def test_first_profile_load_opens_panel_automatically_and_loads_ui_once(self):
        # PANEL-02: the initial panel opens via the profile hook, without a
        # Tools-menu action. Repeated setup must not create a second panel.
        for hook in self.aqt.gui_hooks.profile_did_open:
            hook()
        self.assertIsNotNone(self.addon.sidebar)
        self.assertTrue(self.visible)
        self.addon.profile_opened()
        self.qt.QDockWidget.assert_called_once()
        html = self.webview.AnkiWebView.return_value.stdHtml.call_args.args[0]
        self.assertIn("<section", html)
        self.aqt.mw.addDockWidget.assert_called_once()

    def test_profile_close_cleans_up_and_next_profile_can_reopen(self):
        self.addon.add_sidebar()
        self.assertEqual(len(self.aqt.gui_hooks.profile_will_close), 1)
        self.aqt.gui_hooks.profile_will_close[0]()
        self.aqt.mw.removeDockWidget.assert_called_once()
        self.qt.QDockWidget.return_value.deleteLater.assert_called_once()
        self.assertIsNone(self.addon.sidebar)
        self.addon.profile_opened()
        self.assertEqual(self.qt.QDockWidget.call_count, 2)

    def test_tools_toggle_persists_visibility_for_each_profile(self):
        self.addon.profile_opened()
        self.addon.toggle_sidebar()
        self.assertFalse(self.visible)
        self.addon.close_sidebar()
        self.addon.profile_opened()
        self.assertFalse(self.visible)
        self.addon.close_sidebar()
        self.aqt.mw.pm.name = "profile-b"
        self.addon.profile_opened()
        self.assertTrue(self.visible)

    def test_hiding_and_reopening_does_not_cancel_running_job_or_recreate_webview(self):
        self.addon.profile_opened()
        coordinator = self.addon.runtime.coordinator
        request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": 1},
                   "cardCount": 1, "topic": "Biology", "difficulty": "intermediate", "providerId": "openai"}
        job = coordinator.start("profile-a", request, {})
        self.addon.toggle_sidebar()
        self.addon.toggle_sidebar()
        self.assertTrue(self.visible)
        self.assertEqual(coordinator.snapshot("profile-a")["request"], job["request"])
        self.assertEqual(coordinator.snapshot("profile-a")["state"], "generating")
        self.webview.AnkiWebView.assert_called_once()
        self.addon.close_sidebar()
        self.assertEqual(coordinator.snapshot("profile-a")["state"], "canceled")

    def test_panel_commands_reject_other_webviews_and_unknown_actions(self):
        self.addon.profile_opened()
        self.addon.runtime.outcome = {"requestId": "job-1"}
        with patch.object(self.addon.runtime, "view_cards") as view:
            self.addon.panel_command(object(), "wayoom:v1:view-cards")
            self.addon.panel_command(self.addon.sidebar.widget(), "wayoom:v1:erase")
            view.assert_not_called()
            self.addon.panel_command(self.addon.sidebar.widget(), "wayoom:v1:view-cards")
            view.assert_called_once_with("job-1")
