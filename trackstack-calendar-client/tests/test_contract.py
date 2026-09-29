"""Runs this package's implementation against the shared behavioral
contract in CALENDAR_CONTRACT_FIXTURE.json at the repo root -- the
same fixture trackstack-ui's Node "/calendar-client" subpath's tests
are run against, so the two implementations can't silently drift."""
import json
import os

import pytest
import requests
import responses

from trackstack_calendar_client import create_calendar_pusher

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "..", "CALENDAR_CONTRACT_FIXTURE.json")
with open(FIXTURE_PATH) as f:
    FIXTURE = json.load(f)


def _pusher():
    return create_calendar_pusher(FIXTURE["calendarBaseUrl"], tracker=FIXTURE["tracker"])


@pytest.mark.asyncio
@responses.activate
async def test_posts_exact_contract_request_body_and_url_for_plain_push():
    responses.add(responses.POST, FIXTURE["expectedUrl"], status=FIXTURE["successResponseStatus"])

    push = _pusher()
    result = await push(FIXTURE["authHeader"], **FIXTURE["pushInput"])

    assert result is True
    assert len(responses.calls) == 1
    sent = json.loads(responses.calls[0].request.body)
    assert sent == FIXTURE["expectedRequestBody"]
    assert responses.calls[0].request.headers["Authorization"] == FIXTURE["expectedAuthorizationHeader"]


@pytest.mark.asyncio
@responses.activate
async def test_passes_kind_through_verbatim_when_provided():
    responses.add(responses.POST, FIXTURE["expectedUrl"], status=FIXTURE["successResponseStatus"])

    push = _pusher()
    await push(FIXTURE["authHeader"], **FIXTURE["pushInputWithKind"])

    sent = json.loads(responses.calls[0].request.body)
    assert sent == FIXTURE["expectedRequestBodyWithKind"]


@pytest.mark.asyncio
@responses.activate
async def test_non_2xx_response_resolves_false_not_an_exception():
    responses.add(responses.POST, FIXTURE["expectedUrl"], status=FIXTURE["notFoundResponseStatus"])

    push = _pusher()
    result = await push(FIXTURE["authHeader"], **FIXTURE["pushInput"])
    assert result is False


@pytest.mark.asyncio
@responses.activate
async def test_network_failure_resolves_false_not_an_exception():
    responses.add(
        responses.POST,
        FIXTURE["expectedUrl"],
        body=requests.exceptions.ConnectionError("connection refused"),
    )

    push = _pusher()
    result = await push(FIXTURE["authHeader"], **FIXTURE["pushInput"])
    assert result is False
