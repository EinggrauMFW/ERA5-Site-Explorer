"""The request form talks to the server in the format the server requires.

Batch A made POST /api/jobs strict: latitude, longitude, buffer and time_step must be JSON numbers (a number sent as
text is a 400). The page sent the raw text of its inputs, so every submit was refused with "latitude must be a
number". No test submitted the form through the page, so nothing caught it. This does.
"""

import pytest

from tests.frontend.conftest import open_app

NUMBER_FIELDS = ("latitude", "longitude", "buffer", "time_step")


def submit_and_capture(page):
    with page.expect_request(lambda request: request.method == "POST" and request.url.endswith("/api/jobs")) as sent:
        page.click("#submit")
    return sent.value.post_data_json


def assert_numbers(body):
    for field in NUMBER_FIELDS:
        assert isinstance(body[field], (int, float)) and not isinstance(body[field], bool), f"{field} was sent as {body[field]!r}"


def test_submitting_the_request_form_sends_numbers_and_starts_a_job(page, live):
    open_app(page, live)
    page.evaluate("setCoordinates(99.36764, -2.05368)")             # what a click on the map does
    body = submit_and_capture(page)
    assert_numbers(body)
    assert body["latitude"] == -2.05368 and body["longitude"] == 99.36764
    page.wait_for_function("!document.querySelector('#status-card').hidden")      # the job was created and opened
    assert page.locator("#form-error").is_hidden()
    assert page.errors == []


def test_a_preview_request_is_accepted_too(page, live):
    open_app(page, live)
    page.check("#dry-run")
    body = submit_and_capture(page)
    assert_numbers(body)
    assert body["dry_run"] is True
    page.wait_for_function("!document.querySelector('#status-card').hidden")
    assert page.locator("#form-error").is_hidden()


@pytest.mark.parametrize("product", ["single-levels", "wave-spectra", "mars-surface"])
def test_every_data_product_sends_numbers(page, live, product):
    open_app(page, live)
    page.evaluate("p => { const s = document.querySelector('#product'); s.value = p; s.dispatchEvent(new Event('change')); }", product)
    if product == "mars-surface":
        page.locator("input[name='params']").first.check()
    body = submit_and_capture(page)
    assert_numbers(body)
    assert body["product"] == product
    page.wait_for_function("!document.querySelector('#status-card').hidden")
    assert page.locator("#form-error").is_hidden()
