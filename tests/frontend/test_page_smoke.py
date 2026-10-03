"""The page loads, opens a finished job and renders its analysis without uncaught JavaScript errors."""

from tests.frontend.conftest import JOB_A, NODE_DEFAULT, analysis_node, open_job


def test_a_finished_job_opens_and_renders_its_analysis(page, live):
    open_job(page, live, JOB_A)
    assert analysis_node(page) == list(NODE_DEFAULT)
    assert page.locator("#metrics .metric").count() >= 3
    assert page.locator("#analysis-tabs .tab").count() >= 5
    assert page.errors == []
