import argparse

from hermes_cli.projects_cmd import build_parser


def test_projects_is_an_alias_of_project():
    root = argparse.ArgumentParser()
    build_parser(root.add_subparsers(dest="command"))

    args = root.parse_args(["projects", "list"])

    assert args.project_action == "list"
