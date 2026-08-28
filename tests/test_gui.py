from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from installer.gui import App, INSTALL_TASKS, PROVIDER_GROUPS, resource_path
from installer.providers import PROVIDERS


class FakeWidget:
    def __init__(self):
        self.values = {}
        self.started = False

    def config(self, **kwargs):
        self.values.update(kwargs)

    def cget(self, key):
        return self.values.get(key)

    def start(self, *_):
        self.started = True

    def stop(self):
        self.started = False


class FakeVar:
    def __init__(self, value=0):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


def headless_app() -> App:
    app = object.__new__(App)
    app.active_task = None
    app.completed_tasks = set()
    app.progress_animating = False
    app.task_badge = FakeWidget()
    app.task_title = FakeWidget()
    app.task_meta = FakeWidget()
    app.task_state = FakeWidget()
    app.task_detail = FakeWidget()
    app.current_task_bar = FakeWidget()
    app.current_task_value = FakeVar()
    app.progress_bar = FakeWidget()
    app.progress_value = FakeVar()
    app.progress_text = FakeWidget()
    return app


class DynamicProgressTests(unittest.TestCase):
    def test_branded_icon_assets_are_packaged_from_source_tree(self):
        self.assertTrue(resource_path("assets/app_icon.png").is_file())
        self.assertTrue(resource_path("assets/app_icon.ico").is_file())

    def test_only_current_task_card_is_replaced(self):
        app = headless_app()
        app._update_task_progress("系统检查", "正在检查")
        self.assertEqual(app.task_title.cget("text"), "系统检查")
        app._update_task_progress("Git Bash", "解压进度：42%")
        self.assertEqual(app.task_title.cget("text"), dict(INSTALL_TASKS)["Git Bash"])
        self.assertEqual(app.current_task_value.get(), 42)
        self.assertIn("系统检查", app.completed_tasks)
        self.assertIn("第 6 /", app.task_meta.cget("text"))

    def test_complete_state_reaches_100_percent(self):
        app = headless_app()
        app._update_task_progress("连接测试", "正在验证")
        app._update_task_progress("完成", "连接成功")
        self.assertEqual(app.current_task_value.get(), 100)
        self.assertEqual(app.task_title.cget("text"), "全部配置完成")
        self.assertEqual(len(app.completed_tasks), len(INSTALL_TASKS))

    def test_domestic_source_and_managed_npm_are_visible_steps(self):
        tasks = dict(INSTALL_TASKS)
        self.assertIn("安装源检测", tasks)
        self.assertIn("npm 运行环境", tasks)
        self.assertLess(
            [item[0] for item in INSTALL_TASKS].index("安装源检测"),
            [item[0] for item in INSTALL_TASKS].index("Claude Code"),
        )

    def test_all_existing_action_buttons_are_kept_in_gui(self):
        source_path = Path(inspect.getsourcefile(App) or "")
        source = source_path.read_text(encoding="utf-8")
        for label in (
            "开始安装并配置", "测试连接", "启动 Claude Code", "启动 VS Code",
            "检查更新", "状态", "诊断", "卸载",
        ):
            with self.subTest(label=label):
                self.assertIn(label, source)

    def test_provider_grouping_preserves_every_backend_provider(self):
        grouped = {
            provider_id
            for _brand_id, _label, provider_ids in PROVIDER_GROUPS
            for provider_id in provider_ids
        }
        self.assertEqual(grouped, set(PROVIDERS))
        self.assertEqual(App._brand_for_provider("aliyun-token"), "aliyun")
        self.assertEqual(App._brand_for_provider("deepseek"), "deepseek")

    def test_main_content_has_a_real_vertical_scrollbar(self):
        source_path = Path(inspect.getsourcefile(App) or "")
        source = source_path.read_text(encoding="utf-8")
        self.assertIn("class ModernScrollbar", source)
        self.assertIn("yscrollcommand=self.content_scrollbar.set", source)
        self.assertIn('self.content_canvas.yview_scroll', source)

    def test_template_visuals_use_real_brand_icon_and_circular_progress(self):
        source_path = Path(inspect.getsourcefile(App) or "")
        source = source_path.read_text(encoding="utf-8")
        self.assertIn("image=self.app_icon_large", source)
        self.assertIn("class ProgressRing", source)
        self.assertIn("self.progress_ring = ProgressRing", source)

    def test_v311_uses_antialiased_icons_and_official_vscode_asset(self):
        self.assertTrue(resource_path("assets/ui/vscode.png").is_file())
        self.assertTrue(resource_path("assets/ui/update.png").is_file())
        self.assertTrue(resource_path("assets/ui/play-white.png").is_file())
        source_path = Path(inspect.getsourcefile(App) or "")
        source = source_path.read_text(encoding="utf-8")
        self.assertIn("_render_progress_ring", source)
        self.assertIn('self.ui_icons[name] = tk.PhotoImage', source)
        self.assertNotIn("def _draw_icon", source)


if __name__ == "__main__":
    unittest.main()
