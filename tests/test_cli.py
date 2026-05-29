"""CLI argument parsing + a stdlib-only command."""
from fbbench.cli.commands import cmd_list
from fbbench.cli.main import build_parser


def test_parser_grade_args():
    args = build_parser().parse_args(["grade", "mybug", "blob.bin", "--rounds", "5"])
    assert args.cmd == "grade"
    assert args.bug_id == "mybug"
    assert args.blob == "blob.bin"
    assert args.rounds == 5


def test_cmd_list_runs(capsys):
    assert cmd_list(None) == 0
    assert "bugs available" in capsys.readouterr().out
