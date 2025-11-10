def pytest_addoption(parser):
    parser.addoption(
        "--update-anti",
        action="store_true",
        help="Авто-обновлять tests/sannysoft/browser_antibot_sannysoft.json по результатам.",
    )
