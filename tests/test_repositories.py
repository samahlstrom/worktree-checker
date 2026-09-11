"""Bounded repository discovery tests; no real home or Git checkout is used."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


loader = importlib.machinery.SourceFileLoader(
    "repositories_under_test",
    str(Path(__file__).parents[1] / "bin/repositories"),
)
spec = importlib.util.spec_from_loader(loader.name, loader)
repositories = importlib.util.module_from_spec(spec)
loader.exec_module(repositories)


class RepositoryDiscoveryTests(unittest.TestCase):
    def test_normalize_github_full_name_is_preserved(self):
        self.assertEqual(
            repositories.normalize_github_full_name("git@github.com:Example/widget.git"),
            "Example/widget",
        )

    def test_linked_worktree_resolves_to_primary_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            primary = home / "primary"
            linked = home / "linked"
            (primary / ".git" / "worktrees" / "linked").mkdir(parents=True)
            linked.mkdir()
            (linked / ".git").write_text(
                f"gitdir: {(primary / '.git' / 'worktrees' / 'linked').resolve()}\n"
            )
            common = primary / ".git" / "worktrees" / "linked" / "commondir"
            common.write_text("../..\n")
            self.assertEqual(repositories._canonical_repo(linked), primary.resolve())

    def test_shallow_discovery_does_not_descend_into_children(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            shallow = home / "Projects" / "widget"
            nested = home / "Projects" / "container" / "nested-widget"
            (shallow / ".git").mkdir(parents=True)
            (nested / ".git").mkdir(parents=True)
            with patch.object(repositories, "HOME", home):
                found = repositories._shallow_discover()
            self.assertIn(shallow.resolve(), found)
            self.assertNotIn(nested.resolve(), found)

    def test_background_scan_stops_at_a_repo_and_skips_dependency_trees(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            repo = home / "elsewhere" / "widget"
            child_repo = repo / "nested" / "nested-widget"
            ignored = home / "node_modules" / "ignored-widget"
            (repo / ".git").mkdir(parents=True)
            (child_repo / ".git").mkdir(parents=True)
            (ignored / ".git").mkdir(parents=True)
            with patch.object(repositories, "HOME", home):
                found = repositories._scan_home()
            self.assertIn(repo.resolve(), found)
            self.assertNotIn(child_repo.resolve(), found)
            self.assertNotIn(ignored.resolve(), found)


if __name__ == "__main__":
    unittest.main()
