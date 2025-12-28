from __future__ import annotations

from rep_grow.requests import ExplorerRequest


def test_explorer_request_preserves_string_ratings():
    request = ExplorerRequest(fen="start", ratings="1000,1500")

    assert request.ratings_str() == "1000,1500"
    params = request.params()
    assert params["ratings"] == "1000,1500"
    assert params["fen"] == "start"
