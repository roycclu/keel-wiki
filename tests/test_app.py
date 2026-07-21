from streamlit.testing.v1 import AppTest


def test_app_starts_without_errors(monkeypatch) -> None:
    """Verify that Streamlit can render the initial application screen."""
    monkeypatch.setenv("PYWIKIBOT_NO_USER_CONFIG", "2")

    app = AppTest.from_file("app.py")
    app.run(timeout=10)

    assert not app.exception
